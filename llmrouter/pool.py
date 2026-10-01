"""Round-robin API key pool with per-key cooldown."""
from __future__ import annotations

import itertools
import threading
import time


class KeyPool:
    def __init__(self) -> None:
        self._pools: dict[str, list[str]] = {}
        self._rr: dict[str, itertools.cycle] = {}
        self._cool: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def register(self, provider: str, keys: list[str]) -> None:
        with self._lock:
            self._pools[provider] = list(keys)
            self._rr[provider] = itertools.cycle(keys) if keys else None

    def pick(self, provider: str) -> str | None:
        """Return the next non-cooling key (round-robin)."""
        with self._lock:
            keys = self._pools.get(provider) or []
            if not keys:
                return None
            now = time.time()
            for _ in range(len(keys)):
                k = next(self._rr[provider])
                if self._cool.get((provider, k), 0.0) <= now:
                    return k
            return min(keys, key=lambda k: self._cool.get((provider, k), 0.0))


    def cooldown(self, provider: str, key: str, seconds: float) -> None:
        with self._lock:
            self._cool[(provider, key)] = time.time() + max(0.0, seconds)

    def release(self, provider: str, key: str) -> None:
        with self._lock:
            self._cool.pop((provider, key), None)

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            return {p: {"total": len(ks),
                        "cooling": sum(1 for k in ks
                                       if self._cool.get((p, k), 0.0) > now)}
                    for p, ks in self._pools.items()}
