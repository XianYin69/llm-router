"""Thin async client for OpenAI-compatible upstreams."""
from __future__ import annotations

from typing import AsyncIterator

import httpx

from .config import Provider

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class UpstreamError(Exception):
    def __init__(self, status: int, detail: str, retryable: bool = False) -> None:
        super().__init__(f"[{status}] {detail[:200]}")
        self.status = status
        self.detail = detail
        self.retryable = retryable


def headers(provider: Provider, key: str) -> dict:
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    h.update(provider.headers or {})
    return h


async def list_models(provider: Provider, key: str) -> list[str]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{provider.base_url}/models", headers=headers(provider, key))
    if r.status_code >= 400:
        raise UpstreamError(r.status_code, r.text[:300], r.status_code in RETRY_STATUS)
    return [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]


async def chat(provider: Provider, key: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=provider.timeout) as c:
        r = await c.post(f"{provider.base_url}/chat/completions",
                         json=payload, headers=headers(provider, key))
    if r.status_code >= 400:
        raise UpstreamError(r.status_code, r.text[:400], r.status_code in RETRY_STATUS)
    return r.json()


async def chat_stream(provider: Provider, key: str,
                      payload: dict) -> AsyncIterator[str]:
    """Yield raw SSE lines ("data: {...}") from the upstream."""
    async with httpx.AsyncClient(timeout=provider.timeout) as c:
        async with c.stream("POST", f"{provider.base_url}/chat/completions",
                            json=payload, headers=headers(provider, key)) as r:
            if r.status_code >= 400:
                body = await r.aread()
                raise UpstreamError(r.status_code, body.decode("utf-8", "replace")[:400],
                                    r.status_code in RETRY_STATUS)
            async for line in r.aiter_lines():
                if line.startswith("data:"):
                    yield line
