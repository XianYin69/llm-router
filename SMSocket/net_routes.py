"""Discreet net-plane routes: /internal/net/* (hidden from OpenAPI and the nav).

An operator can find these; a crawler cannot. The router is mounted with
`include_in_schema=False`, so nothing here appears in /openapi.json or /docs,
and the dashboard only shows a muted footer glyph when `clash.enabled` is true.

Everything answers in the same degraded way the gateway behaves: if clash is
off or unreachable you get a JSON answer describing that, never a stack trace.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

DISABLED = "clash net plane is disabled (set clash.enabled: true)"


async def _json(request: Request) -> dict:
    """Body as a dict - an empty or non-JSON body is simply {}."""
    try:
        data = await request.json()
    except Exception:                                   # noqa: BLE001 - no body
        return {}
    return data if isinstance(data, dict) else {}


def build(st, dep) -> APIRouter:
    """Mounted by gateway; `dep` = master-key dependency list."""
    r = APIRouter(dependencies=dep)

    def plane():
        net = getattr(st, "net", None)
        if net is None or not net.cfg.enabled:
            raise HTTPException(409, detail={"error": {"message": DISABLED,
                                                       "type": "net_plane_disabled"}})
        return net

    def fail(e: Exception) -> HTTPException:
        return HTTPException(503, detail={"error": {"message": str(e)[:300],
                                                    "type": "clash_unavailable"}})

    @r.get("/state")
    async def state():
        net = getattr(st, "net", None)
        if net is None:
            return {"enabled": False, "mode": "direct", "smart": False,
                    "controller": None, "alive": None, "paths": ["direct"],
                    "proxy": None, "interval": 0, "groups": [], "scores": 0,
                    "last_refresh": 0, "refreshing": False, "error": ""}
        return net.state()

    @r.get("/proxies")
    async def proxies():
        net = plane()
        if net.controller is None:
            return {"proxies": {}, "error": "no controller configured"}
        try:
            return await net.controller.proxies()
        except Exception as e:                                # ClashUnavailable
            return {"proxies": {}, "error": str(e)[:300]}

    @r.post("/probes")
    async def probes(request: Request, async_: int = Query(0, alias="async")):
        """Re-measure every (provider x path). async=1 returns immediately."""
        net = plane()
        if async_ or (await _json(request)).get("async"):
            asyncio.create_task(net.refresh(st.settings.providers))
            return {"accepted": True, "refreshing": True}
        res = await net.refresh(st.settings.providers)
        return {**res, "scores": [s.as_dict() for s in net.scores.values()]}

    @r.get("/scores")
    async def scores():
        net = plane()
        return {"scores": [s.as_dict() for s in sorted(
            net.scores.values(), key=lambda x: (x.provider, not x.ok, x.delay_ms))],
            "last_refresh": round(net.last_refresh, 1), "error": net.last_error}

    @r.put("/select")
    async def select(request: Request):
        """Pin a clash proxy group onto a node (PUT /proxies/{group})."""
        net = plane()
        body = await _json(request)
        group = str((body or {}).get("group") or "").strip()
        node = str((body or {}).get("node") or "").strip()
        if not group or not node:
            raise HTTPException(400, detail={"error": {
                "message": "group and node are required", "type": "invalid_request_error"}})
        if net.controller is None:
            raise fail(Exception("no controller configured"))
        try:
            out = await net.controller.select(group, node)
        except Exception as e:                                # ClashUnavailable
            raise fail(e) from e
        out["egress"] = f"node:{group}/{node}"
        return out

    @r.post("/mode")
    async def mode(request: Request):
        """Runtime switch: auto | direct | proxy (nothing is written to disk)."""
        net = plane()
        body = await _json(request)
        want = str((body or {}).get("mode") or "").strip().lower()
        if want not in ("auto", "direct", "proxy"):
            raise HTTPException(400, detail={"error": {
                "message": "mode must be auto|direct|proxy",
                "type": "invalid_request_error"}})
        net.mode = want
        return {"mode": want, "smart": net.smart, "state": net.state()}

    @r.post("/flush")
    async def flush(request: Request):
        """Drop clash's DNS cache ({"dns": true})."""
        net = plane()
        body = await _json(request)
        if not body or body.get("dns") is False:
            return {"flushed": False}
        if net.controller is None:
            raise fail(Exception("no controller configured"))
        try:
            return await net.controller.flush_dns()
        except Exception as e:                                # ClashUnavailable
            raise fail(e) from e

    @r.get("/egress/{provider}")
    async def egress_for(provider: str):
        """Ranked preview: what the gateway would choose for this provider."""
        net = plane()
        known = [p.name for p in st.settings.providers]
        if provider not in known:
            raise HTTPException(404, detail={"error": {
                "message": f"unknown provider: {provider}",
                "type": "invalid_request_error"}})
        return {"provider": provider, "pick": net.pick(provider),
                "paths": net.ranked(provider),
                "registry": net.registry.info()}

    return r
