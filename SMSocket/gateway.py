"""OpenAI-compatible gateway built on FastAPI."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .batch import BatchRunner, BatchTooLarge
from .clash import ClashController, EgressRegistry, NetPlane
from .net_routes import build as build_net
from .concurrency import Gate, Meter, SMSocketBusy
from .config import Settings, envv, load_config
from .discover import Catalog
from .admin import build as build_admin
from .dashboard import PAGE
from .providers import Pool
from .router import NoUpstream, Router, UpstreamError
from .usage import Usage

log = logging.getLogger("smssocket")


class State:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool = Pool(settings.providers)
        self.usage = Usage(settings.db_path)
        self.http: httpx.AsyncClient | None = None
        self.router: Router | None = None
        self.meter = Meter()
        self.batch = BatchRunner()
        self.catalog = Catalog(settings.db_path)
        self.progress: dict = {}
        self.gate = Gate(settings.max_concurrency,
                                 settings.queue_wait, settings.per_provider_concurrency,
                                 self.meter)
        # clash net plane - all None while settings.clash.enabled is false
        self.clash: ClashController | None = None
        self.egress: EgressRegistry | None = None
        self.net: NetPlane | None = None

    def build_net_plane(self) -> None:
        """(Re)create controller / egress registry / scoring plane from config.

        Disabled config leaves `st.http` as the only client, so the gateway
        behaves exactly like it did before v0.4a.
        """
        cfg = self.settings.clash
        self.clash = self.egress = self.net = None
        if not (cfg.enabled and self.http is not None):
            return
        self.clash = ClashController(cfg)
        self.egress = EgressRegistry(self.http, cfg)
        self.net = NetPlane(cfg, self.egress, self.clash)
        self.net.set_targets(self.settings.providers)

    def attach_router(self) -> Router:
        self.router = Router(self.settings, self.pool, self.usage, self.http,
                             self.gate, net=self.net, egress=self.egress)
        return self.router


async def rebuild_net_plane(st: State) -> None:
    """Stop the old plane (task + proxy clients), build a fresh one, restart."""
    if st.net is not None:
        await st.net.stop()
    st.build_net_plane()
    if st.net is not None:
        st.net.start()
        log.info("net plane up: controller=%s paths=%s", st.settings.clash.controller,
                 len(st.net.registry.paths()))


def create_app(settings: Settings | None = None) -> FastAPI:
    st = State(settings or load_config(envv("SMSSOCKET_CONFIG", "config.yaml")))
    logging.basicConfig(level=getattr(logging, st.settings.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st.http = httpx.AsyncClient(follow_redirects=True,
                                    limits=httpx.Limits(max_connections=100))
        st.build_net_plane()
        st.attach_router()
        if st.net is not None:
            st.net.start()
        log.info("SMSocket ready: %d providers, %d keys, %d models",
                 len(st.settings.providers), len(st.pool.slots), len(st.settings.model_index()))
        yield
        if st.net is not None:
            await st.net.stop()
        await st.http.aclose()
        st.usage.close()
        st.catalog.close()

    app = FastAPI(title="SMSocket", version="0.4.0", lifespan=lifespan)
    app.state.llm = st

    @app.exception_handler(SMSocketBusy)
    async def _saturated(request: Request, exc: SMSocketBusy):
        """Gate is full and the caller gave up waiting -> 429 + Retry-After."""
        return JSONResponse({"error": {"message": str(exc), "type": "rate_limited",
                                       "code": "gateway_saturated"}},
                            status_code=429,
                            headers={"retry-after": str(exc.retry_after)})

    def authorize(request: Request) -> None:
        keys = st.settings.master_keys
        if not keys:
            return
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        token = token or request.query_params.get("api_key", "")
        if token not in keys:
            raise HTTPException(401, detail={"error": {"message": "invalid api key",
                                                       "type": "authentication_error"}})

    dep = [Depends(authorize)]

    def egress_headers(egress: str) -> dict:
        """x-socket-egress only while the net plane is live (no new headers
        for a gateway that has clash disabled)."""
        if getattr(st, "net", None) is None:
            return {}
        return {"x-socket-egress": egress or "direct"}


    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "providers": len(st.settings.providers),
                "keys": len(st.pool.slots), "models": len(st.settings.model_index()),
                "active": st.meter.active, "currency": st.settings.billing.currency}

    # ---- parallel batch + async jobs --------------------------------------
    @app.post("/v1/batch", dependencies=dep)
    async def batch(request: Request, async_: int = Query(0, alias="async"),
                    wait: int = 1):
        """Send many chat payloads at once.

        {"requests": [{model, messages, ...}, ...], "concurrency": 8,
         "fail_fast": false, "async": true}
        Sync (default) returns every result; async returns a job id to poll.
        """
        body = await request.json()
        items = (body or {}).get("requests") or (body or {}).get("items") or []
        if not isinstance(items, list) or not items:
            raise HTTPException(400, detail={"error": {
                "message": "requests must be a non-empty list",
                "type": "invalid_request_error"}})
        known = st.settings.model_index()
        bad = [i for i, it in enumerate(items)
               if not isinstance(it, dict) or not str(it.get("model") or "")]
        if bad:
            raise HTTPException(400, detail={"error": {
                "message": f"item {bad[0]} needs a model", "type": "invalid_request_error"}})
        unknown = sorted({str(it.get("model")) for it in items
                          if isinstance(it, dict) and str(it.get("model") or "") not in known})
        if unknown:
            raise HTTPException(404, detail={"error": {
                "message": "unknown model(s): " + ", ".join(unknown),
                "type": "invalid_request_error"}})
        conc = int(body.get("concurrency", 8) or 8)
        if not 1 <= conc <= 256:
            raise HTTPException(400, detail={"error": {
                "message": "concurrency must be 1..256", "type": "invalid_request_error"}})
        fail_fast = bool(body.get("fail_fast", False))
        as_job = bool(async_ or body.get("async") or not wait)
        try:
            if as_job:
                job = st.batch.start(st.router, items, conc, fail_fast)
                return JSONResponse({"accepted": True, "job": job.id,
                                     "total": job.total, "concurrency": conc,
                                     "poll": f"/v1/batches/{job.id}"}, status_code=202)
            out = await st.batch.run(st.router, items, conc, fail_fast)
            out["concurrency_live"] = st.meter.snapshot(st.gate.limits())
            return JSONResponse(out)
        except BatchTooLarge as e:
            raise HTTPException(413, detail={"error": {"message": str(e),
                                                       "type": "invalid_request_error"}})

    @app.get("/v1/batches", dependencies=dep)
    async def batches():
        return {"object": "list", "running": st.batch.running(),
                "jobs": st.batch.list()}

    @app.get("/v1/batches/{job_id}", dependencies=dep)
    async def batch_job(job_id: str, results: int = 0, wait: int = 0):
        """Poll a job. wait=1 blocks until it finishes (bounded by job size)."""
        job = st.batch.get(job_id)
        if job is None:
            raise HTTPException(404, detail={"error": {
                "message": f"unknown job: {job_id}", "type": "invalid_request_error"}})
        if wait and job.task and job.status == "running":
            try:
                await asyncio.wait_for(asyncio.shield(job.task), timeout=float(wait))
            except asyncio.TimeoutError:
                pass
        view = job.view(with_results=True, limit=results or 0)
        view["summary"] = st.batch.summary(job.results, job.concurrency,
                                           round(view["elapsed"] * 1000, 1),
                                           st.settings.billing)["summary"]
        return view

    @app.delete("/v1/batches/{job_id}", dependencies=dep)
    async def batch_cancel(job_id: str):
        job = st.batch.get(job_id)
        if job is None:
            raise HTTPException(404, detail={"error": {
                "message": f"unknown job: {job_id}", "type": "invalid_request_error"}})
        return {"job": job_id, "cancelled": st.batch.cancel(job_id),
                "status": job.status}

    @app.get("/concurrency")
    async def concurrency():
        """Live in-flight count, peak, queue depth, per-provider breakdown."""
        return st.meter.snapshot(st.gate.limits())

    def busy(e: SMSocketBusy):
        return JSONResponse(
            {"error": {"message": str(e), "type": "rate_limited",
                      "code": "gateway_saturated"}},
            status_code=429, headers={"retry-after": str(e.retry_after)})

    @app.get("/v1/models", dependencies=dep)
    async def models():
        idx, eidx = st.settings.model_index(), st.settings.embed_index()
        data = [{"id": a, "object": "model", "created": 0,
                 "owned_by": ",".join(sorted({p.name for p in idx[a]}))} for a in sorted(idx)]
        data += [{"id": a, "object": "model", "created": 0,
                  "owned_by": ",".join(sorted({p.name for p in eidx[a]}))} for a in sorted(eidx)]
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions", dependencies=dep)
    async def chat(request: Request):
        payload = await request.json()
        alias = str(payload.get("model") or "")
        if not alias:
            raise HTTPException(400, detail={"error": {"message": "model is required",
                                                       "type": "invalid_request_error"}})
        if alias not in st.settings.model_index():
            raise HTTPException(404, detail={"error": {"message": f"unknown model '{alias}'",
                                                       "type": "invalid_request_error"}})
        out: dict = {}
        try:
            if payload.get("stream"):
                slot, alias, t0, resp = await st.router.open_stream(payload)
                hdr = {"x-socket-upstream": slot.provider.name,
                       "x-router-upstream": slot.provider.name,
                       **egress_headers(getattr(resp, "_sms_egress", ""))}
                return StreamingResponse(st.router.wrap_stream(slot, alias, t0, resp),
                                         media_type="text/event-stream", headers=hdr)
            return JSONResponse(await st.router.complete(payload, egress_out=out),
                                headers=egress_headers(out.get("egress", "")))
        except NoUpstream as e:
            raise HTTPException(503, detail={"error": {"message": str(e), "type": "server_error"}})
        except UpstreamError as e:
            raise HTTPException(e.status, detail={"error": {"message": str(e.detail),
                                                            "type": "upstream_error"}})

    def money(currency: str | None = None) -> dict:
        """Spend totals: per pricing currency, converted into the display one."""
        b = st.settings.billing
        disp = (currency or b.currency).strip().upper() or b.currency
        rows = []
        for row in st.usage.by_currency():
            cur = (row["currency"] or b.base).upper()
            rows.append({"currency": cur, "symbol": b.symbol(cur),
                         "calls": row["calls"] or 0, "tokens": row["tokens"] or 0,
                         "amount": round(row["cost"] or 0.0, 8),
                         "rate_to_display": round(b.rate(cur, disp), 8),
                         "in_display": b.convert(row["cost"] or 0.0, cur, disp)})
        total = round(sum(r["in_display"] for r in rows), b.precision)
        return {"display_currency": disp, "symbol": b.symbol(disp), "total": total,
                "base": b.base, "rates": b.rates, "by_currency": rows}

    @app.get("/stats")
    async def stats(currency: str = None):
        s = st.usage.summary()
        m = money(currency)
        return {"by_provider": s["providers"], "by_model": s["models"],
                "calls": s["total"]["c"], "tokens": s["total"]["t"] or 0,
                "cost": m["total"], "currency": m["display_currency"],
                "symbol": m["symbol"], "money": m,
                "billing": st.settings.billing.table(),
                "recent": st.usage.recent(30)}
    @app.get("/v1/usage", dependencies=dep)
    async def usage(days: int = 14, currency: str = None):
        s = st.usage.summary()
        b = st.settings.billing
        m = money(currency)
        daily = st.usage.daily(max(1, min(days, 365)))
        for row in daily:
            row["cost"] = b.convert(row.get("display") or 0.0,
                                    row.get("currency") or b.currency,
                                    m["display_currency"])
            row["currency"] = m["display_currency"]
        return {"currency": m["display_currency"], "symbol": m["symbol"],
                "totals": {"calls": s["total"]["c"], "tokens": s["total"]["t"] or 0,
                           "cost": m["total"]},
                "money": m, "billing": b.table(),
                "by_provider": s["providers"], "by_model": s["models"],
                "daily": daily}
    @app.post("/v1/embeddings", dependencies=dep)
    async def embeddings(request: Request):
        payload = await request.json()
        alias = str(payload.get("model") or "")
        if not alias:
            raise HTTPException(400, detail={"error": {"message": "model is required"}})
        out: dict = {}
        try:
            return JSONResponse(await st.router.embeddings(payload, egress_out=out),
                                headers=egress_headers(out.get("egress", "")))
        except NoUpstream as e:
            raise HTTPException(404, detail={"error": {"message": str(e), "type": "invalid_request_error"}})
        except SMSocketBusy as e:
            return busy(e)
        except UpstreamError as e:
            raise HTTPException(e.status, detail={"error": {"message": str(e.detail),
                                                            "type": "upstream_error"}})

    @app.get("/pool")
    async def pool():
        return {"strategy": st.settings.strategy, "retry": st.settings.retry,
                "cooldown": st.settings.cooldown, "slots": st.pool.stats()}

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        # __NET_ON__ drives the discreet footer glyph: 0 -> the panel is absent
        # from the DOM entirely, so a disabled net plane leaves no trace.
        return PAGE.replace("__NET_ON__", "1" if st.settings.clash.enabled else "0")

    @app.post("/admin/reload", dependencies=dep)
    async def reload_config():
        # Never swap in an empty/broken config: a bad reload must leave live state intact.
        path = envv("SMSSOCKET_CONFIG", "config.yaml")
        if not Path(path).exists():
            raise HTTPException(409, detail={"error": {
                "message": f"config not found: {path}", "type": "invalid_request_error"}})
        try:
            new = load_config(path)
        except Exception as exc:
            raise HTTPException(400, detail={"error": {
                "message": f"config rejected: {exc}", "type": "invalid_request_error"}})
        if not new.providers:
            raise HTTPException(409, detail={"error": {
                "message": "config has no providers, refusing to reload",
                "type": "invalid_request_error"}})
        st.settings = new
        st.pool = st.pool.rebase(new.providers) if st.pool else Pool(new.providers)
        st.gate = Gate(new.max_concurrency, new.queue_wait,
                       new.per_provider_concurrency, st.meter)
        await rebuild_net_plane(st)
        st.attach_router()
        log.info("reloaded: %d providers, %d keys", len(new.providers), len(st.pool.slots))
        return {"ok": True, "providers": len(new.providers), "keys": len(st.pool.slots),
                "net_plane": bool(st.net is not None)}

    app.include_router(build_admin(st, dep))   # includes /admin/billing*
    # Discreet operator surface: not in /openapi.json, not in /docs, not in the nav.
    app.include_router(build_net(st, dep), prefix="/internal/net",
                       include_in_schema=False)
    return app
