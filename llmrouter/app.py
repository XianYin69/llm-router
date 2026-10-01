"""FastAPI app: OpenAI-compatible aggregation gateway."""
from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from . import upstream
from .config import load
from .pool import KeyPool
from .router import Router
from .stats import Stats

STATE = {"settings": None, "pool": None, "router": None, "stats": None}


def _err(status: int, msg: str, etype: str = "server_error") -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"message": msg, "type": etype, "code": status}})


def _auth_ok(req: Request) -> bool:
    st = STATE["settings"]
    if st.anon_allowed or not st.master_keys:
        return True
    got = req.headers.get("authorization", "")
    token = got[7:] if got.lower().startswith("bearer ") else got
    return token in st.master_keys


def create_app(config: str | os.PathLike | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = load(config)
        pool = KeyPool()
        for name, prov in st.providers.items():
            pool.register(name, prov.api_keys)
        STATE.update(settings=st, pool=pool, router=Router(st, pool), stats=Stats())
        yield

    app = FastAPI(title="llm-router", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        st = STATE["settings"]
        return {"ok": bool(st), "providers": len(st.providers) if st else 0,
                "models": len(st.routes) if st else 0}

    @app.get("/v1/models")
    async def models(req: Request):
        if not _auth_ok(req):
            return _err(401, "invalid api key", "authentication_error")
        st = STATE["settings"]
        return {"object": "list",
                "data": [{"id": m, "object": "model", "owned_by": "llm-router"}
                         for m in STATE["router"].public_models()]}

    @app.get("/stats")
    async def stats():
        return STATE["stats"].snapshot()

    @app.get("/pool")
    async def pool_view():
        return STATE["pool"].snapshot()


    @app.post("/v1/chat/completions")
    async def chat_completions(req: Request):
        if not _auth_ok(req):
            return _err(401, "invalid api key", "authentication_error")
        try:
            payload = await req.json()
        except Exception:
            return _err(400, "invalid JSON body", "invalid_request_error")
        model = payload.get("model")
        if not model:
            return _err(400, "'model' is required", "invalid_request_error")
        router, stats = STATE["router"], STATE["stats"]
        t0 = time.time()
        if payload.get("stream"):
            try:
                gen, route = await router.stream(model, payload)
            except upstream.UpstreamError as exc:
                stats.record(model, False, time.time() - t0)
                return _err(502, f"all upstreams failed: {exc.detail}")
            return StreamingResponse(_count_stream(gen, model, route, stats, t0),
                                     media_type="text/event-stream")
        try:
            data, route = await router.complete(model, payload)
        except upstream.UpstreamError as exc:
            stats.record(model, False, time.time() - t0)
            return _err(502 if exc.retryable else exc.status, exc.detail)
        usage = data.get("usage") or {}
        data["model"] = model
        data["_routed_via"] = route.ref
        stats.record(model, True, time.time() - t0,
                     int(usage.get("prompt_tokens", 0)),
                     int(usage.get("completion_tokens", 0)))
        return data

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        f = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"
        return HTMLResponse(f.read_text(encoding="utf-8"))

    return app


async def _count_stream(gen, model, route, stats, t0):
    words = 0
    try:
        async for line in gen:
            raw = line[5:].strip()
            if raw and raw != "[DONE]":
                try:
                    frag = json.loads(raw)
                    for ch in frag.get("choices") or []:
                        words += len(((ch.get("delta") or {}).get("content") or "").split())
                except Exception:
                    pass
            yield line + "\n\n"
        stats.record(model, True, time.time() - t0, 0, words)
    except Exception as exc:
        stats.record(model, False, time.time() - t0)
        yield "data: " + json.dumps({"error": {"message": str(exc)[:200]}}) + "\n\n"


app = create_app(os.environ.get("LLMROUTER_CONFIG", "config.yaml"))
