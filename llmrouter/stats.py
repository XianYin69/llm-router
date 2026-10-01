"""In-memory usage counters exposed at /stats."""
from __future__ import annotations

import threading
import time
from collections import defaultdict


class Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.req: dict = defaultdict(int)
        self.err: dict = defaultdict(int)
        self.tok_in: dict = defaultdict(int)
        self.tok_out: dict = defaultdict(int)
        self.lat: dict = defaultdict(float)
        self.started = time.time()

    def record(self, key: str, ok: bool, seconds: float,
               pin: int = 0, pout: int = 0) -> None:
        with self._lock:
            self.req[key] += 1
            if not ok:
                self.err[key] += 1
            self.lat[key] += seconds
            self.tok_in[key] += pin
            self.tok_out[key] += pout

    def snapshot(self) -> dict:
        with self._lock:
            out = {}
            for k, n in self.req.items():
                out[k] = {"requests": n, "errors": self.err[k],
                          "avg_ms": round(1000 * self.lat[k] / n, 1) if n else 0,
                          "prompt_tokens": self.tok_in[k],
                          "completion_tokens": self.tok_out[k]}
            return {"uptime_s": round(time.time() - self.started), "keys": out}
