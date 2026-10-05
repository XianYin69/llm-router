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

    def close(self) -> None:
        with self._lock:
            self.con.close()
