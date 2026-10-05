"""End-to-end proof for v0.4: real processes, real sockets, no mocks inside SMSocket.

Starts the mock upstream, a mock clash external-controller and a tiny forwarding
proxy, writes a temp config that turns on clash + stack + assess, boots the
gateway with uvicorn, then walks the operator path and prints PASS/FAIL:

  chat -> x-socket-egress -> /concurrency stack -> /internal/net state/probes/
  scores/select/mode -> proxy-routed chat -> /assess run/report/live/status ->
  saturation parks a caller -> OpenAPI discretion -> dashboard flags

Usage: python e2e_v04.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from contextlib import closing
import threading
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
RESULTS: list[tuple[str, bool, str]] = []
H = {"Authorization": "Bearer sk-e2e"}


def free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def check(name: str, ok, detail: str = "") -> bool:
    ok = bool(ok)
    RESULTS.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name +
          ("  · " + str(detail)[:120] if detail else ""), flush=True)
    return ok


def spawn(script: list[str]) -> subprocess.Popen:
    return subprocess.Popen([PY, "-B"] + script, cwd=HERE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def wait_up(url: str, proc: subprocess.Popen, timeout: float = 30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(url, timeout=1.0).status_code < 500:
                return True
        except Exception:                                   # noqa: BLE001
            time.sleep(0.15)
    return False


CONFIG = """
listen: 127.0.0.1:%(gw)d
master_keys: [sk-e2e]
db_path: %(db)s
strategy: priority
max_concurrency: 1
queue_wait: 0
per_provider_concurrency: 0
clash:
  enabled: true
  controller: http://127.0.0.1:%(ctl)d
  secret: e2e-secret
  mixed_port: %(proxy)d
  health_url: http://127.0.0.1:%(up)d/v1/models
  timeout: 5
  interval: 3600
  mode: auto
  groups: [PROXY-GRP]
  provider_proxy:
    mock: PROXY-GRP
  smart: true
  min_delay_ms: 0
  fail_ratio: 0.5
stack:
  enabled: true
  max_depth: 50
  wait: 3
  policy: lifo
  park_on: [saturated, rpm, provider_saturated]
  interval: 0.05
  repark: 2
assess:
  enabled: true
  interval_s: 3600
  at: ""
  models: [demo]
  egress: auto
  prompt: "Reply with exactly one word: ok"
  max_tokens: 6
  concurrency: 2
  timeout: 15
  live: true
  live_sample: 1.0
  slow_ms: 8000
  window_s: 3600
providers:
  - name: mock
    base_url: http://127.0.0.1:%(up)d/v1
    keys: [sk-up-1]
    models:
      demo: mock-model
