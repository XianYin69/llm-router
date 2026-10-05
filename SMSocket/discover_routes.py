"""Discovery routes: probe providers, cache the catalog, publish aliases.

  POST  /admin/discover                 probe one/every provider (sync or async job)
  GET   /admin/discover/status          progress of the running probe
  GET   /admin/models                   cached catalog (no upstream traffic)
  DELETE /admin/models                  drop the cache (?provider= to scope it)
  POST  /admin/models/apply             write probed ids into provider aliases + pricing

`apply` only ever *adds* aliases and refuses to touch a name already published,
so discovery can never silently break a client that is using an existing alias.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request

from .discover import TEST_PROMPT, Catalog, Discoverer
from .providers import mask


def build(st, dep) -> APIRouter:
    r = APIRouter(dependencies=dep)

    def discoverer() -> Discoverer:
        if st.http is None:
            raise HTTPException(503, detail={"error": {
                "message": "gateway not started", "type": "server_error"}})
        return Discoverer(st.settings, st.http)

    def targets(body: dict) -> list:
        names = body.get("providers") or body.get("provider")
        provs = list(st.settings.providers)
        if isinstance(names, str):
            names = [names]
        if names:
            want = {str(n) for n in names}
            provs = [p for p in provs if p.name in want]
            missing = want - {p.name for p in provs}
            if missing:
                raise HTTPException(404, detail={"error": {
                    "message": f"unknown provider(s): {', '.join(sorted(missing))}",
                    "type": "invalid_request_error"}})
        adhoc = body.get("target")
        if isinstance(adhoc, dict) and adhoc.get("base_url"):
            from .config import ProviderSpec
            keys = adhoc.get("keys") or []
            if not keys:
                p0 = next((p for p in st.settings.providers
                           if p.base_url.rstrip("/") == str(adhoc["base_url"]).rstrip("/")), None)
                keys = list(p0.keys) if p0 else []
            if not keys:
                raise HTTPException(400, detail={"error": {
                    "message": "target needs keys (or a matching configured provider)",
                    "type": "invalid_request_error"}})
            provs = [ProviderSpec(name=str(adhoc.get("name") or "adhoc"),
                                  base_url=str(adhoc["base_url"]), keys=[str(k) for k in keys],
                                  style=str(adhoc.get("style") or "openai"))]
        if not provs:
            raise HTTPException(400, detail={"error": {
                "message": "nothing to probe", "type": "invalid_request_error"}})
        return provs

    def opts(body: dict) -> dict:
        return dict(
            models=body.get("models") or None,
            test_prompt=str(body.get("test_prompt") or TEST_PROMPT),
            probe_params=bool(body.get("probe_params", True)),
            probe_stream=bool(body.get("probe_stream", True)),
            probe_embeddings=bool(body.get("probe_embeddings", False)),
            concurrency=max(1, min(int(body.get("concurrency", 8) or 8), 64)),
            timeout=float(body.get("timeout", 30.0) or 30.0),
            max_models=max(1, min(int(body.get("max_models", 200) or 200), 2000)),
            max_params=max(0, min(int(body.get("max_params", 14) or 14), 40)))

    @r.post("/admin/discover")
    async def discover(request: Request):
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        provs, kw = targets(body), opts(body)
        as_job = bool(body.get("async") or body.get("async_"))
        d = discoverer()
        st.progress = {"started": time.time(), "done": 0, "total": 0, "models": [],
                       "providers": [p.name for p in provs], "status": "running",
                       "concurrency": kw["concurrency"], "results": []}

        async def probe_one(p):
            rep = await d.probe_provider(p, progress=lambda r: _tick(r), **kw)
            st.progress["results"].append({k: v for k, v in rep.items() if k != "models"})
            st.catalog.put(rep["models"])
            return rep

        async def run_all():
            reps = await asyncio.gather(*[probe_one(p) for p in provs],
                                        return_exceptions=True)
            out = []
            for rep in reps:
                if isinstance(rep, Exception):
                    out.append({"error": str(rep)[:300], "models": []})
                else:
                    out.append(rep)
            st.progress["status"] = "done"
            st.progress["finished"] = time.time()
            st.progress["report"] = out
            return out

        if as_job:
            st.progress["task"] = asyncio.create_task(run_all())
            return {"accepted": True, "providers": [p.name for p in provs],
                    "poll": "/admin/discover/status"}
        report = await run_all()
        return {"providers": report, "catalog": {"cached": len(st.catalog.all()),
                                                 "last_seen": st.catalog.last_seen()}}

    def _tick(row) -> None:
        st.progress["done"] += 1
        st.progress["models"].append({"provider": row.provider, "model": row.model,
                                      "ok": row.ok, "status": row.status,
                                      "latency_ms": row.latency_ms,
                                      "params": sum(1 for v in row.params.values()
                                                    if v == "supported")})

    @r.get("/admin/discover/status")
    async def discover_status():
        pr = getattr(st, "progress", None)
        if not pr:
            return {"status": "idle"}
        out = {k: v for k, v in pr.items() if k != "task"}
        out["running"] = bool(pr.get("task") and not pr["task"].done())
        out["elapsed"] = round(time.time() - pr["started"], 2)
        out["active"] = st.meter.active
        return out

    @r.get("/admin/models")
    async def models_cached(provider: str = ""):
        rows = st.catalog.all(provider)
        return {"object": "list", "count": len(rows), "last_seen": st.catalog.last_seen(),
                "published": sorted(st.settings.model_index()),
                "data": rows}

    @r.delete("/admin/models")
    async def models_clear(provider: str = ""):
        return {"cleared": st.catalog.clear(provider), "provider": provider or "*"}

    @r.post("/admin/models/apply")
    async def models_apply(request: Request):
        """Publish probed models as aliases (adds only, never overwrites)."""
        from .admin import cfg_path, find, read_raw, write_raw
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        names = body.get("providers") or body.get("provider")
        if isinstance(names, str):
            names = [names]
        want = {str(n) for n in (names or [])} or None
        rows = [x for x in st.catalog.all() if x.get("ok")
                and (not want or x["provider"] in want)]
        if not rows:
            raise HTTPException(404, detail={"error": {
                "message": "nothing probed yet - POST /admin/discover first",
                "type": "invalid_request_error"}})
        prefix = str(body.get("alias_prefix") or "")
        path = cfg_path()
        data = read_raw(path)
        provs = data.get("providers") or []
        added, skipped = [], []
        for p in provs:
            if not isinstance(p, dict):
                continue
            if want and str(p.get("name")) not in want:
                continue
            models = dict(p.get("models") or {})
            for row in [x for x in rows if x["provider"] == str(p.get("name"))]:
                alias = prefix + row["model"]
                if alias in models:
                    skipped.append(alias)
                    continue
                if not prefix and row["model"] in set(models.values()):
                    skipped.append(alias)      # an existing alias already serves it
                    continue
                if any(alias == str(k) for q in provs if isinstance(q, dict)
                       for k in (q.get("models") or {})):
                    skipped.append(alias)
                    continue
                models[alias] = row["model"]
                added.append(f"{p.get('name')}/{alias}")
            if models:
                p["models"] = models
        if not added:
            return {"ok": True, "added": [], "skipped": skipped,
                    "note": "every probed model is already published",
                    "applied": {"providers": len(st.settings.providers),
                                "keys": len(st.pool.slots),
                                "models": len(st.settings.model_index())}}
        data["providers"] = provs
        write_raw(path, data)
        applied = None
        try:
            from .admin import apply_state
            applied = apply_state(st)
        except HTTPException:
            raise
        return {"ok": True, "added": added, "skipped": skipped, "applied": applied}

    return r
