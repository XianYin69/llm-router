"""Model discovery: ask a provider what it really serves, then prove it.

Flow per provider (all of it parallel, bounded by `concurrency`):

  1. GET /models            -> upstream model ids (+ context_length / created if given)
  2. POST a tiny chat per id -> is the key allowed to use it, how fast, how many tokens
  3. optional param probe    -> for each accepted model, send one request per parameter
     (temperature, top_p, tools, response_format, ...) and classify the answer:
        supported  2xx
        rejected   4xx whose message names the parameter
        unknown    5xx / network (cannot tell)
  4. optional stream + embeddings probe

Nothing here mutates the gateway config; `apply` in the route does that after a
report exists. Results are cached in the usage database (`models` table) so the
dashboard can show the catalog without re-spending tokens.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field

import httpx

from . import upstream as up
from .config import ProviderSpec, Settings
from .providers import mask

log = logging.getLogger("smssocket")

# param -> sample value that is valid for a chat completion
CHAT_PARAMS: dict[str, object] = {
    "temperature": 0.3,
    "top_p": 0.9,
    "max_tokens": 16,
    "max_completion_tokens": 16,
    "n": 1,
    "stop": ["\n"],
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "seed": 1234,
    "logprobs": True,
    "response_format": {"type": "text"},
    "reasoning_effort": "low",
    "tools": [{"type": "function", "function": {
        "name": "get_time", "description": "Current time",
        "parameters": {"type": "object", "properties": {}}}}],
}
TEST_PROMPT = "Reply with exactly one word: ok"
MAX_MODELS = 200


@dataclass
class ProbeResult:
    provider: str
    model: str
    ok: bool = False
    status: int = 0
    latency_ms: float = 0.0
    usage: dict = field(default_factory=dict)
    reply: str = ""
    error: str = ""
    context: object = None
    created: object = None
    owned_by: str = ""
    params: dict = field(default_factory=dict)      # name -> supported|rejected|unknown
    param_detail: dict = field(default_factory=dict)
    stream: str = ""                                 # supported|rejected|unknown
    embeddings: str = ""

    def as_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model, "ok": self.ok,
                "status": self.status, "latency_ms": self.latency_ms,
                "usage": self.usage, "reply": self.reply, "error": self.error,
                "context": self.context, "created": self.created,
                "owned_by": self.owned_by, "params": self.params,
                "param_detail": self.param_detail, "stream": self.stream,
                "embeddings": self.embeddings}


def models_url(p: ProviderSpec) -> str:
    base = p.base_url.rstrip("/")
    return base + "/models" if base.endswith("/v1") else base + "/v1/models"


def list_headers(p: ProviderSpec, key: str) -> dict:
    if p.style == "anthropic":
        return {"x-api-key": key, "anthropic-version": up.ANTHROPIC_VERSION,
                "content-type": "application/json"}
    return {"authorization": f"Bearer {key}", "content-type": "application/json"}


def parse_model_list(data) -> list[dict]:
    """Normalise OpenAI / Anthropic model listings into [{id, context, created, owned_by}]."""
    rows = data.get("data") if isinstance(data, dict) else data
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        mid = row.get("id") or row.get("model") or row.get("name")
        if not mid:
            continue
        meta = row.get("model_metadata") or row.get("meta") or {}
        ctx = (row.get("context_length") or row.get("context_window")
               or (meta.get("context_window") if isinstance(meta, dict) else None)
               or row.get("max_context_window"))
        out.append({"id": str(mid), "context": ctx,
                    "created": row.get("created"),
                    "owned_by": str(row.get("owned_by") or row.get("owner") or "")})
    return out


def classify(status: int, message: str, param: str) -> str:
    """supported / rejected / unknown for one probed parameter."""
    m = (message or "").lower()
    if 200 <= status < 300:
        return "supported"
    if status in (400, 404, 422):
        if param.replace("_", "") in m.replace("_", "").replace(" ", "") or \
           param in m or "unsupport" in m or "not supported" in m or \
           "invalid" in m or "unknown" in m or "unexpected" in m or "extra" in m:
            return "rejected"
        return "rejected"          # 4xx on a param-only payload => provider refused it
    return "unknown"


class Discoverer:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.s = settings
        self.http = client

    # ---- low level ---------------------------------------------------------
    async def _call(self, p: ProviderSpec, key: str, body: dict,
                    timeout: float) -> tuple[int, dict, str, float]:
        t0 = time.time()
        try:
            r = await self.http.post(up.chat_url(p), headers=up.headers(p, key),
                                     json=body, timeout=timeout)
            text = r.text[:400]
            try:
                data = r.json()
            except Exception:
                data = {}
            return r.status_code, data, text, round((time.time() - t0) * 1000, 1)
        except (httpx.RequestError, asyncio.TimeoutError) as e:
            return 0, {}, f"{type(e).__name__}: {e}", round((time.time() - t0) * 1000, 1)

    async def list_models(self, p: ProviderSpec, key: str, timeout: float) -> list[dict]:
        try:
            r = await self.http.get(models_url(p), headers=list_headers(p, key),
                                    timeout=timeout)
            if r.status_code >= 400:
                return []
            return parse_model_list(r.json())
        except (httpx.RequestError, asyncio.TimeoutError, ValueError):
            return []

    # ---- one model ---------------------------------------------------------
    async def probe_model(self, p: ProviderSpec, key: str, model: str, *,
                          test_prompt: str, probe_params: bool, probe_stream: bool,
                          timeout: float, max_params: int) -> ProbeResult:
        res = ProbeResult(provider=p.name, model=model)
        base = {"model": model, "messages": [{"role": "user", "content": test_prompt}]}
        if p.style == "anthropic":
            base = up.build_payload(p, base, model)
        status, data, text, ms = await self._call(p, key, base, timeout)
        res.status, res.latency_ms = status, ms
        if 200 <= status < 300:
            res.ok = True
            if p.style == "anthropic":
                oai = up.from_anthropic(data, model)
                res.usage = oai.get("usage") or {}
                res.reply = (oai["choices"][0]["message"]["content"] or "")[:80]
            else:
                res.usage = data.get("usage") or {}
                try:
                    res.reply = ((data["choices"][0]["message"]["content"]) or "")[:80]
                except Exception:
                    res.reply = ""
        else:
            res.error = (data.get("error") or {}).get("message") or text[:200] \
                if isinstance(data, dict) else text[:200]
            return res                      # no point probing a model we cannot call

        if probe_params:
            for name, value in list(CHAT_PARAMS.items())[:max_params]:
                body = dict(base)
                if p.style == "anthropic" and name in ("max_tokens",):
                    body["max_tokens"] = 16
                    continue
                body[name] = value
                st2, d2, t2, ms2 = await self._call(p, key, body, timeout)
                msg = ""
                if isinstance(d2, dict):
                    msg = str((d2.get("error") or {}).get("message") or d2.get("message") or "")
                verdict = classify(st2, msg, name)
                res.params[name] = verdict
                res.param_detail[name] = {"status": st2, "ms": ms2,
                                          **({"message": msg[:200]} if msg else {})}
        if probe_stream and p.style != "anthropic":
            body = dict(base)
            body["stream"] = True
            try:
                async with self.http.stream("POST", up.chat_url(p),
                                            headers=up.headers(p, key), json=body,
                                            timeout=timeout) as r:
                    if r.status_code >= 400:
                        await r.aread()
                        res.stream = classify(r.status_code, r.text[:200], "stream")
                    else:
                        got = False
                        async for chunk in r.aiter_bytes():
                            if b"data:" in chunk:
                                got = True
                                break
                        res.stream = "supported" if got else "unknown"
            except (httpx.RequestError, asyncio.TimeoutError):
                res.stream = "unknown"
        return res

    # ---- one provider ------------------------------------------------------
    async def probe_provider(self, p: ProviderSpec, *, models: list[str] | None = None,
                             only_listed: bool = True, test_prompt: str = TEST_PROMPT,
                             probe_params: bool = True, probe_stream: bool = True,
                             probe_embeddings: bool = False, concurrency: int = 8,
                             timeout: float = 30.0, max_models: int = MAX_MODELS,
                             max_params: int = len(CHAT_PARAMS),
                             progress=None) -> dict:
        keys = [k for k in p.keys if k]
        if not keys:
            return {"provider": p.name, "error": "no api key configured", "models": []}
        sem = asyncio.Semaphore(max(1, concurrency))
        listed = await self.list_models(p, keys[0], timeout)
        ids = [m["id"] for m in listed]
        meta = {m["id"]: m for m in listed}
        if models:
            ids = [i for i in ids if i in set(models)] or list(models)
        elif only_listed and not ids:
            ids = list(dict.fromkeys(list(p.models.values()) + list(p.embeddings.values())))
        else:
            ids = list(dict.fromkeys(ids + list(p.models.values())))
        ids = ids[:max_models]

        async def one(model_id: str) -> ProbeResult:
            async with sem:
                r = await self.probe_model(
                    p, keys[0], model_id, test_prompt=test_prompt,
                    probe_params=probe_params, probe_stream=probe_stream,
                    timeout=timeout, max_params=max_params)
                m = meta.get(model_id, {})
                r.context, r.created, r.owned_by = (m.get("context"), m.get("created"),
                                                    m.get("owned_by") or "")
                if probe_embeddings and r.ok:      # already inside `sem`
                    r.embeddings = await self._probe_embed(p, keys[0], model_id, timeout)
                if progress:
                    progress(r)
                return r

        t0 = time.time()
        results = await asyncio.gather(*[one(i) for i in ids], return_exceptions=True)
        rows = []
        for r in results:
            if isinstance(r, Exception):
                rows.append(ProbeResult(provider=p.name, model="-", error=str(r)[:200]).as_dict())
            else:
                rows.append(r.as_dict())
        return {"provider": p.name, "base_url": p.base_url, "style": p.style,
                "listed": len(listed), "probed": len(rows),
                "ok": sum(1 for r in rows if r["ok"]),
                "failed": sum(1 for r in rows if not r["ok"]),
                "wall_ms": round((time.time() - t0) * 1000, 1),
                "key": mask(keys[0]), "models": rows}

    async def _probe_embed(self, p: ProviderSpec, key: str, model: str,
                           timeout: float) -> str:
        try:
            r = await self.http.post(up.embed_url(p), headers=up.headers(p, key),
                                     json={"model": model, "input": "ping"},
                                     timeout=timeout)
            return classify(r.status_code, r.text[:200], "embeddings")
        except (httpx.RequestError, asyncio.TimeoutError):
            return "unknown"

    # ---- whole fleet -------------------------------------------------------
    async def run(self, providers: list[ProviderSpec], **kw) -> list[dict]:
        out = []
        for p in providers:
            if not p.enabled:
                continue
            out.append(await self.probe_provider(p, **kw))
        return out


# ---- catalog (sqlite, same file as the usage log) -------------------------
SCHEMA = """CREATE TABLE IF NOT EXISTS models(
  provider TEXT, model TEXT, data TEXT, ts REAL,
  PRIMARY KEY(provider, model))"""


class Catalog:
    def __init__(self, path: str) -> None:
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute(SCHEMA)
        self.con.commit()

    def put(self, rows: list[dict]) -> int:
        n = 0
        for r in rows:
            self.con.execute("INSERT OR REPLACE INTO models(provider,model,data,ts) VALUES(?,?,?,?)",
                             (r["provider"], r["model"], json.dumps(r, ensure_ascii=False),
                              time.time()))
            n += 1
        self.con.commit()
        return n

    def all(self, provider: str = "") -> list[dict]:
        q = "SELECT data FROM models" + (" WHERE provider=?" if provider else "") + " ORDER BY provider,model"
        cur = self.con.execute(q, (provider,) if provider else ())
        return [json.loads(r["data"]) for r in cur.fetchall()]

    def last_seen(self) -> float:
        row = self.con.execute("SELECT max(ts) t FROM models").fetchone()
        return float(row["t"] or 0.0)

    def clear(self, provider: str = "") -> int:
        if provider:
            n = self.con.execute("DELETE FROM models WHERE provider=?", (provider,)).rowcount
        else:
            n = self.con.execute("DELETE FROM models").rowcount
        self.con.commit()
        return n

    def close(self) -> None:
        self.con.close()
