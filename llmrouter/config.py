"""Config loading for llm-router (v1): YAML/JSON with ${ENV} expansion."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def _expand(val: Any) -> Any:
    if isinstance(val, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), val)
    if isinstance(val, list):
        return [_expand(v) for v in val]
    if isinstance(val, dict):
        return {k: _expand(v) for k, v in val.items()}
    return val


@dataclass
class Provider:
    name: str
    base_url: str
    api_keys: list[str]
    models: list[str] = field(default_factory=list)
    weight: int = 1
    timeout: float = 120.0
    headers: dict = field(default_factory=dict)


@dataclass
class Settings:
    providers: dict[str, Provider]
    routes: dict[str, list[str]]          # public model -> ["provider/model", ...]
    master_keys: list[str]
    max_retries: int = 3
    cooldown: float = 30.0
    anon_allowed: bool = False
    raw: dict = field(default_factory=dict)


def _parse(text: str) -> dict:
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except Exception:
        return json.loads(text)


def load(path: str | os.PathLike | None = None) -> Settings:
    p = Path(path or os.environ.get("LLMROUTER_CONFIG", "config.yaml"))
    data = _expand(_parse(p.read_text(encoding="utf-8")))
    providers: dict[str, Provider] = {}
    for name, cfg in (data.get("providers") or {}).items():
        keys = cfg.get("api_keys") or cfg.get("api_key") or []
        if isinstance(keys, str):
            keys = [keys]
        providers[name] = Provider(
            name=name,
            base_url=str(cfg["base_url"]).rstrip("/"),
            api_keys=[k for k in keys if k],
            models=list(cfg.get("models") or []),
            weight=int(cfg.get("weight", 1)),
            timeout=float(cfg.get("timeout", 120)),
            headers=cfg.get("headers") or {})
    routes = {k: (v if isinstance(v, list) else [v])
              for k, v in (data.get("routes") or {}).items()}
    for pv in providers.values():                       # implicit routes
        for m in pv.models:
            routes.setdefault(m, [f"{pv.name}/{m}"])
    mk = data.get("master_keys") or []
    if isinstance(mk, str):
        mk = [mk]
    return Settings(providers=providers, routes=routes,
                    master_keys=[m for m in mk if m],
                    max_retries=int(data.get("max_retries", 3)),
                    cooldown=float(data.get("cooldown", 30)),
                    anon_allowed=bool(data.get("anon_allowed", False)), raw=data)
