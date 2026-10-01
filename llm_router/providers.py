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

    def upstream_model(self, slot: KeySlot, alias: str) -> str:
        return slot.provider.models.get(alias, alias)

    def serving(self, alias: str) -> list[KeySlot]:
        return [s for s in self.slots if s.provider.enabled and alias in s.provider.aliases()]

    def candidates(self, alias: str, strategy: str = "priority") -> list[KeySlot]:
        now = time.time()
        live = [s for s in self.serving(alias) if s.available(now)]
        if not live:
            return []
        if strategy == "round_robin":
            n = len(live)
            self._cursor = (self._cursor + 1) % max(n, 1)
            return live[self._cursor:] + live[:self._cursor]
        if strategy == "weighted":
            weights = [max(s.provider.weight, 1) for s in live]
            out, bag = [], list(live)
            while bag:
                pick = random.choices(bag, weights=[max(s.provider.weight, 1) for s in bag], k=1)[0]
                out.append(pick)
                bag.remove(pick)
            return out
        # priority: high priority first, then low failure count, then weight
        return sorted(live, key=lambda s: (-s.provider.priority, s.fails, -s.provider.weight))

    def stats(self) -> list[dict]:
        return [{"provider": s.provider.name, "key": mask(s.key), "ok": s.ok,
                 "fails": s.fails,
                 "cooldown_left": round(max(0.0, s.dead_until - time.time()), 1)}
                for s in self.slots]