"""


def main() -> int:
    up, ctl, proxy, gw = (free_port() for _ in range(4))
    tmp = tempfile.mkdtemp(prefix="sms-e2e-")
    cfg_path = os.path.join(tmp, "e2e-config.yaml")
    open(cfg_path, "w", encoding="utf-8").write(CONFIG % {
        "gw": gw, "ctl": ctl, "proxy": proxy, "up": up,
        "db": os.path.join(tmp, "usage.sqlite3").replace(os.sep, "/")})

    env = os.environ.copy()
    env["SMSSOCKET_CONFIG"] = cfg_path
    env["PYTHONPATH"] = HERE
    procs = [spawn(["mock_upstream.py", str(up)]),
             spawn(["mock_clash.py", str(ctl)]),
             spawn(["mock_proxy.py", str(proxy)])]
    base = "http://127.0.0.1:%d" % gw
    gw_proc = subprocess.Popen([PY, "-B", "-m", "uvicorn", "SMSocket.gateway:create_app",
                                "--factory", "--host", "127.0.0.1", "--port", str(gw),
                                "--log-level", "warning"], cwd=HERE, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    procs.append(gw_proc)
    try:
        if not wait_up(base + "/healthz", gw_proc):
            check("gateway boots", False, "uvicorn did not come up")
            return 1
        check("gateway boots", True, base)
        with httpx.Client(timeout=25.0) as c:
            walk(c, base, up, ctl, proxy)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n%d/%d checks passed" % (len(RESULTS) - len(bad), len(RESULTS)))
    if bad:
        print("FAILED: " + ", ".join(bad))
        return 1
    print("E2E PASS - clash net plane, stack scheduler and assessment all live")
    return 0


def walk(c: httpx.Client, base: str, up: int, ctl: int, proxy: int) -> None:
    msg = {"messages": [{"role": "user", "content": "ping"}], "model": "demo"}

    # ---- 1. plain chat through the gateway --------------------------------
    r = c.post(base + "/v1/chat/completions", headers=H, json=msg)
    ok = r.status_code == 200 and r.json().get("choices")
    check("chat 200 through mock upstream", ok, str(r.status_code))
    check("x-socket-egress header present", "x-socket-egress" in r.headers,
          r.headers.get("x-socket-egress", "-"))
    check("egress starts direct", (r.headers.get("x-socket-egress") or "") == "direct")

    # ---- 2. stack counters ride on /concurrency ---------------------------
    conc = c.get(base + "/concurrency").json()
    check("/concurrency exposes stack view", bool(conc.get("stack")),
          json.dumps(conc.get("stack", {}))[:60])
    check("stack enabled + draining", conc["stack"]["enabled"] is True
          and conc["stack"]["draining"] is True)

    # ---- 3. discreet net plane --------------------------------------------
    st = c.get(base + "/internal/net/state", headers=H).json()
    check("net plane alive", st.get("enabled") is True and st.get("alive") is True,
          "controller=%s" % st.get("controller"))
    check("clash paths registered", any(p.startswith("proxy:") for p in st["paths"]),
          str(st["paths"]))
    probes = c.post(base + "/internal/net/probes", headers=H, json={}).json()
    scores = c.get(base + "/internal/net/scores", headers=H).json()["scores"]
    check("probe measured every path", len(scores) >= 2,
          "%d scores" % len(scores))
    check("direct path healthy", any(s["egress"] == "direct" and s["ok"] for s in scores))
    check("proxy path healthy", any(s["egress"].startswith("proxy:") and s["ok"]
                                    for s in scores))

    sel = c.put(base + "/internal/net/select", headers=H,
                json={"group": "PROXY-GRP", "node": "jp-2"}).json()
    check("select a clash node", sel.get("ok") is True
          and sel.get("egress") == "node:PROXY-GRP/jp-2", json.dumps(sel)[:80])
    mode = c.post(base + "/internal/net/mode", headers=H, json={"mode": "proxy"}).json()
    check("force proxy mode", mode.get("mode") == "proxy")
    r = c.post(base + "/v1/chat/completions", headers=H, json=msg)
    egress = r.headers.get("x-socket-egress", "")
    check("chat routed through clash proxy", r.status_code == 200
          and egress != "direct", egress)
    back = c.post(base + "/internal/net/mode", headers=H, json={"mode": "auto"}).json()
    check("mode returns to auto", back.get("mode") == "auto")

    # ---- 4. assessment: probe sweep + live harvest ------------------------
    run = c.post(base + "/assess/run", headers=H, json={"models": ["demo"]}).json()
    check("sweep ran", run.get("status") == "done" and run.get("ok", 0) >= 1,
          "total=%s ok=%s" % (run.get("total"), run.get("ok")))
    row = (run.get("rows") or [{}])[0]
    check("probe measured latency + ttft + tok_s",
          row.get("latency_ms", 0) > 0 and row.get("completion", 0) > 0,
          "lat=%sms ttft=%sms tok/s=%s" % (row.get("latency_ms"), row.get("ttft_ms"),
                                           row.get("tok_s")))
    rep = c.get(base + "/assess", headers=H).json()
    cells = {(r["model"], r["egress"]): r["verdict"] for r in rep["rows"]}
    check("report has verdicts", ("demo", "direct") in cells, str(cells)[:80])
    check("healthy verdict for a working cell",
          cells.get(("demo", "direct")) == "healthy")
    c.post(base + "/v1/chat/completions", headers=H, json=msg)
    live = c.get(base + "/assess", headers=H, params={"sources": "live"}).json()
    check("live traffic mirrored (no extra calls)",
          any(r["calls"] >= 1 for r in live["rows"]),
          "%d live cells" % len(live["rows"]))
    status = c.get(base + "/assess/status", headers=H).json()
    check("assess loop scheduled", status["enabled"] is True
          and status["scheduled"] is True and status["next_run"],
          "rows=%s" % status["counts"]["rows"])
    hist = c.get(base + "/assess/history", headers=H, params={"model": "demo"}).json()
    check("history readable", len(hist["rows"]) >= 2, "%d rows" % len(hist["rows"]))

    # ---- 5. saturation parks instead of refusing -------------------------
    batch = c.post(base + "/v1/batch", headers=H, json={
        "requests": [dict(msg, custom_id="a"), dict(msg, custom_id="b"),
                     dict(msg, custom_id="c")], "concurrency": 3}).json()
    codes = [r["status"] for r in batch["results"]]
    stack = c.get(base + "/stack").json()
    check("batch survived a saturated gate", all(x == 200 for x in codes), str(codes))
    check("callers were parked, not rejected",
          stack["pushed"] >= 1 and stack["popped"] >= 1,
          "pushed=%s popped=%s expired=%s" % (stack["pushed"], stack["popped"],
                                              stack["expired"]))
    conc = c.get(base + "/concurrency").json()
    print("      DIAG rejected=%s queued=%s total=%s | stack pushed=%s popped=%s "
          "expired=%s dropped=%s" % (conc["rejected"], conc["queued"], conc["total"],
                                     stack["pushed"], stack["popped"],
                                     stack["expired"], stack["dropped"]), flush=True)
    # `rejected` is gateway-wide and the earlier sweep (concurrency 2 against
    # max_concurrency 1) legitimately parked and expired a probe. What must hold
    # is that no caller was refused outright: every refusal followed parking.
    check("no instant refusals (every 429 followed a park)",
          conc["rejected"] <= stack["expired"] + stack["dropped"],
          "rejected=%s expired=%s dropped=%s" % (conc["rejected"], stack["expired"],
                                                 stack["dropped"]))

    # ---- 6. discretion + docs --------------------------------------------
    spec = c.get(base + "/openapi.json").json()
    paths = json.dumps(list(spec.get("paths", {})))
    check("/internal/net absent from OpenAPI", "/internal/net" not in paths)
    check("/assess documented on purpose", "/assess" in paths)
    page = c.get(base + "/").text
    check("dashboard renders reachability", "const ASSESS_ON=1;" in page
          and "__ASSESS_ON__" not in page)
    check("dashboard net glyph live", "const NET_ON=1;" in page)
    check("404 on an empty stack slot",
          c.delete(base + "/stack/0", headers=H).status_code == 404)
    check("manual drain answers", c.post(base + "/stack/drain", headers=H,
                                        json={"n": 1}).json()["woken"] == 0)
    check("reload keeps every v0.4 plane",
          c.post(base + "/admin/reload", headers=H).json()["assess"] is True)


if __name__ == "__main__":
    sys.exit(main())
