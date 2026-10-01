"""OpenAI-compatible gateway built on FastAPI."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import Settings, load_config
from .dashboard import PAGE
from .providers import Pool
from .router import NoUpstream, Router, UpstreamError
from .usage import Usage

log = logging.getLogger("llmrouter")


class State:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool = Pool(settings.providers)
        self.usage = Usage(settings.db_path)
        self.http: httpx.AsyncClient | None = None
        self.router: Router | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    st = State(settings or load_config(os.environ.get("LLMROUTER_CONFIG", "config.yaml")))
    logging.basicConfig(level=getattr(logging, st.settings.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st.http = httpx.AsyncClient(follow_redirects=True,
                                    limits=httpx.Limits(max_connections=100))
        st.router = Router(st.settings, st.pool, st.usage, st.http)
        log.info("llm-router ready: %d providers, %d keys, %d models",
                 len(st.settings.providers), len(st.pool.slots), len(st.settings.model_index()))
        yield
        await st.http.aclose()
        st.usage.close()

    app = FastAPI(title="llm-router", version="0.1.0", lifespan=lifespan)
    app.state.llm = st

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


    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "providers": len(st.settings.providers),
                "keys": len(st.pool.slots), "models": len(st.settings.model_index())}

    @app.get("/v1/models", dependencies=dep)
    async def models():
        idx = st.settings.model_index()
        return {"object": "list", "data": [
            {"id": a, "object": "model", "created": 0,
             "owned_by": ",".join(sorted({p.name for p in idx[a]}))} for a in sorted(idx)]}

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
        try:
            if payload.get("stream"):
                slot, alias, t0, resp = await st.router.open_stream(payload)
                return StreamingResponse(st.router.wrap_stream(slot, alias, t0, resp),
                                         media_type="text/event-stream",
                                         headers={"x-router-upstream": slot.provider.name})
            return JSONResponse(await st.router.complete(payload))
        except NoUpstream as e:
            raise HTTPException(503, detail={"error": {"message": str(e), "type": "server_error"}})
        except UpstreamError as e:
            raise HTTPException(e.status, detail={"error": {"message": str(e.detail),
                                                            "type": "upstream_error"}})

    @app.get("/stats")
    async def stats():
        s = st.usage.summary()
        return {"by_provider": s["providers"], "by_model": s["models"],
                "calls": s["total"]["c"], "tokens": s["total"]["t"] or 0,
                "recent": st.usage.recent(30)}

    @app.get("/pool")
    async def pool():
        return {"strategy": st.settings.strategy, "retry": st.settings.retry,
                "cooldown": st.settings.cooldown, "slots": st.pool.stats()}

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return PAGE

    @app.post("/admin/reload", dependencies=dep)
    async def reload_config():
        st.settings = load_config(os.environ.get("LLMROUTER_CONFIG", "config.yaml"))
        st.pool = Pool(st.settings.providers)
        st.router = Router(st.settings, st.pool, st.usage, st.http)
        return {"ok": True, "providers": len(st.settings.providers)}

    return app
