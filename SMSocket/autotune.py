"""AutoTuner: weight and priority come from measurement, not from typing.

Two inputs, one ranking:

  live   every real call the gateway served (mirrored by the assessor) -
         this is "the parameters of the conversations users actually have"
  probe  the scheduled sweep - this is "the parameters of the conversations
         we start on a timer"

Both land in the `assess` table, so a score is a statement about observed
behaviour, never about a number someone remembered to edit.

score = ok_ratio * 0.55 + speed * 0.30 + throughput * 0.15
  speed / throughput are min-max normalised inside the same window, so the
  ranking compares providers against each other, not against a constant.
  A provider with fewer than `min_samples` observations is pulled toward the
  middle in proportion to how little we know about it (shrinkage), so one lucky
  call cannot promote a provider over a thousand honest ones.

The result is written into Pool.derived (in force immediately). The config file
keeps the operator's own numbers unless `tune.persist: true`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

log = logging.getLogger("smssocket")


@dataclass
class Score:
    provider: str
    samples: int = 0
    ok_ratio: float = 0.0
    p50_ms: float = 0.0
    tok_s: float = 0.0
    priority: int = 0
    weight: int = 1
    note: str = ""
    score: float = 0.0

    def as_dict(self) -> dict:
        return {"provider": self.provider, "samples": self.samples,
                "ok_ratio": round(self.ok_ratio, 3), "p50_ms": round(self.p50_ms, 1),
                "tok_s": round(self.tok_s, 2), "priority": self.priority,
                "weight": self.weight, "note": self.note}


def _p50(vals: list[float]) -> float:
    if not vals:
        return 0.0
    v = sorted(vals)
    return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2.0


class AutoTuner:
    """Ranks providers from measured traffic and hands the numbers to the pool."""

    def __init__(self, settings, usage, pool) -> None:
        self.s = settings
        self.cfg = settings.tune
        self.usage = usage
        self.pool = pool
        self.last_run: float = 0.0
        self.next_run: float = 0.0
        self.runs: int = 0
        self.scores: list[Score] = []
        self.last_error: str = ""
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled)

    # -- measurement --------------------------------------------------------
    def compute(self) -> list[Score]:
        """Aggregate the assess window into one Score per provider."""
        rows = self.usage.assess_rows(window_s=self.cfg.window_s) or []
        by: dict[str, dict] = {}
        for r in rows:
            name = str(r.get("provider") or "")
            if not name:
                continue
            b = by.setdefault(name, {"n": 0, "ok": 0, "lat": [], "tps": []})
            b["n"] += 1
            if int(r.get("ok") or 0):
                b["ok"] += 1
                lat = float(r.get("latency_ms") or 0.0)
                if lat > 0:
                    b["lat"].append(lat)
                tps = float(r.get("tok_s") or 0.0)
                if tps > 0:
                    b["tps"].append(tps)
        provs = [p for p in self.s.providers if getattr(p, "enabled", True)]
        out: list[Score] = []
        for p in provs:
            if not getattr(p, "auto", True):
                out.append(Score(provider=p.name, note="manual"))
                continue
            b = by.get(p.name)
            if not b or not b["n"]:
                out.append(Score(provider=p.name, note="no-data"))
                continue
            out.append(Score(provider=p.name, samples=b["n"],
                             ok_ratio=b["ok"] / b["n"],
                             p50_ms=_p50(b["lat"]),
                             tok_s=(sum(b["tps"]) / len(b["tps"]) if b["tps"] else 0.0)))
        self._rank(out)
        return out

    def _rank(self, scores: list[Score]) -> None:
        """Normalise, shrink, sort, then assign priority / weight."""
        cfg = self.cfg
        live = [x for x in scores if x.samples > 0]
        lats = [x.p50_ms for x in live if x.p50_ms > 0]
        tpss = [x.tok_s for x in live if x.tok_s > 0]
        lo_lat, hi_lat = (min(lats), max(lats)) if lats else (0.0, 0.0)
        lo_tp, hi_tp = (min(tpss), max(tpss)) if tpss else (0.0, 0.0)

        def norm(v: float, lo: float, hi: float, invert: bool = False) -> float:
            if hi <= lo:
                return 1.0 if v > 0 else 0.0
            f = (hi - v) / (hi - lo) if invert else (v - lo) / (hi - lo)
            return max(0.0, min(1.0, f))

        for x in live:
            speed = norm(x.p50_ms, lo_lat, hi_lat, invert=True) if x.p50_ms else 0.5
            tps = norm(x.tok_s, lo_tp, hi_tp) if x.tok_s else 0.5
            raw = x.ok_ratio * 0.55 + speed * 0.30 + tps * 0.15
            # shrink toward 0.5 while evidence is thin
            k = min(1.0, x.samples / max(1, cfg.min_samples))
            x.score = 0.5 + (raw - 0.5) * k
        for x in scores:
            if x.samples == 0 and not getattr(x, "score", None):
                x.score = 0.0
        ordered = sorted([x for x in scores if getattr(x, "score", 0.0) > 0],
                         key=lambda x: -x.score)
        rest = [x for x in scores if getattr(x, "score", 0.0) <= 0]
        for rank, x in enumerate(ordered):
            x.priority = max(0, cfg.priority_spread - rank)
            x.weight = max(1, cfg.weight_spread - rank)
        for x in rest:
            x.priority, x.weight = 0, 1
        self.scores = ordered + rest

    # -- effect -------------------------------------------------------------
    def apply(self) -> dict:
        """Recompute and push the ranking into the live pool."""
        if not self.enabled:
            return {"applied": 0, "scores": [], "note": "tuning disabled"}
        try:
            scores = self.compute()
        except Exception as e:                        # noqa: BLE001
            self.last_error = str(e)[:200]
            log.warning("autotune compute failed: %s", e)
            return {"applied": 0, "scores": [], "error": self.last_error}
        self.last_run = time.time()
        self.runs += 1
        mapping = {x.provider: {"priority": x.priority, "weight": x.weight}
                   for x in scores if x.samples > 0}
        try:
            self.pool.set_derived(mapping)
        except Exception as e:                        # noqa: BLE001
            log.warning("autotune could not update the pool: %s", e)
        persisted = 0
        if self.cfg.persist and mapping:
            persisted = self._persist(mapping)
        out = {"applied": len(mapping), "persisted": persisted,
               "window_s": self.cfg.window_s, "at": round(self.last_run, 1),
               "scores": [x.as_dict() for x in scores]}
        return out

    def _persist(self, mapping: dict) -> int:
        """Optionally write the derived numbers back into the config file."""
        try:
            from .admin import cfg_path, read_raw, write_raw
            path = cfg_path()
            data = read_raw(path)
            n = 0
            for item in data.get("providers") or []:
                if not isinstance(item, dict):
                    continue
                d = mapping.get(str(item.get("name")))
                if not d:
                    continue
                if int(item.get("priority", -1)) != d["priority"] \
                        or int(item.get("weight", -1)) != d["weight"]:
                    item["priority"] = d["priority"]
                    item["weight"] = d["weight"]
                    n += 1
            if n:
                write_raw(path, data)
            return n
        except Exception as e:                        # noqa: BLE001
            log.warning("autotune persist failed: %s", e)
            return 0

    def status(self) -> dict:
        return {"enabled": self.enabled, "runs": self.runs,
                "last_run": round(self.last_run, 1),
                "next_run": round(self.next_run, 1) if self.next_run else 0.0,
                "interval_s": self.cfg.interval_s, "window_s": self.cfg.window_s,
                "min_samples": self.cfg.min_samples, "persist": self.cfg.persist,
                "in_force": dict(self.pool.derived) if self.pool else {},
                "scores": [x.as_dict() for x in self.scores],
                "error": self.last_error}

    # -- scheduling ---------------------------------------------------------
    def next_slot(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        if not self.enabled:
            return 0.0
        return now + max(30.0, float(self.cfg.interval_s))

    async def loop(self) -> None:
        while True:
            self.next_run = self.next_slot()
            try:
                await asyncio.sleep(max(0.5, self.next_run - time.time()))
            except asyncio.CancelledError:
                raise
            try:
                self.apply()
            except asyncio.CancelledError:
                raise
            except Exception as e:                    # noqa: BLE001
                log.warning("scheduled tune failed: %s", e)
                await asyncio.sleep(1.0)

    def start(self) -> None:
        if not self.enabled or (self._task is not None and not self._task.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(self.loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
