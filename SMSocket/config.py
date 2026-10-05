"""Config loading & validation for SMSocket."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from .billing import Billing, from_raw as billing_from_raw
from pathlib import Path

LEGACY_ENV = {
    "SMSSOCKET_CONFIG": "LLMROUTER_CONFIG",
    "SMSSOCKET_NO_KEY": "LLMROUTER_NO_KEY",
    "SMSSOCKET_MASTER_KEY": "LLMROUTER_MASTER_KEY",
    "SMSSOCKET_KEY_FILE": "LLMROUTER_KEY_FILE",
}


def envv(name: str, default=None):
    """Read SMSSOCKET_* env, falling back to the pre-rename LLMROUTER_* name."""
    v = os.environ.get(name)
    if v is None:
        v = os.environ.get(LEGACY_ENV.get(name, ""), None)
    return default if v is None else v


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
class ClashConfig:
    """`clash:` block - the optional net plane (see clash.py). Disabled by default.

    Only `controller`/`mixed_port` are needed to start; everything else has a
    safe default. Secrets belong in the environment (`secret: ${CLASH_SECRET}`).
    """

    enabled: bool = False
    controller: str = "http://127.0.0.1:9090"
    secret: str = ""
    mixed_port: int = 7890
    proxy_url: str = ""                      # overrides http://host:mixed_port
    health_url: str = "https://www.google.com/generate_204"
    timeout: float = 5.0
    interval: float = 60.0
    mode: str = "auto"                       # auto | direct | proxy
    groups: list[str] = field(default_factory=list)
    provider_proxy: dict[str, str] = field(default_factory=dict)
    smart: bool = True
    min_delay_ms: float = 0.0
    fail_ratio: float = 0.5

    def proxy_url_for(self) -> str:
        """The http proxy endpoint clash listens on ("" = no proxy path)."""
        if self.proxy_url:
            return self.proxy_url.strip()
        if self.mixed_port:
            host = self.controller.split("//")[-1].split("/")[0]
            host = host.split(":")[0] or "127.0.0.1"
            return f"http://{host}:{int(self.mixed_port)}"
        return ""

    def as_config(self) -> dict:
        return {"enabled": self.enabled, "controller": self.controller,
                "secret": "***" if self.secret else "",
                "mixed_port": self.mixed_port, "proxy_url": self.proxy_url,
                "health_url": self.health_url, "timeout": self.timeout,
                "interval": self.interval, "mode": self.mode,
                "groups": list(self.groups), "provider_proxy": dict(self.provider_proxy),
                "smart": self.smart, "min_delay_ms": self.min_delay_ms,
                "fail_ratio": self.fail_ratio}


def clash_from_raw(raw, default: ClashConfig | None = None) -> ClashConfig:
    """Parse + validate the `clash:` block; a missing block keeps the default."""
    base = default or ClashConfig()
    if not isinstance(raw, dict):
        return base
    modes = ("auto", "direct", "proxy")
    groups = raw.get("groups") or []
    if isinstance(groups, str):
        groups = [groups]
    hints = raw.get("provider_proxy") or {}
    if not isinstance(hints, dict):
        raise ValueError("clash.provider_proxy must be a mapping")
    mode = str(raw.get("mode", base.mode) or "auto").strip().lower()
    if mode not in modes:
        raise ValueError(f"clash.mode must be one of {modes}")
    out = ClashConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        controller=str(_env(raw.get("controller", base.controller)) or "").strip(),
        secret=str(_env(raw.get("secret", base.secret)) or "").strip(),
        mixed_port=int(raw.get("mixed_port", base.mixed_port) or 0),
        proxy_url=str(_env(raw.get("proxy_url", base.proxy_url)) or "").strip(),
        health_url=str(raw.get("health_url", base.health_url)),
        timeout=float(raw.get("timeout", base.timeout) or 5.0),
        interval=float(raw.get("interval", base.interval) or 60.0),
        mode=mode,
        groups=[str(g).strip() for g in _env(list(groups)) if str(g).strip()],
        provider_proxy={str(k).strip(): str(v).strip()
                        for k, v in _env(dict(hints)).items()
                        if str(k).strip() and str(v).strip()},
        smart=bool(raw.get("smart", base.smart)),
        min_delay_ms=float(raw.get("min_delay_ms", base.min_delay_ms) or 0.0),
        fail_ratio=float(raw.get("fail_ratio", base.fail_ratio) or 0.5))
    if out.timeout <= 0:
        raise ValueError("clash.timeout must be > 0")
    if out.interval < 1:
        raise ValueError("clash.interval must be >= 1 second")
    if not 0 <= out.fail_ratio <= 1:
        raise ValueError("clash.fail_ratio must be between 0 and 1")
    if out.enabled and not out.controller.startswith("http"):
        raise ValueError("clash.controller must be an http:// url when enabled")
    return out



PARK_REASONS = ("saturated", "rpm", "provider_saturated")
STACK_POLICIES = ("lifo", "fifo", "priority")


@dataclass
class StackConfig:
    """`stack:` block - park requests when a limit bites (see stacksched.py).

    Off by default: with `enabled: false` the gate behaves exactly as before
    (saturated caller -> immediate 429). `wait` is how long one caller accepts
    being parked, `park_on` picks which limits may park it at all.
    """

    enabled: bool = False
    max_depth: int = 1000                  # parked callers at once, then 429
    wait: float = 120.0                    # seconds a caller tolerates parking
    policy: str = "lifo"                   # lifo | fifo | priority
    park_on: list[str] = field(
        default_factory=lambda: ["saturated", "rpm", "provider_saturated"])
    interval: float = 0.25                 # drain poll (seconds)
    repark: int = 3                        # batch retries after a 429

    def parks_on(self, reason: str) -> bool:
        return self.enabled and reason in self.park_on

    def as_config(self) -> dict:
        return {"enabled": self.enabled, "max_depth": self.max_depth,
                "wait": self.wait, "policy": self.policy,
                "park_on": list(self.park_on), "interval": self.interval,
                "repark": self.repark}


def stack_from_raw(raw, default: StackConfig | None = None) -> StackConfig:
    """Parse + validate the `stack:` block; a missing block keeps the default."""
    base = default or StackConfig()
    if not isinstance(raw, dict):
        return base
    policy = str(raw.get("policy", base.policy) or "lifo").strip().lower()
    if policy not in STACK_POLICIES:
        raise ValueError(f"stack.policy must be one of {STACK_POLICIES}")
    reasons = raw.get("park_on", base.park_on) or []
    if isinstance(reasons, str):
        reasons = [reasons]
    reasons = [str(r).strip().lower() for r in _env(list(reasons)) if str(r).strip()]
    bad = [r for r in reasons if r not in PARK_REASONS]
    if bad:
        raise ValueError(f"stack.park_on must be a subset of {PARK_REASONS} (got {bad})")
    out = StackConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        max_depth=int(raw.get("max_depth", base.max_depth) or base.max_depth),
        wait=float(raw.get("wait", base.wait) or base.wait),
        policy=policy,
        park_on=reasons or list(PARK_REASONS),
        interval=float(raw.get("interval", base.interval) or base.interval),
        repark=int(raw.get("repark", base.repark) or 0))
    if out.max_depth < 1:
        raise ValueError("stack.max_depth must be >= 1")
    if out.wait <= 0:
        raise ValueError("stack.wait must be > 0 seconds")
    if out.interval < 0.01:
        raise ValueError("stack.interval must be >= 0.01 second")
    if out.repark < 0:
        raise ValueError("stack.reparks must be 0 or more")
    return out


ASSESS_EGRESS = ("auto", "direct")
ASSESS_VERDICTS = ("healthy", "slow", "blocked", "unstable", "unknown")


@dataclass
class AssessConfig:
    """`assess:` block - how we measure model performance and reachability.

    Two sources feed one table (see assess.py): `live` rows harvested from real
    traffic (free, no extra calls) and `probe` rows from a scheduled sweep that
    actually calls the model. `egress: auto` sweeps direct plus every clash
    path, which is what makes "is this model reachable from this network
    environment?" a question we can answer from data instead of folklore.
    """

    enabled: bool = False
    interval_s: int = 3600                 # hours between sweeps
    at: str = ""                           # "HH:MM" daily slot ("" = interval only)
    models: list[str] = field(default_factory=list)      # [] = every alias
    providers: list[str] = field(default_factory=list)   # [] = every provider
    egress: str = "auto"                   # auto | direct | <egress id>
    prompt: str = "Reply with exactly one word: ok"
    max_tokens: int = 8
    concurrency: int = 4
    timeout: float = 20.0
    live: bool = True                      # harvest from real traffic
    live_sample: float = 1.0               # 0..1 share of live calls recorded
    slow_ms: float = 8000.0                # p50 above this = "slow"
    window_s: int = 86400                  # report window

    def as_dict(self) -> dict:
        return {"interval_s": self.interval_s, "max_tokens": self.max_tokens,
                "concurrency": self.concurrency, "timeout": self.timeout,
                "live_sample": self.live_sample, "slow_ms": self.slow_ms,
                "window_s": self.window_s}

    def as_config(self) -> dict:
        return {"enabled": self.enabled, "interval_s": self.interval_s,
                "at": self.at, "models": list(self.models),
                "providers": list(self.providers), "egress": self.egress,
                "max_tokens": self.max_tokens, "concurrency": self.concurrency,
                "timeout": self.timeout, "live": self.live,
                "live_sample": self.live_sample, "slow_ms": self.slow_ms,
                "window_s": self.window_s}


def assess_from_raw(raw, default: AssessConfig | None = None) -> AssessConfig:
    """Parse + validate the `assess:` block; a missing block keeps the default."""
    base = default or AssessConfig()
    if not isinstance(raw, dict):
        return base
    at = str(raw.get("at", base.at) or "").strip()
    if at:
        parts = at.split(":")
        if len(parts) != 2 or not all(p.strip().isdigit() for p in parts) \
                or not (0 <= int(parts[0]) < 24 and 0 <= int(parts[1]) < 60):
            raise ValueError("assess.at must be HH:MM (24h) or empty")
        at = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    models = raw.get("models", base.models) or []
    if isinstance(models, str):
        models = [models]
    provs = raw.get("providers", base.providers) or []
    if isinstance(provs, str):
        provs = [provs]
    def num(key, cast):
        """Read one number as configured: absent/null = default, 0 = 0."""
        v = raw.get(key, None)
        return base.as_dict()[key] if v is None else cast(v)

    out = AssessConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        interval_s=int(num("interval_s", int)),
        at=at,
        models=[str(m).strip() for m in _env(list(models)) if str(m).strip()],
        providers=[str(p).strip() for p in _env(list(provs)) if str(p).strip()],
        egress=str(raw.get("egress", None) or base.egress).strip(),
        prompt=str(raw.get("prompt", None) or base.prompt),
        max_tokens=int(num("max_tokens", int)),
        concurrency=int(num("concurrency", int)),
        timeout=float(num("timeout", float)),
        live=bool(raw.get("live", base.live)),
        live_sample=float(num("live_sample", float)),
        slow_ms=float(num("slow_ms", float)),
        window_s=int(num("window_s", int)))
    if out.interval_s < 1:
        raise ValueError("assess.interval_s must be >= 1 second")
    if out.concurrency < 1 or out.concurrency > 64:
        raise ValueError("assess.concurrency must be 1..64")
    if out.timeout <= 0:
        raise ValueError("assess.timeout must be > 0 seconds")
    if out.max_tokens < 1:
        raise ValueError("assess.max_tokens must be >= 1")
    if not 0.0 <= out.live_sample <= 1.0:
        raise ValueError("assess.live_sample must be 0..1")
    if out.window_s < 1:
        raise ValueError("assess.window_s must be >= 1 second")
    return out


@dataclass
class Settings:
    listen: str = "127.0.0.1:8000"
    master_keys: list[str] = field(default_factory=list)
    strategy: str = "priority"     # priority | round_robin | weighted
    retry: int = 2                 # extra attempts after first failure
    cooldown: float = 60.0         # seconds a dead key/provider is skipped
    db_path: str = "usage.sqlite3"
    log_level: str = "INFO"
    max_concurrency: int = 0       # 0 = unlimited in-flight upstream calls
    queue_wait: float = 30.0       # seconds a caller may wait for a free slot
    per_provider_concurrency: int = 0   # 0 = no per-provider cap
    currency: str = "USD"          # display currency (mirrors billing.currency)
    billing: Billing = field(default_factory=Billing)
    clash: ClashConfig = field(default_factory=ClashConfig)
    stack: StackConfig = field(default_factory=StackConfig)
    assess: AssessConfig = field(default_factory=AssessConfig)
    pricing: dict[str, dict] = field(default_factory=dict)   # alias -> {prompt, completion} per 1M tokens
    providers: list[ProviderSpec] = field(default_factory=list)

    def embed_index(self) -> dict[str, list[ProviderSpec]]:
        idx: dict[str, list[ProviderSpec]] = {}
        for p in self.providers:
            if p.enabled and p.style == "openai":
                for alias in p.embeddings:
                    idx.setdefault(alias, []).append(p)
        return idx
    def pricing_row(self, alias: str) -> dict:
        """Per-1M-token price row for an alias ('*' = default row)."""
        return self.pricing.get(alias) or self.pricing.get("*") or {}

    def cost_of(self, alias: str, prompt: int, completion: int) -> float:
        """Estimated cost in the pricing row's own currency."""
        return float(self.cost_detail(alias, prompt, completion)["amount"])

    def cost_detail(self, alias: str, prompt: int, completion: int) -> dict:
        """Cost of a call: amount in pricing currency + converted display amount.

        {"amount", "currency", "display", "display_currency", "rate"} - `display`
        is always in settings.billing.currency, so mixed-currency providers
        add up correctly.
        """
        return self.billing.cost(prompt, completion, self.pricing_row(alias))
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
    """Load settings from YAML/JSON file (default: ./config.yaml, env SMSSOCKET_CONFIG;
    the pre-rename LLMROUTER_CONFIG is still honoured)."""
    path = Path(path or envv("SMSSOCKET_CONFIG") or "config.yaml")
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
    settings = Settings(
        listen=raw.get("listen", Settings.listen),
        master_keys=[k for k in _env(raw.get("master_keys") or []) if k],
        strategy=raw.get("strategy", Settings.strategy),
        retry=int(raw.get("retry", Settings.retry)),
        cooldown=float(raw.get("cooldown", Settings.cooldown)),
        db_path=raw.get("db_path", Settings.db_path),
        log_level=raw.get("log_level", Settings.log_level),
        max_concurrency=int(raw.get("max_concurrency", 0) or 0),
        queue_wait=float(raw.get("queue_wait", 30.0) or 0),
        per_provider_concurrency=int(raw.get("per_provider_concurrency", 0) or 0),
        currency=raw.get("currency", Settings.currency),
        billing=billing_from_raw(raw.get("billing"), raw.get("currency", "USD")),
        clash=clash_from_raw(raw.get("clash")),
        stack=stack_from_raw(raw.get("stack")),
        assess=assess_from_raw(raw.get("assess")),
        pricing={k: dict(v or {}) for k, v in (raw.get("pricing") or {}).items()},
        providers=provs)
    from .keys import ensure_master_key
    settings.key_source = ensure_master_key(settings, path)
    return settings
