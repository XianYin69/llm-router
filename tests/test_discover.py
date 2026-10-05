"""Discovery tests: model listing, test-message probing, param classification,
catalog caching, apply-to-config, async job + progress."""
import asyncio
import json

import httpx
import pytest
import yaml

from SMSocket.config import ProviderSpec, Settings, load_config
from SMSocket.discover import (CHAT_PARAMS, Catalog, Discoverer, classify,
                               models_url, parse_model_list)
from SMSocket.gateway import create_app
from SMSocket.providers import mask
from SMSocket.router import Router

KEY = "sk-test"
H = {"Authorization": "Bearer " + KEY}


def make_settings(tmp_path, **kw):
    return Settings(listen="127.0.0.1:8000", master_keys=[KEY], strategy="priority",
                    retry=0, cooldown=0.0, db_path=str(tmp_path / "usage.sqlite3"),
                    providers=[ProviderSpec(name="VendorX", base_url="https://x.example/v1",
                                            keys=[KEY], models={"old-alias": "x-old"},
                                            priority=5)],
                    **kw)


def upstream(serve=("x-old", "x-big", "x-small"), reject=("reasoning_effort", "tools"),
            list_ok=True, live=None, slow=0.0, chat_deny=()):
    """Fake provider: /v1/models + chat with per-parameter rejection."""
    async def h(request: httpx.Request) -> httpx.Response:
        if live is not None:
            live["n"] = live.get("n", 0) + 1
            live["peak"] = max(live.get("peak", 0), live["n"])
        if slow:
            await asyncio.sleep(slow)
        if live is not None:
            live["n"] -= 1
        if request.url.path.endswith("/models") and request.method == "GET":
            if not list_ok:
                return httpx.Response(403, json={"error": {"message": "models endpoint off"}})
            return httpx.Response(200, headers={"content-type": "application/json"}, json={
                "object": "list",
                "data": [{"id": m, "object": "model", "owned_by": "vendorx",
                          "created": 1700000000,
                          "context_length": 128000 if m == "x-big" else 8000}
                         for m in serve]})
        body = json.loads(request.content)
        model = str(body.get("model") or "")
        if model in chat_deny:
            return httpx.Response(404, json={"error": {
                "message": f"The model '{model}' does not exist"}})
        if model not in serve:
            return httpx.Response(404, json={"error": {"message": f"The model '{model}' does not exist"}})
        for param in reject:
            if param in body:
                return httpx.Response(400, json={"error": {
                    "message": f"Unknown parameter: '{param}'"}})
        if body.get("stream"):
            sse = (b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                   b"data: [DONE]\n\n")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=sse)
        return httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}})
    return httpx.MockTransport(h)


CFG = """listen: 127.0.0.1:8011
db_path: "{db}"
strategy: priority
retry: 0
cooldown: 0
providers:
  - name: VendorX
    base_url: https://x.example/v1
    keys: [sk-test]
    models:
      old-alias: x-old
    priority: 5
"""


def make_app(tmp_path, transport, monkeypatch, **kw):
    cfg = tmp_path / "config.yaml"
    db = str(tmp_path / "usage.sqlite3").replace("\\", "/")
    cfg.write_text(CFG.replace(chr(123) + "db" + chr(125), db), encoding="utf-8")
    monkeypatch.setenv("SMSSOCKET_CONFIG", str(cfg))
    monkeypatch.setenv("SMSSOCKET_MASTER_KEY", KEY)
    monkeypatch.delenv("SMSSOCKET_NO_KEY", raising=False)
    settings = load_config(cfg)
    app = create_app(settings)
    st = app.state.llm
    st.http = httpx.AsyncClient(transport=transport)
    st.router = Router(st.settings, st.pool, st.usage, st.http, st.gate)
    return app, st


def session(app, script):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://gateway") as c:
            return await script(c)
    return asyncio.run(go())


# ---- pure functions -------------------------------------------------------
def test_parse_model_list_shapes():
    assert [m["id"] for m in parse_model_list(
        {"data": [{"id": "a"}, {"id": "b"}]})] == ["a", "b"]
    assert parse_model_list({"data": [{"name": "n1"}]})[0]["id"] == "n1"
    got = parse_model_list({"data": [{"id": "a", "model_metadata": {"context_window": 32000}}]})
    assert got[0]["context"] == 32000
    assert parse_model_list({"data": [{"id": ""}, {"nope": 1}]}) == []
    assert parse_model_list([{"id": "x"}])[0]["id"] == "x"


def test_models_url_and_masking():
    p = ProviderSpec(name="p", base_url="https://a.example/v1", keys=["sk-1234567890abcdef"])
    assert models_url(p) == "https://a.example/v1/models"
    p2 = ProviderSpec(name="p", base_url="https://a.example", keys=["k"])
    assert models_url(p2) == "https://a.example/v1/models"
    assert "1234567890abcdef" not in mask("sk-1234567890abcdef")


