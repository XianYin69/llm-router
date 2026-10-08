"""Model + reachability assessment: harvest live traffic, probe on a schedule.

Two sources, one table (`assess` in usage.py):

  live   every real chat call the gateway serves is mirrored here - free,
         no extra upstream traffic, and it reflects what users actually feel.
  probe  a scheduled sweep that really calls each (model x egress path) pair
         with a one-word prompt, so we learn things live traffic never tells
         us: is this model reachable over *this* network path at all?

Verdicts are deliberately coarse (healthy / slow / blocked / unstable) because
they drive an operator decision - reroute, disable the alias, or leave it - and
a number that cannot be acted on is decoration.

Everything here is optional: with `assess.enabled: false` no Assessor exists,
no rows are written and no background task runs.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

import httpx

from .clash import DIRECT
from .config import AssessConfig
from .router import NoUpstream, UpstreamError

log = logging.getLogger("smssocket")

VERDICTS = ("healthy", "slow", "blocked", "unstable", "unknown")
DAY_S = 86400.0


def _sniff_usage(chunk: bytes, cur: dict) -> dict:
    """Pull `usage` out of one SSE chunk (OpenAI sends it on the last event)."""
    for line in chunk.split(b"\n"):
        s = line.strip()
        if s.startswith(b"data:") and b"usage" in s:
            try:
                import json
                u = json.loads(s[5:].decode("utf-8", "ignore")).get("usage")
                if u:
                    return u
            except Exception:                               # noqa: BLE001
                pass
    return cur


@dataclass
class Probe:
    """One (model x egress) measurement, before it becomes a db row."""
    model: str
    egress: str
    provider: str = ""
    ok: int = 0
    status: int = 0
    latency_ms: float = 0.0
    ttft_ms: float = 0.0
    tok_s: float = 0.0
    prompt: int = 0
    completion: int = 0
    error: str = ""

    def as_row(self) -> dict:
        return {"model": self.model, "egress": self.egress, "provider": self.provider,
                "source": "probe", "ok": self.ok, "status": self.status,
                "latency_ms": round(self.latency_ms, 1),
                "ttft_ms": round(self.ttft_ms, 1), "tok_s": round(self.tok_s, 2),
                "prompt": self.prompt, "completion": self.completion,
                "error": self.error[:300]}


class Assessor:
    """Measures model performance and per-path reachability.

    `router` is the live Router, so a probe rotates keys, respects cooldown and
    lands in the normal usage table too - the assessment is a *reader* of the
    routing machinery, never a second implementation of it.
    """

    def __init__(self, settings, router, net, usage, rng: random.Random | None = None):
        self.s = settings
        self.cfg: AssessConfig = settings.assess
        self.router = router
        self.net = net
        self.usage = usage
        self.rng = rng or random.Random()
        self.progress: dict = {}
        self.last_run: float = 0.0
        self.next_run: float = 0.0
        self.runs: int = 0
        self._task: asyncio.Task | None = None
        self._probing: int = 0          # suppress live mirroring during a probe
        self._running: bool = False
        self.tuner = None          # autotune.AutoTuner, set by State.build_tuner

    # -- surface ------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled)

    def as_config(self) -> dict:
        out = self.cfg.as_config()
        out["prompt"] = self.cfg.prompt[:60]
        return out

    def models(self) -> list[str]:
        """Aliases to measure: the configured list, or everything routable."""
        if self.cfg.models:
            return list(self.cfg.models)
        idx = self.s.model_index()
        names = sorted(idx)
        if self.cfg.providers:
            want = set(self.cfg.providers)
            names = [a for a in names
                     if any(p.name in want for p in idx.get(a, []))]
        return names

    def egress_paths(self) -> list[str]:
        """Paths to measure. `auto` = direct plus every registered clash path."""
        mode = (self.cfg.egress or "auto").strip()
        if mode and mode != "auto":
            return [mode]
        paths = [DIRECT]
        registry = getattr(self.net, "registry", None) if self.net is not None else None
        if registry is not None:
            for p in registry.paths():
                if p not in paths:
                    paths.append(p)
        return paths

    # -- one measurement ----------------------------------------------------
    async def probe_once(self, alias: str, egress: str = DIRECT,
                         timeout: float | None = None) -> dict:
        """Call one model over one egress path and record what happened.

        Streamed on purpose: a stream tells us time-to-first-byte, which is the
        difference between "fast" and "feels slow" that a non-streaming probe
        cannot see.
        """
        cfg = self.cfg
        limit = float(cfg.timeout if timeout is None else timeout)
        payload = {"model": alias,
                   "messages": [{"role": "user", "content": cfg.prompt}],
                   "max_tokens": int(cfg.max_tokens)}
        probe = Probe(model=alias, egress=egress or DIRECT)
        self._probing += 1
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(self._stream(probe, payload, egress), limit)
        except asyncio.TimeoutError:
            probe.status, probe.error = 0, f"timeout after {limit:.0f}s"
        except NoUpstream as e:
            probe.status, probe.error = 0, f"no upstream: {e}"
        except UpstreamError as e:
            # a failover is reported to clients as 502; for a verdict we want
            # the answer the provider really gave (403 blocked != 502 flaky)
            probe.status = int(getattr(e, "upstream_status", 0) or e.status or 0)
            probe.error = str(e.detail if e.detail is not None else e)
            if getattr(e, "slot", None) is not None:
                probe.provider = e.slot.provider.name
        except httpx.RequestError as e:
            probe.error = f"{type(e).__name__}: {e}"
        except Exception as e:                                # noqa: BLE001
            probe.status, probe.error = 0, f"{type(e).__name__}: {e}"
        finally:
            self._probing -= 1
            probe.latency_ms = probe.latency_ms or (time.perf_counter() - t0) * 1000
        row = probe.as_row()
        self.usage.assess_log(**row)
        return row

    async def _stream(self, probe: Probe, payload: dict, egress: str) -> None:
        """Open a stream through the router, watch first byte + final usage."""
        slot, alias, t_open, resp = await self.router.open_stream(
            payload, force_egress=egress)
        probe.provider = slot.provider.name
        t0 = time.perf_counter()
        usage: dict = {}
        try:
            async for chunk in self.router.wrap_stream(slot, alias, t_open, resp):
                if not probe.ttft_ms:
                    probe.ttft_ms = (time.perf_counter() - t0) * 1000
                usage = _sniff_usage(chunk, usage)
        finally:
            probe.latency_ms = (time.perf_counter() - t0) * 1000
        u = usage or {}
        probe.prompt = int(u.get("prompt_tokens", 0) or 0)
        probe.completion = int(u.get("completion_tokens", 0) or 0)
        gen_s = max(0.001, (probe.latency_ms - probe.ttft_ms) / 1000.0)
        probe.tok_s = probe.completion / gen_s if probe.completion else 0.0
        probe.ok, probe.status = 1, 200

    # -- a whole matrix -----------------------------------------------------
    async def sweep(self, models: list[str] | None = None,
                    egress: list[str] | None = None,
                    concurrency: int | None = None) -> dict:
        """Probe every (model x egress) pair, bounded, and return a summary."""
        if self._running:
            return {"skipped": True, "reason": "a sweep is already running",
                    **self.progress}
        mods = [m for m in (models or self.models()) if m]
        paths = [e or DIRECT for e in (egress or self.egress_paths())]
        conc = max(1, min(int(concurrency or self.cfg.concurrency), 64))
        if not mods:
            return {"skipped": True, "reason": "no models to assess",
                    "models": [], "egress": paths}
        self._running = True
        self.last_run = time.time()
        self.runs += 1
        sem = asyncio.Semaphore(conc)
        total = len(mods) * len(paths)
        self.progress = {"started": time.time(), "finished": 0.0, "total": total,
                         "done": 0, "ok": 0, "failed": 0, "status": "running",
                         "models": mods, "egress": paths, "rows": []}
        log.info("assess sweep: %d models x %d paths (concurrency %d)",
                 len(mods), len(paths), conc)

        async def one(alias: str, egress_id: str) -> None:
            async with sem:
                row = await self.probe_once(alias, egress_id)
            self.progress["done"] += 1
            if row["ok"]:
                self.progress["ok"] += 1
            else:
                self.progress["failed"] += 1
            self.progress["rows"].append(row)

        try:
            await asyncio.gather(*[asyncio.create_task(one(a, e))
                                   for a in mods for e in paths])
        finally:
            self._running = False
            self.progress["status"] = "done"
            self.progress["finished"] = time.time()
        # the sweep just ended, so this is the freshest evidence we will have:
        # hand it straight to the tuner instead of waiting for the next timer
        t = getattr(self, "tuner", None)
        if t is not None:
            try:
                t.apply()
            except Exception as e:                            # noqa: BLE001
                log.warning("autotune after sweep failed: %s", e)
        return dict(self.progress)

    def sweep_now(self, models=None, egress=None, concurrency=None) -> asyncio.Task:
        """Fire-and-forget sweep for POST /assess/run?async=1."""
        return asyncio.create_task(self.sweep(models, egress, concurrency))

    # -- harvesting real traffic --------------------------------------------
    def observe(self, alias: str = "", provider: str = "", egress: str = "",
                ms: float = 0.0, status: int = 0, usage: dict | None = None,
                stream: int = 0, error: str = "") -> None:
        """Mirror one served call into the assess table (no extra traffic).

        Skipped while a probe is in flight (that probe writes its own, richer
        row) and by the sampling rate, so a busy gateway can keep the table
        small without losing the shape of the distribution.
        """
        if not self.cfg.live or self._probing or not self.enabled:
            return
        if self.cfg.live_sample < 1.0 and self.rng.random() > self.cfg.live_sample:
            return
        u = usage or {}
        prompt = int(u.get("prompt_tokens", 0) or 0)
        completion = int(u.get("completion_tokens", 0) or 0)
        gen_ms = max(1.0, float(ms))
        self.usage.assess_log(
            model=alias or "", provider=provider or "", egress=egress or DIRECT,
            source="live", ok=1 if 200 <= int(status or 0) < 300 else 0,
            status=int(status or 0), latency_ms=round(float(ms), 1),
            ttft_ms=0.0,
            tok_s=round(completion / (gen_ms / 1000.0), 2) if completion else 0.0,
            prompt=prompt, completion=completion, error=(error or "")[:300])

    # -- reading it back ----------------------------------------------------
    def report(self, window_s: float | None = None, sources=("live", "probe"),
               model: str = "") -> dict:
        """Verdicts per model x egress, plus the shape of the data behind them."""
        win = float(window_s or self.cfg.window_s)
        rows = self.usage.assess_report(window_s=win, slow_ms=self.cfg.slow_ms,
                                        sources=tuple(sources))
        if model:
            rows = [r for r in rows if r["model"] == model]
        by_egress: dict[str, dict] = {}
        for r in rows:
            cell = by_egress.setdefault(
                r["egress"], {"egress": r["egress"], "cells": 0, "blocked": 0,
                              "healthy": 0, "p50_ms": 0.0})
            cell["cells"] += 1
            if r["verdict"] == "blocked":
                cell["blocked"] += 1
            if r["verdict"] == "healthy":
                cell["healthy"] += 1
            cell["p50_ms"] = max(cell["p50_ms"], r["p50_ms"])
        return {"window_s": win, "generated": round(time.time(), 3),
                "sources": list(sources), "models": len({r["model"] for r in rows}),
                "rows": rows, "by_egress": sorted(by_egress.values(),
                                                  key=lambda d: d["egress"]),
                "counts": self.usage.assess_counts(),
                "config": self.as_config()}

    def history(self, model: str = "", days: int = 7, limit: int = 500) -> dict:
        return {"model": model or None, "days": days,
                "rows": self.usage.assess_history(model, days, limit)}

    # -- scheduling ---------------------------------------------------------
    def next_slot(self, now: float | None = None) -> float:
        """When the next scheduled sweep fires (0.0 = never)."""
        now = time.time() if now is None else now
        if not self.enabled:
            return 0.0
        if self.cfg.at:
            hh, mm = (int(x) for x in self.cfg.at.split(":"))
            lt = time.localtime(now)
            today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
            if today > now:
                return today
            return today + DAY_S
        return now + float(self.cfg.interval_s)

    async def loop(self) -> None:
        """Background scheduler: sweep on `interval_s` and/or the daily `at`."""
        while True:
            self.next_run = self.next_slot()
            wait = max(0.5, self.next_run - time.time())
            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                raise
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as e:                            # noqa: BLE001
                log.warning("scheduled sweep failed: %s", e)
                await asyncio.sleep(1.0)

    def start(self) -> None:
        """Spawn the scheduler task (lifespan). No-op when disabled."""
        if not self.enabled or (self._task is not None and not self._task.done()):
            return
        self._task = asyncio.create_task(self.loop(), name="assess-loop")
        log.info("assess loop: every %ss%s", self.cfg.interval_s,
                 f" + daily {self.cfg.at}" if self.cfg.at else "")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def status(self) -> dict:
        return {"enabled": self.enabled, "running": self._running,
                "scheduled": self._task is not None and not self._task.done(),
                "interval_s": self.cfg.interval_s, "at": self.cfg.at,
                "next_run": round(self.next_run, 3) or None,
                "last_run": round(self.last_run, 3) or None,
                "runs": self.runs, "live": self.cfg.live,
                "live_sample": self.cfg.live_sample,
                "models": self.models(), "egress": self.egress_paths(),
                "progress": dict(self.progress),
                "counts": self.usage.assess_counts()}

    def reschedule(self, patch: dict) -> dict:
        """PUT /assess/schedule: change the schedule live, then restart the loop."""
        cfg = self.cfg
        if "enabled" in patch:
            cfg.enabled = bool(patch["enabled"])
        if "interval_s" in patch:
            n = int(patch["interval_s"] or 0)
            if n < 1:
                raise ValueError("interval_s must be >= 1 second")
            cfg.interval_s = n
        if "at" in patch:
            at = str(patch["at"] or "").strip()
            if at:
                parts = at.split(":")
                if len(parts) != 2 or not all(p.isdigit() for p in parts) \
                        or not (0 <= int(parts[0]) < 24 and 0 <= int(parts[1]) < 60):
                    raise ValueError("at must be HH:MM (24h) or empty")
                at = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
            cfg.at = at
        if "models" in patch:
            m = patch["models"] or []
            cfg.models = [str(x).strip() for x in (
                [m] if isinstance(m, str) else m) if str(x).strip()]
        if "egress" in patch:
            cfg.egress = str(patch["egress"] or "auto").strip()
        if "live" in patch:
            cfg.live = bool(patch["live"])
        if "live_sample" in patch:
            r = float(patch["live_sample"] or 1.0)
            if not 0.0 <= r <= 1.0:
                raise ValueError("live_sample must be 0..1")
            cfg.live_sample = r
        return self.status()
