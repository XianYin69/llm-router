"""Admin API: live read/write of gateway config for the dashboard pages.

Used by the "提供商与API" page (providers / custom APIs / keys) and the
"总设置" page (global tuning, pricing, client self-info):

  GET    /admin/config                     live config snapshot (keys masked)
  PUT    /admin/config                     patch globals / pricing / providers
  POST   /admin/providers                  add provider (custom OpenAI-compatible too)
  PATCH  /admin/providers/{name}           edit one provider
  DELETE /admin/providers/{name}           remove one provider (refuses the last)
  POST   /admin/providers/{name}/keys      append an api key
  DELETE /admin/providers/{name}/keys/{idx} drop an api key
  GET    /admin/self?reveal=1              base_url + master key for client setup
  GET    /admin/export                     raw config text (secrets masked)

Safety: writes go to a temp file, are parsed by load_config() before swapping,
the previous file is kept as <name>.bak, and live state is only re-based after
a successful parse. Secrets never leave the server unmasked except through
/admin/self?reveal=1, which is itself key-authenticated.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from . import billing_routes, discover_routes
from .billing_routes import billing_map
from .concurrency import Gate
from .config import envv, load_config
from .keys import key_path
from .providers import Pool, mask
from .router import Router

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,48}$")
GLOBALS = ("listen", "strategy", "retry", "cooldown", "db_path", "log_level",
           "currency", "max_concurrency", "queue_wait", "per_provider_concurrency")
P_FIELDS = ("base_url", "style", "weight", "priority", "timeout", "max_rpm",
            "enabled", "models", "embeddings", "extra_headers")


def cfg_path() -> Path:
    return Path(envv("SMSSOCKET_CONFIG", "config.yaml"))


def read_raw(path: Path) -> dict:
    if not path.exists():
        raise HTTPException(409, detail={"error": {"message": f"config not found: {path}",
                                                   "type": "invalid_request_error"}})
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text) if path.suffix == ".json" else _yaml().safe_load(text)
    except Exception as exc:
        raise HTTPException(409, detail={"error": {"message": f"config unreadable: {exc}",
                                                   "type": "invalid_request_error"}})
    return data or {}


def _yaml():
    import yaml
    return yaml


def write_raw(path: Path, data: dict) -> None:
    """Validate-then-swap: temp file -> load_config() -> .bak -> replace."""
    tmp = path.with_name(path.name + ".tmp")
    if path.suffix == ".json":
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        tmp.write_text(_yaml().safe_dump(data, allow_unicode=True, sort_keys=False),
                       encoding="utf-8")
    try:
        load_config(tmp)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, detail={"error": {"message": f"config rejected: {exc}",
                                                   "type": "invalid_request_error"}})
    if path.exists():
        try:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass
    tmp.replace(path)


def prov_dict(p) -> dict:
    """Provider view for the UI: keys masked, never raw."""
    return {"name": p.name, "base_url": p.base_url, "style": p.style,
            "weight": p.weight, "priority": p.priority, "timeout": p.timeout,
            "max_rpm": p.max_rpm, "enabled": p.enabled,
            "models": dict(p.models), "embeddings": dict(p.embeddings),
            "extra_headers": dict(p.extra_headers),
            "keys": [mask(k) for k in p.keys], "key_count": len(p.keys)}


def _str_map(val, field) -> dict:
    if val in (None, ""):
        return {}
    if not isinstance(val, dict):
        raise HTTPException(400, detail={"error": {"message": f"{field} must be an object",
                                                   "type": "invalid_request_error"}})
    return {str(k).strip(): str(v).strip() for k, v in val.items()
            if str(k).strip() and str(v).strip()}


def clean_provider(item: dict, old=None) -> dict:
    """UI payload -> config file dict. `keys` are managed only by the key routes,
    so an edit can never accidentally wipe or echo back a secret."""
    if not isinstance(item, dict):
        raise HTTPException(400, detail={"error": {"message": "provider must be an object",
                                                   "type": "invalid_request_error"}})
    name = str(item.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise HTTPException(400, detail={"error": {"message": "bad provider name",
                                                   "type": "invalid_request_error"}})
    base = str(item.get("base_url") or "").strip().rstrip("/")
    if not base.startswith("http"):
        raise HTTPException(400, detail={"error": {"message": "base_url must start with http(s)",
                                                   "type": "invalid_request_error"}})
    style = str(item.get("style") or "openai").strip().lower()
    if style not in ("openai", "anthropic"):
        raise HTTPException(400, detail={"error": {"message": "style is openai or anthropic",
                                                   "type": "invalid_request_error"}})
    prev = old or {}
    out = {"name": name, "base_url": base, "style": style,
           "keys": list(prev.get("keys") or []),
           "models": _str_map(item.get("models"), "models") or dict(prev.get("models") or {}),
           "embeddings": _str_map(item.get("embeddings"), "embeddings"),
           "extra_headers": _str_map(item.get("extra_headers"), "extra_headers"),
           "weight": int(item.get("weight", prev.get("weight", 1))),
           "priority": int(item.get("priority", prev.get("priority", 0))),
           "timeout": float(item.get("timeout", prev.get("timeout", 120.0))),
           "max_rpm": int(item.get("max_rpm", prev.get("max_rpm", 0))),
           "enabled": bool(item.get("enabled", prev.get("enabled", True)))}
    return {k: v for k, v in out.items() if v not in ({}, [], "")}


def apply_state(st) -> dict:
    """Re-read the file and hot-swap live state (never on a broken/empty config)."""
    path = cfg_path()
    try:
        new = load_config(path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, detail={"error": {"message": f"config rejected: {exc}",
                                                   "type": "invalid_request_error"}})
    if not new.providers:
        raise HTTPException(409, detail={"error": {"message": "config has no providers",
                                                   "type": "invalid_request_error"}})
    if not new.master_keys:
        new.master_keys = list(st.settings.master_keys or [])
    st.settings = new
    st.pool = st.pool.rebase(new.providers) if st.pool else Pool(new.providers)
    st.gate = Gate(getattr(new, "max_concurrency", 0), getattr(new, "queue_wait", 30.0),
                   getattr(new, "per_provider_concurrency", 0), st.meter)
    st.router = Router(st.settings, st.pool, st.usage, st.http, st.gate)
    return {"providers": len(new.providers), "keys": len(st.pool.slots),
            "models": len(new.model_index())}


def find(provs: list, name: str) -> dict:
    for item in provs:
        if isinstance(item, dict) and str(item.get("name")) == name:
            return item
    raise HTTPException(404, detail={"error": {"message": f"unknown provider: {name}",
                                               "type": "invalid_request_error"}})


def pricing_map(val) -> dict:
    if val in (None, ""):
        return {}
    if not isinstance(val, dict):
        raise HTTPException(400, detail={"error": {"message": "pricing must be an object",
                                                   "type": "invalid_request_error"}})
    out = {}
    for alias, row in val.items():
        if isinstance(row, dict):
            item = {k: float(row[k]) for k in ("prompt", "completion",
                                                "cache_read", "request")
                         if row.get(k) not in (None, "")}
            if row.get("currency"):
                item["currency"] = str(row["currency"]).strip().upper()
            out[str(alias)] = item
    return out


def build(st, dep) -> APIRouter:
    """Admin routes for the dashboard; `dep` = master-key dependency list."""
    r = APIRouter(dependencies=dep)

    def snapshot() -> dict:
        s = st.settings
        path = cfg_path()
        return {"path": str(path), "exists": path.exists(),
                **{k: getattr(s, k) for k in GLOBALS},
                "billing": s.billing.as_config(),
                "pricing": s.pricing,
                "providers": [prov_dict(p) for p in s.providers],
                "models": sorted(s.model_index()),
                "embeddings": sorted(s.embed_index()),
                "slots": len(st.pool.slots) if st.pool else 0,
                "auth": bool(s.master_keys)}

    @r.get("/admin/config")
    async def get_config():
        return snapshot()

    @r.get("/admin/self")
    async def get_self(request: Request, reveal: int = 0):
        origin = str(request.base_url).rstrip("/")
        full = (st.settings.master_keys or [""])[0]
        return {"base_url": origin + "/v1", "origin": origin,
                "auth": bool(full), "key": full if (reveal and full) else mask(full),
                "key_masked": mask(full), "key_file": str(key_path(cfg_path())),
                "models": sorted(st.settings.model_index())}

    @r.get("/admin/export")
    async def export_config():
        data = json.loads(json.dumps(read_raw(cfg_path()), default=str))
        for p in data.get("providers") or []:
            if isinstance(p, dict):
                p["keys"] = [mask(str(k)) for k in (p.get("keys") or [])]
        if data.get("master_keys"):
            data["master_keys"] = [mask(str(k)) for k in data["master_keys"]]
        return {"path": str(cfg_path()), "config": data}

    @r.put("/admin/config")
    async def put_config(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, detail={"error": {"message": "body must be an object",
                                                       "type": "invalid_request_error"}})
        path = cfg_path()
        data = read_raw(path)
        if body.get("strategy") not in (None, "priority", "round_robin", "weighted"):
            raise HTTPException(400, detail={"error": {"message": "bad strategy",
                                                       "type": "invalid_request_error"}})
        for k in GLOBALS:
            if k in body:
                data[k] = body[k]
        if "billing" in body:
            block = billing_map(body["billing"])
            cur = data.get("billing") or {}
            if isinstance(cur.get("rates"), dict) and isinstance(block.get("rates"), dict):
                block = {**cur, **block,
                         "rates": {**cur["rates"], **block["rates"]}}
            else:
                block = {**cur, **block}
            data["billing"] = block
            if block.get("currency"):
                data["currency"] = block["currency"]
        elif "currency" in body:
            cur = data.get("billing") or {}
            data["billing"] = {**cur, "currency": str(body["currency"]).strip().upper()}
        if "pricing" in body:
            data["pricing"] = pricing_map(body["pricing"])
        if isinstance(body.get("providers"), list):
            old = {str(p.get("name")): p for p in (data.get("providers") or [])}
            data["providers"] = [clean_provider(i, old.get(str(i.get("name"))))
                                 for i in body["providers"]]
        if not data.get("providers"):
            raise HTTPException(409, detail={"error": {"message": "refusing to drop all providers",
                                                       "type": "invalid_request_error"}})
        write_raw(path, data)
        return {"ok": True, "applied": apply_state(st), "config": snapshot()}

    r.include_router(billing_routes.build(st, dep))
    r.include_router(discover_routes.build(st, dep))

    @r.post("/admin/providers")
    async def add_provider(request: Request):
        body = await request.json()
        path = cfg_path()
        data = read_raw(path)
        provs = data.get("providers") or []
        raw_keys = [str(k).strip() for k in ((body or {}).get("keys") or [])
                    if str(k).strip() and "…" not in str(k)]
        item = clean_provider(body)
        if any(str(p.get("name")) == item["name"] for p in provs):
            raise HTTPException(409, detail={"error": {"message": "provider exists",
                                                       "type": "invalid_request_error"}})
        item["keys"] = raw_keys
        item = {k: v for k, v in item.items() if v not in ({}, [], "")}
        provs.append(item)
        data["providers"] = provs
        write_raw(path, data)
        return {"ok": True, "applied": apply_state(st), "config": snapshot()}

    @r.patch("/admin/providers/{name}")
    async def patch_provider(name: str, request: Request):
        body = await request.json()
        path = cfg_path()
        data = read_raw(path)
        provs = data.get("providers") or []
        old = find(provs, name)
        merged = dict(old)
        for f in P_FIELDS:
            if f in (body or {}):
                merged[f] = body[f]
        if "keys" in (body or {}):
            merged["keys"] = old.get("keys")
        item = clean_provider(merged, old)
        for i, p in enumerate(provs):
            if str(p.get("name")) == name:
                provs[i] = item
        data["providers"] = provs
        write_raw(path, data)
        return {"ok": True, "applied": apply_state(st), "config": snapshot()}

    @r.delete("/admin/providers/{name}")
    async def del_provider(name: str):
        path = cfg_path()
        data = read_raw(path)
        provs = data.get("providers") or []
        find(provs, name)
        if len(provs) <= 1:
            raise HTTPException(409, detail={"error": {"message": "refusing to delete the last provider",
                                                       "type": "invalid_request_error"}})
        data["providers"] = [p for p in provs if str(p.get("name")) != name]
        write_raw(path, data)
        return {"ok": True, "applied": apply_state(st), "config": snapshot()}

    @r.post("/admin/providers/{name}/keys")
    async def add_key(name: str, request: Request):
        body = await request.json()
        key = str((body or {}).get("key") or "").strip()
        if len(key) < 6:
            raise HTTPException(400, detail={"error": {"message": "key too short",
                                                       "type": "invalid_request_error"}})
        path = cfg_path()
        data = read_raw(path)
        item = find(data.get("providers") or [], name)
        keys = list(item.get("keys") or [])
        if key in keys:
            raise HTTPException(409, detail={"error": {"message": "key already in pool",
                                                       "type": "invalid_request_error"}})
        item["keys"] = keys + [key]
        write_raw(path, data)
        return {"ok": True, "applied": apply_state(st), "config": snapshot()}

    @r.delete("/admin/providers/{name}/keys/{idx}")
    async def del_key(name: str, idx: int):
        path = cfg_path()
        data = read_raw(path)
        item = find(data.get("providers") or [], name)
        keys = list(item.get("keys") or [])
        if not (0 <= idx < len(keys)):
            raise HTTPException(404, detail={"error": {"message": "no such key index",
                                                       "type": "invalid_request_error"}})
        if len(keys) == 1:
            raise HTTPException(409, detail={"error": {"message": "provider needs at least one key",
                                                       "type": "invalid_request_error"}})
        keys.pop(idx)
        item["keys"] = keys
        write_raw(path, data)
        return {"ok": True, "applied": apply_state(st), "config": snapshot()}

    @r.post("/admin/apply")
    async def reapply():
        return {"ok": True, "applied": apply_state(st), "config": snapshot()}

    return r