def test_classify_verdicts():
    assert classify(200, "", "temperature") == "supported"
    assert classify(400, "Unknown parameter: 'tools'", "tools") == "rejected"
    assert classify(404, "model not found", "seed") == "rejected"
    assert classify(500, "internal", "seed") == "unknown"
    assert classify(0, "ConnectError", "seed") == "unknown"


# ---- discoverer -----------------------------------------------------------
def test_probe_provider_lists_and_probes_every_model(tmp_path, monkeypatch):
    st = make_settings(tmp_path)
    d = Discoverer(st, httpx.AsyncClient(transport=upstream()))
    rep = asyncio.run(d.probe_provider(st.providers[0], concurrency=4, timeout=5.0))
    ids = sorted(m["model"] for m in rep["models"])
    assert ids == ["x-big", "x-old", "x-small"]
    assert rep["listed"] == 3 and rep["ok"] == 3 and rep["failed"] == 0
    big = next(m for m in rep["models"] if m["model"] == "x-big")
    assert big["context"] == 128000 and big["owned_by"] == "vendorx"
    assert big["usage"]["total_tokens"] == 10 and big["reply"] == "ok"
    assert big["params"]["temperature"] == "supported"
    assert big["params"]["reasoning_effort"] == "rejected"
    assert big["params"]["tools"] == "rejected"
    assert big["param_detail"]["tools"]["status"] == 400
    await_close = None
    asyncio.run(d.http.aclose())


def test_probe_marks_unusable_models_and_skips_unlisted_ones(tmp_path, monkeypatch):
    """A listed-but-unusable model is reported; models the provider never lists
    are not probed (the listing is authoritative)."""
    st = make_settings(tmp_path)
    d = Discoverer(st, httpx.AsyncClient(transport=upstream(chat_deny={"x-small"})))
    rep = asyncio.run(d.probe_provider(st.providers[0], concurrency=2, timeout=5.0))
    dead = next(m for m in rep["models"] if m["model"] == "x-small")
    assert dead["ok"] is False and dead["status"] == 404
    assert "does not exist" in dead["error"]
    assert dead["params"] == {}                 # never probed further
    assert rep["ok"] == 2 and rep["failed"] == 1
    asyncio.run(d.http.aclose())


def test_probe_ignores_models_the_provider_does_not_list(tmp_path, monkeypatch):
    st = make_settings(tmp_path)
    d = Discoverer(st, httpx.AsyncClient(transport=upstream(serve=("x-old",))))
    rep = asyncio.run(d.probe_provider(st.providers[0], concurrency=2, timeout=5.0))
    assert [m["model"] for m in rep["models"]] == ["x-old"]
    assert rep["ok"] == 1
    asyncio.run(d.http.aclose())


def test_probe_respects_model_filter_and_param_switch(tmp_path, monkeypatch):
    st = make_settings(tmp_path)
    d = Discoverer(st, httpx.AsyncClient(transport=upstream()))
    rep = asyncio.run(d.probe_provider(st.providers[0], models=["x-big"],
                                       probe_params=False, probe_stream=False,
                                       timeout=5.0))
    assert [m["model"] for m in rep["models"]] == ["x-big"]
    assert rep["models"][0]["params"] == {}
    asyncio.run(d.http.aclose())


def test_probe_without_key_reports_not_probes(tmp_path, monkeypatch):
    st = make_settings(tmp_path)
    st.providers[0].keys = []
    d = Discoverer(st, httpx.AsyncClient(transport=upstream()))
    rep = asyncio.run(d.probe_provider(st.providers[0]))
    assert rep["models"] == [] and "no api key" in rep["error"]
    asyncio.run(d.http.aclose())


def test_probe_runs_in_parallel(tmp_path, monkeypatch):
    st = make_settings(tmp_path)
    live = {"n": 0, "peak": 0}
    d = Discoverer(st, httpx.AsyncClient(transport=upstream(live=live, slow=0.05)))
    rep = asyncio.run(d.probe_provider(st.providers[0], concurrency=3, probe_params=False,
                                       probe_stream=False, timeout=5.0))
    assert rep["probed"] == 3 and live["peak"] > 1, live
    asyncio.run(d.http.aclose())


# ---- catalog --------------------------------------------------------------
def test_catalog_roundtrip_and_scoping(tmp_path, monkeypatch):
    c = Catalog(str(tmp_path / "cat.sqlite3"))
    rows = [{"provider": "A", "model": "a1", "ok": True},
            {"provider": "A", "model": "a2", "ok": False},
            {"provider": "B", "model": "b1", "ok": True}]
    assert c.put(rows) == 3
    assert len(c.all()) == 3 and [r["model"] for r in c.all("B")] == ["b1"]
    assert c.last_seen() > 0
    c.put([{"provider": "A", "model": "a1", "ok": True, "context": 99}])   # replace
    assert len(c.all()) == 3
    assert next(r for r in c.all("A") if r["model"] == "a1")["context"] == 99
    assert c.clear("B") == 1 and len(c.all()) == 2
    c.close()


