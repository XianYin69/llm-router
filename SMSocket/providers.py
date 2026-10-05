"""Upstream key pool: rotation, health tracking, cooldown, rpm limiting."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .config import ProviderSpec


def mask(key: str) -> str:
    if len(key) <= 10:
        return (key[:2] + "***") if key else "(empty)"
    return f"{key[:5]}…{key[-4:]}"


@dataclass
class KeySlot:
    provider: ProviderSpec
    key: str
    fails: int = 0
    dead_until: float = 0.0
    ok: int = 0
    _window: list[float] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.provider.name}/{mask(self.key)}"

    def available(self, now: float) -> bool:
        if not self.key or now < self.dead_until:
            return False
        if self.provider.max_rpm:
            self._window = [t for t in self._window if now - t < 60]
            if len(self._window) >= self.provider.max_rpm:
                return False
        return True

    def rpm_headroom(self, now: float | None = None) -> int | None:
        """Requests still allowed in this 60s window (None = no rpm cap).

        Prunes the window like `available()` does, so the stack scheduler can
        ask "is this provider rate-limited right now?" without consuming a key.
        """
        if not self.provider.max_rpm:
            return None
        now = time.time() if now is None else now
        self._window = [t for t in self._window if now - t < 60]
        return max(0, self.provider.max_rpm - len(self._window))

    def touch(self) -> None:
        self._window.append(time.time())

    def note_ok(self) -> None:
        self.ok += 1
        self.fails = 0
        self.dead_until = 0.0

    def note_fail(self, cooldown: float) -> None:
        self.fails += 1
        self.dead_until = time.time() + cooldown


class Pool:
    """All key slots across providers, with selection strategies."""

    def __init__(self, providers: list[ProviderSpec]) -> None:
        self.providers = providers
        self.slots: list[KeySlot] = [
            KeySlot(p, k) for p in providers for k in p.keys]
        self._cursor = 0

    def upstream_model(self, slot: KeySlot, alias: str, embed: bool = False) -> str:
        table = slot.provider.embeddings if embed else slot.provider.models
        return table.get(alias, alias)

    def serving(self, alias: str, embed: bool = False) -> list[KeySlot]:
        out = []
        for s in self.slots:
            if not s.provider.enabled:
                continue
            names = s.provider.embeddings if embed else s.provider.aliases()
            if alias in names:
                out.append(s)
        return out

    def candidates(self, alias: str, strategy: str = "priority",
                   embed: bool = False, net=None) -> list[KeySlot]:
        """Ranked key slots for one alias.

        `net` (a clash.NetPlane) is optional: when the net plane scores in smart
        mode it re-ranks the result by measured egress quality, so a provider
        only reachable through a proxy outranks a dead direct route. Strategy
        order stays the tie-break, so nothing moves unless the network speaks.
        """
        now = time.time()
        live = [s for s in self.serving(alias, embed) if s.available(now)]
        if not live:
            return []
        if strategy == "round_robin":
            n = len(live)
            self._cursor = (self._cursor + 1) % max(n, 1)
            return self._rerank(live[self._cursor:] + live[:self._cursor], net)
        if strategy == "weighted":
            weights = [max(s.provider.weight, 1) for s in live]
            out, bag = [], list(live)
            while bag:
                pick = random.choices(bag, weights=[max(s.provider.weight, 1) for s in bag], k=1)[0]
                out.append(pick)
                bag.remove(pick)
            return self._rerank(out, net)
        # priority: high priority first, then low failure count, then weight
        return self._rerank(sorted(live, key=lambda s: (-s.provider.priority,
                                                 s.fails, -s.provider.weight)), net)

    @staticmethod
    def _rerank(slots: list[KeySlot], net) -> list[KeySlot]:
        """Stable sort by egress quality: fastest first, then least broken."""
        if net is None or not getattr(net, "smart", False):
            return slots
        return sorted(slots, key=lambda s: net.sort_key(s.provider.name))

    def rebase(self, providers) -> "Pool":
        # Rebuild slots for a new provider set but carry over observed health,
        # so /admin/reload cannot instantly un-cool a key that is failing.
        keep = {(x.provider.name, x.key): x for x in self.slots}
        fresh = Pool(providers)
        for x in fresh.slots:
            old = keep.get((x.provider.name, x.key))
            if old:
                x.fails, x.dead_until, x.ok = old.fails, old.dead_until, old.ok
        return fresh
    def stats(self) -> list[dict]:
        return [{"provider": s.provider.name, "key": mask(s.key), "ok": s.ok,
                 "fails": s.fails,
                 "cooldown_left": round(max(0.0, s.dead_until - time.time()), 1)}
                for s in self.slots]
