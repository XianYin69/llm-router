"""Master key resolution: config > env > auto-generated random key (persisted).

The gateway must always answer "what key do I use?" in the startup banner, so a
missing master_keys section is not an error - we mint a random key once and keep
it in a file next to the config, reused on every restart / --reload subprocess.
Set LLMROUTER_NO_KEY=1 (run.py --no-key) to opt out and run unauthenticated.
"""
from __future__ import annotations
import os
import secrets
from pathlib import Path

KEY_FILE = "router.key"
PREFIX = "sk-router-"
ENV_NO_KEY = "LLMROUTER_NO_KEY"
ENV_KEY = "LLMROUTER_MASTER_KEY"
ENV_KEY_FILE = "LLMROUTER_KEY_FILE"


def new_key() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def key_path(config_path: os.PathLike | str | None = None) -> Path:
    """Where the generated key lives: env override, else beside the config file."""
    env = os.environ.get(ENV_KEY_FILE)
    if env:
        return Path(env)
    base = Path(config_path) if config_path else Path.cwd()
    if base.is_file():
        base = base.parent
    return base / KEY_FILE


def ensure_master_key(settings, config_path=None, rotate: bool = False) -> str:
    """Fill settings.master_keys if empty; return source label.

    'config'      - master_keys came from config.yaml (unchanged)
    'env'         - taken from LLMROUTER_MASTER_KEY
    'generated'   - random key minted now and written to router.key
    'reused'      - read back from router.key (restart / reload keeps one key)
    'disabled'    - no key, gateway open (LLMROUTER_NO_KEY / --no-key)
    """
    if getattr(settings, "master_keys", None):
        return "config"
    if os.environ.get(ENV_NO_KEY):
        return "disabled"
    env_key = os.environ.get(ENV_KEY)
    if env_key:
        settings.master_keys = [env_key]
        return "env"
    path = key_path(config_path)
    if not rotate:
        try:
            old = path.read_text(encoding="utf-8").strip()
        except OSError:
            old = ""
        if old:
            settings.master_keys = [old]
            return "reused"
    key = new_key()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key + "\n", encoding="utf-8")
    except OSError:
        pass  # read-only media: the key still works for this process
    settings.master_keys = [key]
    return "generated"
