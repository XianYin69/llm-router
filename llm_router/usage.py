"""SQLite usage log (per-call records + aggregates for the dashboard)."""
from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """CREATE TABLE IF NOT EXISTS calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, alias TEXT, upstream TEXT, provider TEXT, key TEXT,
  status INTEGER, ms REAL, prompt INTEGER, completion INTEGER,
  total INTEGER, stream INTEGER, error TEXT)"""


class Usage:
    def __init__(self, path: str = "usage.sqlite3") -> None:
        self._lock = threading.Lock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute(SCHEMA)
        self.con.commit()

    def log(self, **kw) -> None:
        row = {"ts": time.time(), "alias": "", "upstream": "", "provider": "",
               "key": "", "status": 0, "ms": 0.0, "prompt": 0, "completion": 0,
               "total": 0, "stream": 0, "error": ""}
        row.update(kw)
        cols = ",".join(row)
        ph = ",".join("?" * len(row))
        with self._lock:
            self.con.execute(f"INSERT INTO calls({cols}) VALUES({ph})", list(row.values()))
            self.con.commit()

    def recent(self, n: int = 50) -> list[dict]:
        cur = self.con.execute("SELECT * FROM calls ORDER BY id DESC LIMIT ?", (n,))
        return [dict(r) for r in cur.fetchall()]

    def summary(self) -> dict:
        q = ("SELECT provider, count(*) calls, sum(total) tokens, "
             "round(avg(ms),1) avg_ms, sum(status>=400) errors FROM calls "
             "GROUP BY provider ORDER BY calls DESC")
        by_model = ("SELECT alias, count(*) calls, sum(total) tokens FROM calls "
                    "GROUP BY alias ORDER BY calls DESC")
        return {"providers": [dict(r) for r in self.con.execute(q)],
                "models": [dict(r) for r in self.con.execute(by_model)],
                "total": self.con.execute("SELECT count(*) c, sum(total) t FROM calls")
                        .fetchone()}

    def close(self) -> None:
        with self._lock:
            self.con.close()