# ---- routes ---------------------------------------------------------------
def test_discover_route_caches_catalog(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    r = session(app, lambda c: c.post("/admin/discover", headers=H,
                                      json={"providers": ["VendorX"], "concurrency": 4,
                                           "timeout": 5.0}))
    assert r.status_code == 200, r.text
    rep = r.json()["providers"][0]
    assert rep["ok"] == 3 and rep["probed"] == 3
    cached = session(app, lambda c: c.get("/admin/models", headers=H)).json()
    assert cached["count"] == 3
    assert any(m["model"] == "x-big" and m["context"] == 128000 for m in cached["data"])
    assert cached["published"] == ["old-alias"]


def test_discover_validates_target(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    bad = session(app, lambda c: c.post("/admin/discover", headers=H,
                                        json={"providers": ["Ghost"]}))
    assert bad.status_code == 404 and "Ghost" in bad.json()["detail"]["error"]["message"]
    junk = session(app, lambda c: c.post("/admin/discover", headers=H,
                                         json={"target": {"base_url": "https://z/v1"}}))
    assert junk.status_code == 400


def test_discover_adhoc_target_reuses_configured_key(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    r = session(app, lambda c: c.post("/admin/discover", headers=H, json={
        "target": {"name": "adhoc", "base_url": "https://x.example/v1"},
        "models": ["x-old"], "probe_params": False, "probe_stream": False, "timeout": 5.0}))
    assert r.status_code == 200, r.text
    rep = r.json()["providers"][0]
    assert rep["provider"] == "adhoc" and rep["ok"] == 1
    assert rep["key"] == mask(KEY)


def test_discover_async_job_and_progress(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(slow=0.05), monkeypatch)
    async def script(c):
        r = await c.post("/admin/discover", headers=H,
                         json={"providers": ["VendorX"], "async": 1, "concurrency": 1,
                               "probe_params": False, "probe_stream": False, "timeout": 5.0})
        assert r.status_code == 200, r.text
        assert r.json()["poll"] == "/admin/discover/status"
        seen = []
        for _ in range(80):
            s = (await c.get("/admin/discover/status", headers=H)).json()
            seen.append((s["status"], s["done"]))
            if s["status"] == "done":
                return s, seen
            await asyncio.sleep(0.02)
        return s, seen
    final, seen = session(app, script)
    assert final["status"] == "done" and final["done"] == 3
    assert any(st_ == "running" for st_, _ in seen), seen
    assert final["providers"] == ["VendorX"]


def test_apply_publishes_new_aliases_only(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    session(app, lambda c: c.post("/admin/discover", headers=H,
                                  json={"providers": ["VendorX"], "probe_stream": False,
                                        "timeout": 5.0}))
    r = session(app, lambda c: c.post("/admin/models/apply", headers=H,
                                      json={"providers": ["VendorX"]}))
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(body["added"]) == ["VendorX/x-big", "VendorX/x-small"]
    assert "VendorX/old-alias" not in body["added"]
    assert "old-alias" in session(app, lambda c: c.get("/v1/models", headers=H)).text
    cfg_after = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    served = sorted(cfg_after["providers"][0]["models"])
    assert "x-big" in served and "old-alias" in served
    # second apply is a no-op
    again = session(app, lambda c: c.post("/admin/models/apply", headers=H,
                                          json={"providers": ["VendorX"]})).json()
    assert again["added"] == [] and len(again["skipped"]) == 3


def test_apply_requires_prior_discovery(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    r = session(app, lambda c: c.post("/admin/models/apply", headers=H,
                                      json={"providers": ["VendorX"]}))
    assert r.status_code == 404


def test_apply_alias_prefix(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    session(app, lambda c: c.post("/admin/discover", headers=H,
                                  json={"providers": ["VendorX"], "probe_params": False,
                                        "probe_stream": False, "timeout": 5.0}))
    r = session(app, lambda c: c.post("/admin/models/apply", headers=H,
                                      json={"providers": ["VendorX"], "alias_prefix": "vx-"}))
    assert sorted(x.split("/")[1] for x in r.json()["added"]) == ["vx-x-big", "vx-x-old",
                                                                  "vx-x-small"]


def test_catalog_clear_route(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    session(app, lambda c: c.post("/admin/discover", headers=H,
                                  json={"providers": ["VendorX"], "probe_stream": False,
                                        "timeout": 5.0}))
    assert session(app, lambda c: c.delete("/admin/models", headers=H,
                                           params={"provider": "VendorX"})).json()["cleared"] == 3
    assert session(app, lambda c: c.get("/admin/models", headers=H)).json()["count"] == 0


def test_discover_requires_auth(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    assert session(app, lambda c: c.post("/admin/discover", json={})).status_code == 401
    assert session(app, lambda c: c.get("/admin/models")).status_code == 401
