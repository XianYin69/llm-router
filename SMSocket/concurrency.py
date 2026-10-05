"""Concurrency: live in-flight meter + admission gate (asyncio, no threads).

Two separate concerns, one module:

  Meter  - counts what is happening *right now*: in-flight requests, per
           provider, queue depth, peak, wait time, rejections. Read by
           GET /concurrency and the dashboard.
  Gate   - admission control. `max_concurrency` caps simultaneous upstream
           calls; extra callers wait up to `queue_wait` seconds, then get a
           429 (SMSocketBusy) instead of piling onto the event loop.

Everything is single-loop and lock-free: asyncio tasks only switch at await
points, so the counters below are consistent without a threading.Lock. Leases
are released exactly once (idempotent) even when a stream is abandoned.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


class SMSocketBusy(Exception):
    """Gate is saturated and the caller refused to wait any longer."""

    def __init__(self, message: str, retry_after: float = 1.0):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class Meter:
    active: int = 0
    peak: int = 0
    total: int = 0
    queued: int = 0
    rejected: int = 0
    errors: int = 0
    wait_ms_sum: float = 0.0
    wait_ms_peak: float = 0.0
    dur_ms_sum: float = 0.0
    dur_ms_peak: float = 0.0
    t0: float = field(default_factory=time.time)
    by_provider: dict[str, dict] = field(default_factory=dict)

    def enter(self, provider: str) -> None:
        self.active += 1
        self.total += 1
        self.peak = max(self.peak, self.active)
        row = self.by_provider.setdefault(
            provider or "-", {"active": 0, "peak": 0, "total": 0, "errors": 0})
        row["active"] += 1
        row["total"] += 1
        row["peak"] = max(row["peak"], row["active"])

    def exit(self, provider: str, ms: float, error: bool = False) -> None:
        self.active = max(0, self.active - 1)
        row = self.by_provider.get(provider or "-")
        if row:
            row["active"] = max(0, row["active"] - 1)
            if error:
                row["errors"] += 1
        if error:
            self.errors += 1
        self.dur_ms_sum += ms
        self.dur_ms_peak = max(self.dur_ms_peak, ms)

    def note_wait(self, ms: float) -> None:
        self.wait_ms_sum += ms
        self.wait_ms_peak = max(self.wait_ms_peak, ms)

    def snapshot(self, limits: dict | None = None) -> dict:
        n = max(self.total, 1)
        d = {"active": self.active, "peak": self.peak, "total": self.total,
             "queued": self.queued, "rejected": self.rejected, "errors": self.errors,
             "avg_ms": round(self.dur_ms_sum / n, 1), "max_ms": round(self.dur_ms_peak, 1),
             "avg_wait_ms": round(self.wait_ms_sum / n, 2),
             "max_wait_ms": round(self.wait_ms_peak, 1),
             "uptime_s": round(time.time() - self.t0, 1),
             "rps": round(self.total / max(time.time() - self.t0, 1e-6), 3),
             "by_provider": {k: dict(v) for k, v in sorted(self.by_provider.items())}}
        if limits:
            d.update(limits)
        return d


@dataclass
class Lease:
    """Holds the acquired semaphore(s); release() is idempotent."""
    gate: "Gate"
    provider: str
    alias: str
    t0: float
    _done: bool = False

    def release(self, error: bool = False) -> None:
        if self._done:
            return
        self._done = True
        ms = (time.perf_counter() - self.t0) * 1000
        self.gate._release(self, ms, error)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release(bool(exc[0]))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.release(bool(exc[0]))


class Gate:
    """Admission control over the upstream call path."""

    def __init__(self, max_concurrency: int = 0, queue_wait: float = 30.0,
                 per_provider: int = 0, meter: Meter | None = None) -> None:
        self.max_concurrency = max(0, int(max_concurrency or 0))
        self.queue_wait = max(0.0, float(queue_wait or 0.0))
        self.per_provider = max(0, int(per_provider or 0))
        self.meter = meter or Meter()
        self._sem: asyncio.Semaphore | None = None
        self._prov: dict[str, asyncio.Semaphore] = {}
        self._loop = None

    # -- internals ----------------------------------------------------------
    def _global_sem(self) -> asyncio.Semaphore | None:
        if not self.max_concurrency:
            return None
        loop = asyncio.get_running_loop()
        if self._sem is None or self._loop is not loop:
            # a new event loop (uvicorn reload / TestClient) -> rebuild
            self._sem = asyncio.Semaphore(self.max_concurrency)
            self._prov = {}
            self._loop = loop
        return self._sem

    def _provider_sem(self, provider: str) -> asyncio.Semaphore | None:
        if not self.per_provider or not provider:
            return None
        sem = self._prov.get(provider)
        if sem is None:
            sem = self._prov[provider] = asyncio.Semaphore(self.per_provider)
        return sem

    def _release(self, lease: Lease, ms: float, error: bool) -> None:
        self.meter.exit(lease.provider, ms, error)
        if lease.provider and lease.provider in self._prov:
            self._prov[lease.provider].release()
        if self._sem is not None:
            try:
                self._sem.release()
            except ValueError:      # released after a loop swap
                pass

    # -- public -------------------------------------------------------------
    async def acquire(self, alias: str = "", provider: str = "") -> Lease:
        """Wait for admission. Raises SMSocketBusy when the queue is too slow."""
        sem = self._global_sem()
        m = self.meter
        t0 = time.perf_counter()
        queued_here = sem is not None and sem.locked()
        if queued_here:
            m.queued += 1
        try:
            if sem is not None:
                if self.queue_wait <= 0:
                    if sem.locked():
                        m.rejected += 1
                        raise SMSocketBusy(
                            f"gateway saturated ({self.max_concurrency} in flight)", 0.5)
                    await sem.acquire()
                else:
                    try:
                        await asyncio.wait_for(sem.acquire(), self.queue_wait)
                    except asyncio.TimeoutError:
                        m.rejected += 1
                        raise SMSocketBusy(
                            f"queue wait exceeded {self.queue_wait}s "
                            f"({self.max_concurrency} in flight, {m.queued} queued)",
                            max(0.5, min(self.queue_wait, 5.0)))
            psem = self._provider_sem(provider)
            if psem is not None:
                try:
                    if self.queue_wait > 0:
                        await asyncio.wait_for(psem.acquire(), self.queue_wait)
                    else:
                        await psem.acquire()
                except asyncio.TimeoutError:
                    if sem is not None:
                        sem.release()
                    m.rejected += 1
                    raise SMSocketBusy(f"provider '{provider}' saturated "
                                       f"({self.per_provider} in flight)", 1.0)
        finally:
            if queued_here:
                m.queued = max(0, m.queued - 1)
        wait_ms = (time.perf_counter() - t0) * 1000
        m.note_wait(wait_ms)
        m.enter(provider)
        return Lease(self, provider, alias, time.perf_counter())

    def limits(self) -> dict:
        return {"max_concurrency": self.max_concurrency or None,
                "per_provider_concurrency": self.per_provider or None,
                "queue_wait": self.queue_wait,
                "saturated": bool(self.max_concurrency and
                                  self.meter.active >= self.max_concurrency)}
