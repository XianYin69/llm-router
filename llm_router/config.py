"""Config loading & validation for llm-router."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _env(v):
    """Expand ${VAR} / $VAR from the environment (keeps secrets out of the file)."""
    if isinstance(v, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1) or m.group(2), ""), v)
    if isinstance(v, list):
        return [_env(x) for x in v]
    if isinstance(v, dict):
        return {k: _env(x) for k, x in v.items()}
    return v

try:
    import yaml
except Exception:  # yaml optional
    yaml = None


@dataclass
class ProviderSpec:
    name: str
    base_url: str
    keys: list[str] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=dict)  # public alias -> upstream id
    style: str = "openai"          # openai | anthropic
    weight: int = 1
    priority: int = 0
    timeout: float = 120.0
    max_rpm: int = 0
    enabled: bool = True
    extra_headers: dict[str, str] = field(default_factory=dict)
    embeddings: dict[str, str] = field(default_factory=dict)  # alias -> upstream embed model

    def aliases(self) -> list[str]:
        return list(self.models) or [self.name]


@dataclass
class Settings:
    listen: str = "127.0.0.1:8000"
    master_keys: list[str] = field(default_factory=list)
    strategy: str = "priority"     # priority | round_robin | weighted
    retry: int = 2                 # extra attempts after first failure
    cooldown: float = 60.0         # seconds a dead key/provider is skipped
    db_path: str = "usage.sqlite3"
    log_level: str = "INFO"
    currency: str = "USD"
    pricing: dict[str, dict] = field(default_factory=dict)   # alias -> {prompt, completion} per 1M tokens
    providers: list[ProviderSpec] = field(default_factory=list)

    def embed_index(self) -> dict[str, list[ProviderSpec]]:
        idx: dict[str, list[ProviderSpec]] = {}
        for p in self.providers:
            if p.enabled and p.style == "openai":
                for alias in p.embeddings:
                    idx.setdefault(alias, []).append(p)
        return idx
    def cost_of(self, alias: str, prompt: int, completion: int) -> float:
        """Estimated cost in `currency` using per-1M-token prices ('*' = default row)."""
        row = self.pricing.get(alias) or self.pricing.get("*") or {}
        if not row:
            return 0.0
        return round((prompt * float(row.get("prompt", 0) or 0)
                      + completion * float(row.get("completion", 0) or 0)) / 1e6, 6)
    def model_index(self) -> dict[str, list[ProviderSpec]]:
        idx: dict[str, list[ProviderSpec]] = {}
        for p in self.providers:
            if not p.enabled:
                continue
            for alias in p.aliases():
                idx.setdefault(alias, []).append(p)
        return idx

_TOP = {"listen", "master_keys", "strategy", "retry", "cooldown", "db_path", "log_level"}


def load_config(path: str | os.PathLike | None = None) -> Settings:
    """Load settings from YAML/JSON file (default: ./config.yaml, env LLMROUTER_CONFIG)."""
    path = Path(path or os.environ.get("LLMROUTER_CONFIG") or "config.yaml")
    raw: dict = {}
    if path.exists():
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text) if path.suffix == ".json" else (
            yaml.safe_load(text) if yaml else json.loads(text))
    provs = []
    for item in raw.get("providers", []):
        keys = item.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        provs.append(ProviderSpec(
            name=item["name"], base_url=_env(item["base_url"]).rstrip("/"),
            keys=[k for k in _env(keys) if k],
            models=dict(_env(item.get("models") or {})),
            style=item.get("style", "openai"), weight=int(item.get("weight", 1)),
            priority=int(item.get("priority", 0)), timeout=float(item.get("timeout", 120.0)),
            max_rpm=int(item.get("max_rpm", 0)), enabled=bool(item.get("enabled", True)),
            extra_headers=dict(_env(item.get("extra_headers") or {})),
            embeddings=dict(_env(item.get("embeddings") or {}))))
    return Settings(
        listen=raw.get("listen", Settings.listen),
        master_keys=[k for k in _env(raw.get("master_keys") or []) if k],
        strategy=raw.get("strategy", Settings.strategy),
        retry=int(raw.get("retry", Settings.retry)),
        cooldown=float(raw.get("cooldown", Settings.cooldown)),
        db_path=raw.get("db_path", Settings.db_path),
        log_level=raw.get("log_level", Settings.log_level),
        currency=raw.get("currency", Settings.currency),
        pricing={k: dict(v or {}) for k, v in (raw.get("pricing") or {}).items()},
        providers=provs)
