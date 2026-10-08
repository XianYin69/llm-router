"""v0.5b tests: the two-level provider -> model tree and the auto-probe trigger.

The console has no "模型探测" page any more: entering 提供商与大模型 reads the tree
and asks the gateway to re-probe whatever went stale, all by itself.
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_discover import KEY, make_app, session, upstream          # noqa: E402
from SMSocket import discover_routes                                # noqa: E402

H = {"Authorization": "Bearer " + KEY}


def test_tree_lists_provider_then_models(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    body = session(app, lambda c: c.get("/admin/provider-models", headers=H)).json()
    assert body["count"] == 1 and body["models"] == 1
    prov = body["data"][0]
    assert prov["name"] == "VendorX" and prov["probed_at"] == 0.0
    assert prov["stale"] is True and prov["ok_models"] == 0
    m = prov["models"][0]
    assert m["alias"] == "old-alias" and m["upstream"] == "x-old"
    assert m["published"] is True and m["probed"] is False


def test_tree_merges_probe_results(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    st.catalog.put([
        {"provider": "VendorX", "model": "x-old", "ok": True, "status": 200,
         "latency_ms": 420.0, "context": 8000, "stream": "supported",
         "params": {"temperature": "supported", "reasoning_effort": "rejected"},
         "error": "", "ts": time.time()},
        {"provider": "VendorX", "model": "x-big", "ok": False, "status": 404,
         "latency_ms": 30.0, "context": 128000, "params": {}, "error": "no access",
         "embeddings": "", "ts": time.time()}])
    prov = session(app, lambda c: c.get("/admin/provider-models", headers=H)).json()["data"][0]
    assert prov["stale"] is False and prov["ok_models"] == 1
    assert prov["model_count"] == 2                      # published alias + new id
    byid = {x["upstream"]: x for x in prov["models"]}
    old = byid["x-old"]
    assert old["probed"] and old["ok"] and old["alias"] == "old-alias"
    assert old["params"]["reasoning_effort"] == "rejected"
    big = byid["x-big"]
    assert big["published"] is False and big["ok"] is False
    assert big["error"] == "no access"


def test_tree_requires_auth(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    assert session(app, lambda c: c.get("/admin/provider-models")).status_code == 401


def test_refresh_queues_stale_provider(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    r = session(app, lambda c: c.post("/admin/provider-models/refresh", headers=H, json={}))
    assert r.status_code == 200 and r.json()["queued"] == ["VendorX"]
    assert r.json()["poll"] == "/admin/discover/status"


def test_refresh_skips_fresh_provider(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    st.catalog.put([{"provider": "VendorX", "model": "x-old", "ok": True,
                     "ts": time.time()}])
    r = session(app, lambda c: c.post("/admin/provider-models/refresh", headers=H, json={}))
    assert r.json()["queued"] == [] and "fresh" in r.json()["note"]


def test_refresh_never_probes_keyless_or_disabled(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    from SMSocket.config import ProviderSpec
    st.settings.providers.append(ProviderSpec(name="NoKey", base_url="https://n.example/v1",
                                              keys=[]))
    st.settings.providers.append(ProviderSpec(name="Off", base_url="https://o.example/v1",
                                              keys=["k"], enabled=False))
    r = session(app, lambda c: c.post("/admin/provider-models/refresh", headers=H, json={}))
    assert r.json()["queued"] == ["VendorX"]
    assert discover_routes._stale_names(st, 900) == ["VendorX"]


def test_refresh_explicit_provider_and_unknown(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    st.catalog.put([{"provider": "VendorX", "model": "x-old", "ok": True,
                     "ts": time.time()}])
    r = session(app, lambda c: c.post("/admin/provider-models/refresh", headers=H,
                                      json={"providers": ["VendorX"]}))
    assert r.json()["queued"] == ["VendorX"]          # explicit wins over freshness
    bad = session(app, lambda c: c.post("/admin/provider-models/refresh", headers=H,
                                        json={"providers": ["Ghost"]}))
    assert bad.status_code == 404


def test_refresh_respects_discover_switch(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    st.settings.discover.on_add = False
    r = session(app, lambda c: c.post("/admin/provider-models/refresh", headers=H, json={}))
    assert r.json()["queued"] == [] and "off" in r.json()["note"]


def test_refresh_needs_live_gateway(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    st.http = None
    r = session(app, lambda c: c.post("/admin/provider-models/refresh", headers=H, json={}))
    assert r.status_code == 503


def test_auto_discover_publishes_without_any_click(tmp_path, monkeypatch):
    """The point of the change: add a provider, walk away, aliases appear."""
    app, st = make_app(tmp_path, upstream(), monkeypatch)

    async def go():
        return await discover_routes.auto_discover(st, ["VendorX"])
    out = asyncio.run(go())
    assert "VendorX/x-big" in out["added"] and "VendorX/x-small" in out["added"]
    # x-old is already served by `old-alias`, so publishing must not duplicate it
    assert "VendorX/x-old" not in out["added"] and "x-old" in out["skipped"]
    assert "x-big" in st.settings.model_index() and "x-small" in st.settings.model_index()
    assert st.settings.model_index()["old-alias"][0].models["old-alias"] == "x-old"
    prov = session(app, lambda c: c.get("/admin/provider-models", headers=H)).json()["data"][0]
    assert prov["stale"] is False and prov["ok_models"] >= 1
    assert all(x["probed"] for x in prov["models"] if x["published"])


def test_fresh_seconds_is_configurable(tmp_path):
    from SMSocket.config import discover_from_raw
    assert discover_from_raw({"fresh_seconds": 30}).fresh_seconds == 30
    assert discover_from_raw({}).fresh_seconds == 900
    assert discover_from_raw({}).as_config()["fresh_seconds"] == 900
    with pytest.raises(ValueError):
        discover_from_raw({"fresh_seconds": 99999999})


def test_stale_names_marks_old_or_missing(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    assert discover_routes._stale_names(st, 900) == ["VendorX"]
    st.catalog.put([{"provider": "VendorX", "model": "x-old", "ok": True,
                     "ts": time.time() - 4000}])
    assert discover_routes._stale_names(st, 900) == ["VendorX"]
    st.catalog.put([{"provider": "VendorX", "model": "x-old", "ok": True,
                     "ts": time.time()}])
    assert discover_routes._stale_names(st, 900) == []

def test_blocked_providers_are_explained(tmp_path, monkeypatch):
    """A provider that cannot be probed says why - not "everything is fresh"."""
    from SMSocket.config import ProviderSpec
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    st.catalog.put([{"provider": "VendorX", "model": "x-old", "ok": True,
                     "ts": time.time()}])
    st.settings.providers.append(ProviderSpec(name="NoKey", base_url="https://n.example/v1",
                                              keys=[]))
    st.settings.providers.append(ProviderSpec(name="Off", base_url="https://o.example/v1",
                                              keys=["k"], enabled=False))
    r = session(app, lambda c: c.post("/admin/provider-models/refresh", headers=H, json={}))
    assert r.json()["queued"] == []
    note = r.json()["note"]
    assert "无密钥" in note and "已停用" in note
    tree = session(app, lambda c: c.get("/admin/provider-models", headers=H)).json()["data"]
    by = {x["name"]: x for x in tree}
    assert "无密钥" in by["NoKey"]["probe_note"]
    assert "已停用" in by["Off"]["probe_note"]
    assert by["VendorX"]["probe_note"] == ""

def test_scheduled_tick_probes_without_a_page(tmp_path, monkeypatch):
    """Freshness holds even when nobody opens the console."""
    app, st = make_app(tmp_path, upstream(), monkeypatch)

    async def go():
        names = await discover_routes._probe_tick(st, 900)
        again = await discover_routes._probe_tick(st, 900)
        return names, again
    names, again = asyncio.run(go())
    assert names == ["VendorX"]
    assert again == []                                  # fresh now: nothing to do
    assert "x-big" in st.settings.model_index()
    assert getattr(st, "auto_probe", set()) == set()


def test_scheduled_tick_respects_switch(tmp_path, monkeypatch):
    """`discover.on_add: false` means the gateway never probes on its own."""
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    st.settings.discover.on_add = False

    async def go():
        return await discover_routes._probe_tick(st, 900)
    assert asyncio.run(go()) == []                      # switch off = no probing


def test_scheduled_tick_noop_without_http(tmp_path, monkeypatch):
    app, st = make_app(tmp_path, upstream(), monkeypatch)
    st.http = None
    assert asyncio.run(discover_routes._probe_tick(st, 900)) == []


def test_probe_loop_starts_and_stops(tmp_path, monkeypatch):
    """The lifespan owns the task; cancelling it must not leak."""
    app, st = make_app(tmp_path, upstream(), monkeypatch)

    async def go():
        st.settings.discover.fresh_seconds = 0          # clamp: loop sleeps 60s
        task = discover_routes._probe_loop(st)
        await asyncio.sleep(0.05)
        alive = not task.done()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return alive
    assert asyncio.run(go()) is True
