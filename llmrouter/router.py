"""Resolve a public model to an ordered fallback chain of upstream routes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import AsyncIterator, Tuple

from .config import Provider, Settings
from .pool import KeyPool
from .upstream import UpstreamError, chat, chat_stream


@dataclass
class Route:
    provider: Provider
    model: str

    @property
    def ref(self) -> str:
        return f"{self.provider.name}/{self.model}"


class Router:
    def __init__(self, settings: Settings, pool: KeyPool) -> None:
        self.settings = settings
        self.pool = pool

    def candidates(self, public_model: str) -> list[Route]:
        chain = self.settings.routes.get(public_model)
        if not chain:
            if "/" in public_model:
                chain = [public_model]
            else:
                chain = [f"{p.name}/{m}" for p in self.settings.providers.values()
                         for m in p.models if m == public_model]
        routes: list[Route] = []
        for ref in chain:
            name, _, model = str(ref).partition("/")
            prov = self.settings.providers.get(name)
            if prov:
                routes.append(Route(prov, model or public_model))
        routes.sort(key=lambda r: -r.provider.weight)
        return routes

    def public_models(self) -> list[str]:
        return sorted(self.settings.routes)

    def plan(self, public_model: str) -> list[Tuple[Route, str]]:
        out: list[Tuple[Route, str]] = []
        for route in self.candidates(public_model):
            n = len(route.provider.api_keys)
            if not n:
                continue
            for _ in range(min(2, n)):
                key = self.pool.pick(route.provider.name)
                if key:
                    out.append((route, key))
        return out

    def _penalise(self, route: Route, key: str, exc: UpstreamError) -> None:
        if exc.status in (401, 403, 429) or exc.retryable:
            self.pool.cooldown(route.provider.name, key, self.settings.cooldown)


    async def complete(self, public_model: str, payload: dict) -> Tuple[dict, Route]:
        attempts = self.plan(public_model)
        if not attempts:
            raise UpstreamError(404, f"no upstream available for model '{public_model}'")
        last: UpstreamError | None = None
        for route, key in attempts:
            body = dict(payload, model=route.model)
            try:
                data = await chat(route.provider, key, body)
                self.pool.release(route.provider.name, key)
                return data, route
            except UpstreamError as exc:
                last = exc
                self._penalise(route, key, exc)
        raise last or UpstreamError(502, "all upstreams failed")

    async def stream(self, public_model: str,
                     payload: dict) -> Tuple[AsyncIterator[str], Route]:
        """Open the first healthy upstream; yields SSE lines with model rewritten."""
        attempts = self.plan(public_model)
        if not attempts:
            raise UpstreamError(404, f"no upstream available for model '{public_model}'")
        last: UpstreamError | None = None
        for route, key in attempts:
            body = dict(payload, model=route.model, stream=True)
            try:
                agen = chat_stream(route.provider, key, body)
                first = await agen.__anext__()
            except UpstreamError as exc:
                last = exc
                self._penalise(route, key, exc)
                continue
            except StopAsyncIteration:
                last = UpstreamError(502, "empty stream")
                continue

            async def wrapped(g=agen, head=first, pub=public_model):
                async for line in _chain(head, g):
                    yield _rewrite(line, pub)

            return wrapped(), route
        raise last or UpstreamError(502, "all upstreams failed")


async def _chain(first: str, rest: AsyncIterator[str]) -> AsyncIterator[str]:
    yield first
    async for line in rest:
        yield line


def _rewrite(line: str, public_model: str) -> str:
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return line
    try:
        obj = json.loads(raw)
    except Exception:
        return line
    obj["model"] = public_model
    return "data: " + json.dumps(obj, ensure_ascii=False)
