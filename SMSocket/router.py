"""Routing engine: candidate plan, upstream call, failover, usage accounting."""
from __future__ import annotations

import json
import logging
import time

import httpx

from . import upstream as up
from .clash import DIRECT
from .config import Settings
from .concurrency import Gate, SMSocketBusy
from .providers import KeySlot, Pool
from .usage import Usage

log = logging.getLogger("smssocket")
RETRY_STATUS = {401, 403, 408, 429, 500, 502, 503, 504, 529}


class NoUpstream(Exception):
    """Nothing available for this model right now."""


class UpstreamError(Exception):
    def __init__(self, status: int, detail, slot: KeySlot | None = None,
                 upstream_status: int = 0):
        super().__init__(str(detail))
        # `status` is what the client sees (a failover is reported as 502);
        # `upstream_status` is what the provider actually answered, which is
        # the difference between "blocked here" and "having a bad day".
        self.status, self.detail, self.slot = status, detail, slot
        self.upstream_status = int(upstream_status or 0)


class Router:
    """Turns one request into one successful upstream call.

    `net` (clash.NetPlane) and `egress` (clash.EgressRegistry) are optional:
    without them every call leaves through the shared client exactly as before.
    With them, each attempt asks the plane which path is currently best and
    sends through that path's client, recording the choice in the usage row and
    in the `x-socket-egress` response header.
    """

    def __init__(self, settings: Settings, pool: Pool, usage: Usage,
                 client: httpx.AsyncClient, gate: Gate | None = None,
                 net=None, egress=None):
        self.s, self.pool, self.usage, self.http = settings, pool, usage, client
        self.gate = gate or Gate()
        self.net = net
        self.egress = egress if egress is not None else getattr(net, "registry", None)
        self.assessor = None        # assess.Assessor, set by State.attach_router

    def plan(self, alias: str) -> list[KeySlot]:
        cands = self.pool.candidates(alias, self.s.strategy, net=self._net())
        if not cands:
            raise NoUpstream(f"no available upstream key for model '{alias}'")
        return cands[: max(1, self.s.retry + 1)]

    def _net(self):
        """The plane only influences ranking when it is enabled *and* smart."""
        return self.net if getattr(self.net, "smart", False) else None

    def pick_egress(self, provider_name: str,
                    force: str = "") -> tuple[str, httpx.AsyncClient]:
        """(egress id, client) for a provider - ("direct", st.http) by default.

        `force` names one path and pins the call to it: that is how a probe
        measures "can this model be reached over *this* egress" instead of
        whatever the plane happens to consider best right now.
        """
        if force and force != DIRECT:
            if self.egress is not None:
                return force, self.egress.client_for(force)
            log.debug("cannot force egress '%s': no egress registry", force)
            return DIRECT, self.http
        if force == DIRECT:
            return DIRECT, self.http
        if self.net is None or not getattr(self.net.cfg, "enabled", False):
            return DIRECT, self.http
        try:
            eid = self.net.pick(provider_name)
        except Exception as e:                            # noqa: BLE001
            log.warning("net plane pick failed: %s", e)
            return DIRECT, self.http
        if self.egress is None or eid == DIRECT:
            return DIRECT, self.http
        return eid, self.egress.client_for(eid)

    def _request(self, slot: KeySlot, alias: str, payload: dict, stream: bool,
                 client: httpx.AsyncClient | None = None) -> httpx.Request:
        p = slot.provider
        body = up.build_payload(p, payload, self.pool.upstream_model(slot, alias))
        if stream and p.style != "anthropic":
            body["stream"] = True
            body.setdefault("stream_options", {"include_usage": True})
        return (client or self.http).build_request(
            "POST", up.chat_url(p), headers=up.headers(p, slot.key),
            json=body, timeout=p.timeout)


    def _account(self, slot: KeySlot, alias: str, status: int, t0: float,
                 usage: dict | None, stream: int, error: str = "", upstream: str = "",
                 egress: str = "") -> None:
        u = usage or {}
        cd = self.s.cost_detail(alias, int(u.get("prompt_tokens", 0) or 0),
                                int(u.get("completion_tokens", 0) or 0))
        self.usage.log(ts=time.time(), alias=alias, upstream=upstream or alias,
                       provider=slot.provider.name, key=slot.label.split("/")[-1],
                       status=status, ms=round((time.time() - t0) * 1000, 1),
                       prompt=int(u.get("prompt_tokens", 0) or 0),
                       completion=int(u.get("completion_tokens", 0) or 0),
                       total=int(u.get("total_tokens", 0) or 0),
                       stream=stream, error=error[:300],
                       cost=cd["amount"], cost_currency=cd["currency"],
                       cost_display=cd["display"],
                       display_currency=cd["display_currency"], egress=egress)
        a = getattr(self, "assessor", None)
        if a is not None:
            # "daily conversation" evaluation: real traffic is the sample, so
            # measuring what users feel costs no extra upstream call.
            a.observe(alias=alias, provider=slot.provider.name, egress=egress,
                      ms=round((time.time() - t0) * 1000, 1), status=status,
                      usage=u, stream=stream, error=error)

    async def embeddings(self, payload: dict, egress_out: dict | None = None) -> dict:
        # OpenAI-compatible /v1/embeddings, same pool + failover rules as chat.
        alias = str(payload.get("model") or "")
        cands = self.pool.candidates(alias, self.s.strategy, embed=True, net=self._net())
        if not cands:
            raise NoUpstream(f"no embedding upstream for model '{alias}'")
        last: UpstreamError | None = None
        for slot in cands[: max(1, self.s.retry + 1)]:
            t0 = time.time()
            up_model = self.pool.upstream_model(slot, alias, embed=True)
            lease = await self.gate.acquire(alias, slot.provider.name)
            eid, client = self.pick_egress(slot.provider.name)
            if egress_out is not None:
                egress_out["egress"] = eid
            req = client.build_request("POST", up.embed_url(slot.provider),
                                       headers=up.headers(slot.provider, slot.key),
                                       json=dict(payload, model=up_model),
                                       timeout=slot.provider.timeout)
            try:
                r = await client.send(req)
                lease.release(error=r.status_code >= 400)
            except httpx.RequestError as e:
                lease.release(error=True)
                slot.note_fail(self.s.cooldown)
                last = UpstreamError(502, f"{slot.label}: {e}", slot)
                continue
            if r.status_code >= 400:
                slot.note_fail(self.s.cooldown)
                detail = _safe_detail(r)
                last = UpstreamError(r.status_code, detail, slot)
                self._account(slot, alias, r.status_code, t0, None, 0, str(detail),
                              egress=eid)
                continue
            data = r.json()
            slot.note_ok()
            data["model"] = alias
            self._account(slot, alias, 200, t0, data.get("usage"), 0, upstream=up_model,
                          egress=eid)
            return data
        raise last or UpstreamError(502, "all embedding upstreams failed")

    async def complete(self, payload: dict, egress_out: dict | None = None,
                       force_egress: str = "") -> dict:
        alias = str(payload.get("model") or "")
        last: UpstreamError | None = None
        for slot in self.plan(alias):
            t0 = time.time()
            lease = await self.gate.acquire(alias, slot.provider.name)
            eid, client = self.pick_egress(slot.provider.name, force_egress)
            if egress_out is not None:
                egress_out["egress"] = eid
            try:
                r = await client.send(self._request(slot, alias, payload, False, client))
                lease.release(error=r.status_code >= 400)
                if r.status_code >= 400:
                    slot.note_fail(self.s.cooldown)
                    detail = _safe_detail(r)
                    if r.status_code not in RETRY_STATUS:
                        raise UpstreamError(r.status_code, detail, slot)
                    last = UpstreamError(502, f"{slot.label}: {detail}", slot,
                                         upstream_status=r.status_code)
                    self._account(slot, alias, r.status_code, t0, None, 0, str(detail),
                                  egress=eid)
                    log.warning("failover from %s (%s)", slot.label, detail)
                    continue
                body = r.json()
                if slot.provider.style == "anthropic":
                    body = up.from_anthropic(body, alias)
                slot.note_ok()
                body["model"] = alias
                self._account(slot, alias, r.status_code, t0, body.get("usage"), 0,
                              upstream=self.pool.upstream_model(slot, alias), egress=eid)
                return body
            except (httpx.RequestError, ValueError) as e:
                lease.release(error=True)
                slot.note_fail(self.s.cooldown)
                last = UpstreamError(502, f"{slot.label}: {type(e).__name__}: {e}", slot)
                self._account(slot, alias, 502, t0, None, 0, str(e), egress=eid)
                log.warning("upstream %s error: %s", slot.label, e)
        raise last or UpstreamError(502, "all upstreams failed")


    async def open_stream(self, payload: dict, force_egress: str = ""):
        """Try candidates until one opens a stream; returns (slot, alias, t0, response).

        The chosen egress travels on the response (`_sms_egress`), the same way
        the concurrency lease does, so the gateway can echo it as a header and
        wrap_stream() can log it without changing this tuple's shape.
        """
        alias = str(payload.get("model") or "")
        last: UpstreamError | None = None
        for slot in self.plan(alias):
            t0 = time.time()
            lease = await self.gate.acquire(alias, slot.provider.name)
            eid, client = self.pick_egress(slot.provider.name, force_egress)
            try:
                r = await client.send(self._request(slot, alias, payload, True, client),
                                      stream=True)
                if r.status_code >= 400:
                    await r.aread()
                    lease.release(error=True)
                    slot.note_fail(self.s.cooldown)
                    detail = _safe_detail(r)
                    if r.status_code not in RETRY_STATUS:
                        raise UpstreamError(r.status_code, detail, slot)
                    last = UpstreamError(502, f"{slot.label}: {detail}", slot,
                                         upstream_status=r.status_code)
                    continue
                r._sms_lease = lease          # released when the stream ends
                r._sms_egress = eid           # echoed as x-socket-egress
                return slot, alias, t0, r
            except httpx.RequestError as e:
                lease.release(error=True)
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
            lease = getattr(r, "_sms_lease", None)
            if lease is not None:
                lease.release()
            await r.aclose()
            slot.note_ok()
            self._account(slot, alias, 200, t0, usage, 1,
                          upstream=self.pool.upstream_model(slot, alias),
                          egress=getattr(r, "_sms_egress", ""))


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
