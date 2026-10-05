"""Upstream adapters: request/response translation for openai-compatible & anthropic."""
from __future__ import annotations

import json
import time
import uuid

from .config import ProviderSpec

ANTHROPIC_VERSION = "2023-06-01"


def chat_url(p: ProviderSpec) -> str:
    if p.style == "anthropic":
        return p.base_url.rstrip("/") + "/messages" if p.base_url.rstrip("/").endswith("/v1") \
            else p.base_url.rstrip("/") + "/v1/messages"
    base = p.base_url.rstrip("/")
    return base + "/chat/completions" if base.endswith("/v1") else base + "/v1/chat/completions"


def embed_url(p: ProviderSpec) -> str:
    base = p.base_url.rstrip("/")
    return base + "/embeddings" if base.endswith("/v1") else base + "/v1/embeddings"
def headers(p: ProviderSpec, key: str) -> dict:
    if p.style == "anthropic":
        h = {"content-type": "application/json", "x-api-key": key,
             "anthropic-version": ANTHROPIC_VERSION}
    else:
        h = {"content-type": "application/json", "authorization": f"Bearer {key}"}
    h.update(p.extra_headers)
    return h


def build_payload(p: ProviderSpec, payload: dict, upstream_model: str) -> dict:
    body = dict(payload)
    body["model"] = upstream_model
    if p.style != "anthropic":
        return body
    msgs = body.pop("messages", []) or []
    sys_txt = "\n".join(m.get("content", "") for m in msgs if m.get("role") == "system")
    out = {"model": upstream_model,
           "messages": [m for m in msgs if m.get("role") != "system"],
           "max_tokens": int(body.pop("max_tokens", 0) or body.pop("max_completion_tokens", 0) or 1024)}
    if sys_txt:
        out["system"] = sys_txt
    for k in ("temperature", "top_p", "stream", "stop_sequences"):
        if body.get(k) is not None:
            out[k] = body[k]
    if "stop" in body and "stop_sequences" not in out:
        out["stop_sequences"] = body["stop"]
    return out


def _stop_reason(r) -> str:
    return {"end_turn": "stop", "max_tokens": "length",
            "stop_sequence": "stop", "tool_use": "tool_calls"}.get(r, "stop")


def from_anthropic(data: dict, alias: str) -> dict:
    u = data.get("usage", {}) or {}
    pin, pout = int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0)
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return {"id": "chatcmpl-" + str(data.get("id", uuid.uuid4().hex[:12])),
            "object": "chat.completion", "created": int(time.time()), "model": alias,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": _stop_reason(data.get("stop_reason"))}],
            "usage": {"prompt_tokens": pin, "completion_tokens": pout, "total_tokens": pin + pout}}


def sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


def _chunk(cid, created, alias, delta, finish=None, usage=None) -> dict:
    c = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": alias,
         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        c["usage"] = usage
    return c


async def anthropic_stream_to_openai(chunks, alias: str):
    """Translate Anthropic SSE into OpenAI chat.completion.chunk dicts."""
    cid, created = "chatcmpl-" + uuid.uuid4().hex[:12], int(time.time())
    buf, pin, pout, finish = b"", 0, 0, None
    yield _chunk(cid, created, alias, {"role": "assistant", "content": ""})
    async for raw in chunks:
        buf += raw
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            s = line.decode("utf-8", "ignore").strip()
            if not s.startswith("data:"):
                continue
            try:
                ev = json.loads(s[5:].strip())
            except Exception:
                continue
            t = ev.get("type")
            if t == "message_start":
                pin = int((ev.get("message", {}).get("usage") or {}).get("input_tokens", 0) or 0)
            elif t == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    yield _chunk(cid, created, alias, {"content": d["text"]})
            elif t == "message_delta":
                pout = int((ev.get("usage") or {}).get("output_tokens", pout) or 0)
                finish = _stop_reason((ev.get("delta") or {}).get("stop_reason"))
            elif t == "message_stop":
                yield _chunk(cid, created, alias, {}, finish,
                             {"prompt_tokens": pin, "completion_tokens": pout,
                              "total_tokens": pin + pout})
