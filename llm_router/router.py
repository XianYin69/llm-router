"""Routing engine: candidate plan, upstream call, failover, usage accounting."""
from __future__ import annotations

import json
import logging
import time

import httpx

from . import upstream as up
from .config import Settings
from .providers import KeySlot, Pool
from .usage import Usage

log = logging.getLogger("llmrouter")
RETRY_STATUS = {401, 403, 408, 429, 500, 502, 503, 504, 529}


class NoUpstream(Exception):
    """Nothing available for this model right now."""


class UpstreamError(Exception):
    def __init__(self, status: int, detail, slot: KeySlot | None = None):
        super().__init__(str(detail))
        self.status, self.detail, self.slot = status, detail, slot


class Router:
    def __init__(self, settings: Settings, pool: Pool, usage: Usage, client: httpx.AsyncClient):
        self.s, self.pool, self.usage, self.http = settings, pool, usage, client

    def plan(self, alias: str) -> list[KeySlot]:
        cands = self.pool.candidates(alias, self.s.strategy)
        if not cands:
            raise NoUpstream(f"no available upstream key for model '{alias}'")
        return cands[: max(1, self.s.retry + 1)]

    def _request(self, slot: KeySlot, alias: str, payload: dict, stream: bool) -> httpx.Request:
        p = slot.provider
        body = up.build_payload(p, payload, self.pool.upstream_model(slot, alias))
        if stream and p.style != "anthropic":
            body["stream"] = True
            body.setdefault("stream_options", {"include_usage": True})
        return self.http.build_request("POST", up.chat_url(p), headers=up.headers(p, slot.key),
                                       json=body, timeout=p.timeout)


    def _account(self, slot: KeySlot, alias: str, status: int, t0: float,
                 usage: dict | None, stream: int, error: str = "", upstream: str = "") -> None:
        u = usage or {}
        self.usage.log(ts=time.time(), alias=alias, upstream=upstream or alias,
                       provider=slot.provider.name, key=slot.label.split("/")[-1],
                       status=status, ms=round((time.time() - t0) * 1000, 1),
                       prompt=int(u.get("prompt_tokens", 0) or 0),
                       completion=int(u.get("completion_tokens", 0) or 0),
                       total=int(u.get("total_tokens", 0) or 0),
                       stream=stream, error=error[:300],
                       cost=self.s.cost_of(alias, int(u.get("prompt_tokens", 0) or 0),
                                           int(u.get("completion_tokens", 0) or 0)))

    async def embeddings(self, payload: dict) -> dict:
        # OpenAI-compatible /v1/embeddings, same pool + failover rules as chat.
        alias = str(payload.get("model") or "")
        cands = self.pool.candidates(alias, self.s.strategy, embed=True)
        if not cands:
            raise NoUpstream(f"no embedding upstream for model '{alias}'")
        last: UpstreamError | None = None
        for slot in cands[: max(1, self.s.retry + 1)]:
            t0 = time.time()
            up_model = self.pool.upstream_model(slot, alias, embed=True)
            req = self.http.build_request("POST", up.embed_url(slot.provider),
                                          headers=up.headers(slot.provider, slot.key),
                                          json=dict(payload, model=up_model),
                                          timeout=slot.provider.timeout)
            try:
                r = await self.http.send(req)
            except httpx.RequestError as e:
                slot.note_fail(self.s.cooldown)
                last = UpstreamError(502, f"{slot.label}: {e}", slot)
                continue
            if r.status_code >= 400:
                slot.note_fail(self.s.cooldown)
                detail = _safe_detail(r)
                last = UpstreamError(r.status_code, detail, slot)
                self._account(slot, alias, r.status_code, t0, None, 0, str(detail))
                continue
            data = r.json()
            slot.note_ok()
            data["model"] = alias
            self._account(slot, alias, 200, t0, data.get("usage"), 0, upstream=up_model)
            return data
        raise last or UpstreamError(502, "all embedding upstreams failed")

    async def complete(self, payload: dict) -> dict:
        alias = str(payload.get("model") or "")
        last: UpstreamError | None = None
        for slot in self.plan(alias):
            t0 = time.time()
            try:
                r = await self.http.send(self._request(slot, alias, payload, False))
                if r.status_code >= 400:
                    slot.note_fail(self.s.cooldown)
                    detail = _safe_detail(r)
                    if r.status_code not in RETRY_STATUS:
                        raise UpstreamError(r.status_code, detail, slot)
                    last = UpstreamError(502, f"{slot.label}: {detail}", slot)
                    self._account(slot, alias, r.status_code, t0, None, 0, str(detail))
                    log.warning("failover from %s (%s)", slot.label, detail)
                    continue
                body = r.json()
                if slot.provider.style == "anthropic":
                    body = up.from_anthropic(body, alias)
                slot.note_ok()
                body["model"] = alias
                self._account(slot, alias, r.status_code, t0, body.get("usage"), 0,
                              upstream=self.pool.upstream_model(slot, alias))
                return body
            except (httpx.RequestError, ValueError) as e:
                slot.note_fail(self.s.cooldown)
                last = UpstreamError(502, f"{slot.label}: {type(e).__name__}: {e}", slot)
                self._account(slot, alias, 502, t0, None, 0, str(e))
                log.warning("upstream %s error: %s", slot.label, e)
        raise last or UpstreamError(502, "all upstreams failed")


    async def open_stream(self, payload: dict):
        """Try candidates until one opens a stream; returns (slot, alias, t0, response)."""
        alias = str(payload.get("model") or "")
        last: UpstreamError | None = None
        for slot in self.plan(alias):
            t0 = time.time()
            try:
                r = await self.http.send(self._request(slot, alias, payload, True), stream=True)
                if r.status_code >= 400:
                    await r.aread()
                    slot.note_fail(self.s.cooldown)
                    detail = _safe_detail(r)
                    if r.status_code not in RETRY_STATUS:
                        raise UpstreamError(r.status_code, detail, slot)
                    last = UpstreamError(502, f"{slot.label}: {detail}", slot)
                    continue
                return slot, alias, t0, r
            except httpx.RequestError as e:
                slot.note_fail(self.s.cooldown)
                last = UpstreamError(502, f"{slot.label}: {e}", slot)
        raise last or UpstreamError(502, "all upstreams failed")

    async def wrap_stream(self, slot: KeySlot, alias: str, t0: float, r: httpx.Response):
        usage: dict = {}
        try:
            if slot.provider.style == "anthropic":
                async for c in up.anthropic_stream_to_openai(r.aiter_bytes(), alias):
                    if c.get("usage"):
                        usage = c["usage"]
                    yield up.sse(c)
                yield b"data: [DONE]\n\n"
            else:
                buf = b""
                async for raw in r.aiter_raw():
                    buf += raw
                    while b"\n\n" in buf:
                        evt, buf = buf.split(b"\n\n", 1)
                        out, u = _normalize_event(evt, alias)
                        usage = u or usage
                        yield out
                if buf.strip():
                    out, u = _normalize_event(buf, alias)
                    usage = u or usage
                    yield out
        finally:
            await r.aclose()
            slot.note_ok()
            self._account(slot, alias, 200, t0, usage, 1,
                          upstream=self.pool.upstream_model(slot, alias))


