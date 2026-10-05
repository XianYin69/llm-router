"""SQLite usage log (per-call records + aggregates for the dashboard)."""
from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """CREATE TABLE IF NOT EXISTS calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, alias TEXT, upstream TEXT, provider TEXT, key TEXT,
  status INTEGER, ms REAL, prompt INTEGER, completion INTEGER,
  total INTEGER, stream INTEGER, error TEXT, cost REAL DEFAULT 0,
  cost_currency TEXT DEFAULT "", cost_display REAL DEFAULT 0,
  display_currency TEXT DEFAULT "", egress TEXT DEFAULT "")"""

ASSESS_SCHEMA = """CREATE TABLE IF NOT EXISTS assess(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, model TEXT, provider TEXT, egress TEXT, source TEXT,
  ok INTEGER, status INTEGER, latency_ms REAL, ttft_ms REAL, tok_s REAL,
  prompt INTEGER, completion INTEGER, error TEXT)"""


def _pct(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile of an already-sorted list (0.0 when empty)."""
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return round(sorted_vals[i], 1)


def _verdict(rows: list[dict], ok_rows: list[dict], lat: list[float],
             slow_ms: float) -> str:
    """healthy | slow | blocked | unstable for one model x egress cell.

    blocked = nothing ever got through and the failures look like a wall
    (4xx / no connection at all); unstable = it works but keeps flip-flopping.
    """
    if not rows:
        return "unknown"
    if not ok_rows:
        bad = {int(r["status"] or 0) for r in rows}
        if any(400 <= s < 500 for s in bad) or bad in ({0}, set()):
            return "blocked"
        return "unstable"
    ratio = len(ok_rows) / len(rows)
    if ratio < 0.7:
        return "unstable"
    p50 = _pct(lat, 0.5)
    if p50 > slow_ms:
        return "slow"
    return "healthy"


class Usage:
    def __init__(self, path: str = "usage.sqlite3") -> None:
        self._lock = threading.Lock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute(SCHEMA)
        cols = {r["name"] for r in self.con.execute("PRAGMA table_info(calls)")}
        if "cost" not in cols:            # migrate v0.1 databases in place
            self.con.execute("ALTER TABLE calls ADD COLUMN cost REAL DEFAULT 0")
        for name, decl in (("cost_currency", "TEXT DEFAULT ''"),
                           ("cost_display", "REAL DEFAULT 0"),
                           ("display_currency", "TEXT DEFAULT ''"),
                           ("egress", "TEXT DEFAULT ''")):
            if name not in cols:      # migrate v0.2 databases in place
                self.con.execute(f"ALTER TABLE calls ADD COLUMN {name} {decl}")
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts)")
        self.con.execute(ASSESS_SCHEMA)          # v0.4: model/net assessment rows
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_assess_ts ON assess(ts)")
        self.con.commit()

    def log(self, **kw) -> None:
        row = {"ts": time.time(), "alias": "", "upstream": "", "provider": "",
               "key": "", "status": 0, "ms": 0.0, "prompt": 0, "completion": 0,
               "total": 0, "stream": 0, "error": "", "cost": 0.0,
               "cost_currency": "", "cost_display": 0.0, "display_currency": "",
               "egress": ""}
        row.update(kw)
        cols = ",".join(row)
        ph = ",".join("?" * len(row))
        with self._lock:
            self.con.execute(f"INSERT INTO calls({cols}) VALUES({ph})", list(row.values()))
            self.con.commit()

    def recent(self, n: int = 50) -> list[dict]:
        cur = self.con.execute("SELECT * FROM calls ORDER BY id DESC LIMIT ?", (n,))
        return [dict(r) for r in cur.fetchall()]

    def daily(self, days: int = 14) -> list[dict]:
        q = ("SELECT date(ts,'unixepoch') d, count(*) calls, sum(total) tokens, "
             "round(sum(cost),8) cost, round(sum(cost_display),8) display, "
             "max(display_currency) currency, sum(status>=400) errors FROM calls "
             "GROUP BY d ORDER BY d DESC LIMIT ?")
        return [dict(r) for r in self.con.execute(q, (days,))]
    def summary(self) -> dict:
        q = ("SELECT provider, count(*) calls, sum(total) tokens, "
             "round(sum(cost),6) cost, round(avg(ms),1) avg_ms, "
             "sum(status>=400) errors FROM calls "
             "GROUP BY provider ORDER BY calls DESC")
        by_model = ("SELECT alias, count(*) calls, sum(total) tokens, "
                    "round(sum(cost),6) cost FROM calls "
                    "GROUP BY alias ORDER BY calls DESC")
        return {"providers": [dict(r) for r in self.con.execute(q)],
                "models": [dict(r) for r in self.con.execute(by_model)],
                "total": self.con.execute("SELECT count(*) c, sum(total) t, "
                                         "round(sum(cost),6) cost FROM calls").fetchone()}

    def by_currency(self) -> list[dict]:
        """Spend grouped by the currency each price row was expressed in."""
        q = ("SELECT COALESCE(NULLIF(cost_currency,''),'USD') currency, count(*) calls, "
             "sum(total) tokens, round(sum(cost),8) cost, "
             "round(sum(cost_display),8) display FROM calls GROUP BY currency "
             "ORDER BY display DESC")
        return [dict(r) for r in self.con.execute(q)]

    # ---- assessment log (v0.4) ---------------------------------------------
    ASSESS_COLS = ("ts", "model", "provider", "egress", "source", "ok", "status",
                   "latency_ms", "ttft_ms", "tok_s", "prompt", "completion", "error")

    def assess_log(self, **row) -> None:
        """Append one measurement (source = 'live' | 'probe')."""
        rec = {"ts": time.time(), "model": "", "provider": "", "egress": "",
               "source": "probe", "ok": 0, "status": 0, "latency_ms": 0.0,
               "ttft_ms": 0.0, "tok_s": 0.0, "prompt": 0, "completion": 0,
               "error": ""}
        rec.update({k: v for k, v in row.items() if k in self.ASSESS_COLS})
        cols = ",".join(self.ASSESS_COLS)
        ph = ",".join("?" * len(self.ASSESS_COLS))
        with self._lock:
            self.con.execute(f"INSERT INTO assess({cols}) VALUES({ph})",
                             [rec[c] for c in self.ASSESS_COLS])
            self.con.commit()

    def assess_rows(self, window_s: float = 86400.0,
                    source: str = "") -> list[dict]:
        """Raw measurements inside the window (newest first)."""
        q = "SELECT * FROM assess WHERE ts >= ?"
        args: list = [time.time() - max(1.0, float(window_s))]
        if source:
            q += " AND source = ?"
            args.append(source)
        q += " ORDER BY ts DESC"
        return [dict(r) for r in self.con.execute(q, args)]

    def assess_report(self, window_s: float = 86400.0, slow_ms: float = 8000.0,
                      sources: tuple = ("live", "probe")) -> list[dict]:
        """Per model x egress verdicts - the answer to "is it fast, and is it
        reachable from here?". Aggregation happens in Python because sqlite has
        no percentiles and a verdict needs the whole ordered sample anyway."""
        ph = ",".join("?" * len(sources))
        q = (f"SELECT model, egress, provider, ok, status, latency_ms, ttft_ms, "
             f"tok_s, ts, error FROM assess WHERE ts >= ? AND source IN ({ph})")
        rows = [dict(r) for r in self.con.execute(
            q, [time.time() - max(1.0, float(window_s)), *sources])]
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            groups.setdefault((r["model"] or "", r["egress"] or ""), []).append(r)
        out = []
        for (model, egress), g in groups.items():
            g.sort(key=lambda r: r["ts"] or 0.0)
            ok_rows = [r for r in g if r["ok"]]
            lat = sorted(float(r["latency_ms"] or 0.0) for r in ok_rows)
            ttft = sorted(float(r["ttft_ms"] or 0.0) for r in ok_rows
                          if r["ttft_ms"])
            tps = [float(r["tok_s"]) for r in ok_rows if r["tok_s"]]
            statuses = {int(r["status"] or 0) for r in g if not r["ok"]}
            providers = sorted({r["provider"] for r in g if r["provider"]})
            out.append({
                "model": model, "egress": egress or "direct",
                "providers": providers, "calls": len(g), "ok": len(ok_rows),
                "ok_ratio": round(len(ok_rows) / len(g), 4) if g else 0.0,
                "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95),
                "avg_ms": round(sum(lat) / len(lat), 1) if lat else 0.0,
                "avg_ttft_ms": round(sum(ttft) / len(ttft), 1) if ttft else 0.0,
                "avg_tok_s": round(sum(tps) / len(tps), 2) if tps else 0.0,
                "last_seen": round(g[-1]["ts"], 3) if g else 0.0,
                "errors": sorted(statuses)[:8],
                "last_error": next((r["error"] for r in reversed(g)
                                    if r["error"]), ""),
                "verdict": _verdict(g, ok_rows, lat, slow_ms),
            })
        out.sort(key=lambda d: (d["model"], d["egress"]))
        return out

    def assess_history(self, model: str = "", days: int = 7,
                       limit: int = 500) -> list[dict]:
        """Recent measurements for one model ("" = every model)."""
        q = "SELECT * FROM assess WHERE ts >= ?"
        args: list = [time.time() - max(0.0, float(days)) * 86400.0]
        if model:
            q += " AND model = ?"
            args.append(model)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(max(1, int(limit)))
        return [dict(r) for r in self.con.execute(q, args)]

    def assess_counts(self) -> dict:
        """How much assessment data we hold (dashboard + /assess/status)."""
        r = self.con.execute(
            "SELECT count(*) n, min(ts) first, max(ts) last, "
            "sum(source='probe') probes, sum(source='live') live FROM assess").fetchone()
        return {"rows": r["n"] or 0, "first": r["first"] or 0.0,
                "last": r["last"] or 0.0, "probes": r["probes"] or 0,
                "live": r["live"] or 0}

    def assess_purge(self, keep_days: float = 90.0) -> int:
        """Drop measurements older than `keep_days` (0 = keep everything)."""
        if keep_days <= 0:
            return 0
        cut = time.time() - float(keep_days) * 86400.0
        with self._lock:
            cur = self.con.execute("DELETE FROM assess WHERE ts < ?", (cut,))
            self.con.commit()
        return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self.con.close()
