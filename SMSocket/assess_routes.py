"""Assessment routes: read the verdicts, fire a sweep, change the schedule.

  GET   /assess                 verdicts per model x egress (report window)
  POST  /assess/run             sweep now (sync, or ?async=1 / {"async":true})
  GET   /assess/status          schedule + progress + row counts
  GET   /assess/history         raw measurements for one model
  PUT   /assess/schedule        enable/disable, interval, daily HH:MM, sampling

These are ordinary authenticated routes (they appear in OpenAPI on purpose —
unlike /internal/net, an assessment report is not something to hide).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request


def build(st, dep) -> APIRouter:
    """Mounted by gateway; `dep` = master-key dependency list."""
    r = APIRouter(dependencies=dep)
    DISABLED = "assessment is disabled (set assess.enabled: true)"

    def need() -> object:
        a = getattr(st, "assessor", None)
        if a is None:
            raise HTTPException(409, detail={"error": {"message": DISABLED,
                                                       "type": "assess_disabled"}})
        return a

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:                                   # noqa: BLE001 - no body
            return {}
        return data if isinstance(data, dict) else {}

    def names(v):
        if v is None:
            return None
        if isinstance(v, str):
            v = [v]
        out = [str(x).strip() for x in (v or []) if str(x).strip()]
        return out or None

    @r.get("/assess")
    async def assess_report(window_s: float = 0, sources: str = "",
                            model: str = ""):
        a = getattr(st, "assessor", None)
        if a is None:
            return {"enabled": False, "rows": [], "by_egress": [], "models": 0,
                    "window_s": st.settings.assess.window_s, "counts": {},
                    "config": st.settings.assess.as_config(), "note": DISABLED}
        src = tuple(s.strip() for s in sources.split(",") if s.strip()) or \
            ("live", "probe")
        return a.report(window_s=window_s or None, sources=src, model=model)

    @r.post("/assess/run")
    async def assess_run(request: Request, async_: int = Query(0, alias="async")):
        a = need()
        b = await body(request)
        mods, paths = names(b.get("models")), names(b.get("egress"))
        conc = b.get("concurrency")
        as_job = bool(async_ or b.get("async") or b.get("async_"))
        known = set(a.models())
        bad = [m for m in (mods or []) if m not in known]
        if bad:
            raise HTTPException(404, detail={"error": {
                "message": f"unknown model(s): {', '.join(bad)}",
                "type": "invalid_request_error"}})
        if as_job:
            if a.progress.get("status") == "running":
                return {"accepted": False, "reason": "already running",
                        "progress": a.progress}
            asyncio.create_task(a.sweep(mods, paths, conc))
            return {"accepted": True, "running": True,
                    "poll": "/assess/status", "total": len(mods or a.models())
                    * len(paths or a.egress_paths())}
        out = await a.sweep(mods, paths, conc)
        rows = out.get("rows")
        if isinstance(rows, list) and len(rows) > 200:   # keep the answer small
            out["rows"], out["rows_truncated"] = rows[:200], len(rows) - 200
        return out

    @r.get("/assess/status")
    async def assess_status():
        a = getattr(st, "assessor", None)
        if a is None:
            return {"enabled": False, "scheduled": False, "running": False,
                    "config": st.settings.assess.as_config(),
                    "models": [], "egress": ["direct"], "progress": {},
                    "counts": {}}
        return a.status()

    @r.get("/assess/history")
    async def assess_history(model: str = "", days: int = 7, limit: int = 500):
        a = need()
        return a.history(model, max(1, min(days, 365)), max(1, min(limit, 5000)))

    @r.put("/assess/schedule")
    async def assess_schedule(request: Request):
        a = need()
        try:
            out = a.reschedule(await body(request))
        except ValueError as e:
            raise HTTPException(400, detail={"error": {
                "message": str(e), "type": "invalid_request_error"}}) from e
        if a.enabled and not out["scheduled"]:
            a.start()                       # turning it on must actually schedule
        elif not a.enabled:
            await a.stop()                  # turning it off must stop the loop
        return a.status()                   # the answer is the *new* state

    return r