def _normalize_event(evt: bytes, alias: str) -> tuple:
    """Rewrite one SSE event: guarantee object + public model, sniff usage."""
    out, usage = [], {}
    for line in evt.split(b"\n"):
        s = line.strip()
        if not s.startswith(b"data:"):
            out.append(line)
            continue
        payload = s[5:].strip()
        if payload in (b"", b"[DONE]"):
            out.append(line)
            continue
        try:
            obj = json.loads(payload)
        except Exception:
            out.append(line)
            continue
        obj.setdefault("object", "chat.completion.chunk")
        obj["model"] = alias
        if obj.get("usage"):
            usage = obj["usage"]
        out.append(b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    return b"\n".join(out) + b"\n\n", usage

def _sniff_usage(chunk: bytes, cur: dict) -> dict:
    for line in chunk.split(b"\n"):
        s = line.decode("utf-8", "ignore").strip()
        if s.startswith("data:") and "usage" in s:
            try:
                u = json.loads(s[5:].strip()).get("usage")
                if u:
                    return u
            except Exception:
                pass
    return cur


def _safe_detail(r) -> str:
    try:
        j = r.json()
        err = j.get("error") if isinstance(j.get("error"), dict) else {}
        return str(err.get("message") or j.get("detail") or j)[:200] if j else r.text[:200]
    except Exception:
        return r.text[:200]
