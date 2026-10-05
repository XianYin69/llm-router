"""Net-plane tests: clash client, graceful degradation, egress clients,
smart routing, the discreet /internal/net surface and its OpenAPI absence.

Everything is offline: the controller and every egress client are backed by
httpx.MockTransport, and scores are injected instead of timed, so no test
depends on DNS, latency or a running clash.
"""
import asyncio
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from SMSocket.clash import (DIRECT, ClashController, ClashUnavailable,
                            EgressRegistry, EgressScore, NetPlane, NetProbe,
                            node_id, proxy_id, split_node)
from SMSocket.config import (ClashConfig, ProviderSpec, Settings,
                            clash_from_raw, load_config)
from SMSocket.gateway import create_app
from SMSocket.providers import Pool
from SMSocket.usage import Usage

SECRET = "sec-123"
PROXY = "http://clash.test:7890"
H = {"Authorization": "Bearer sk-test"}
CALLS: list[tuple[str, str]] = []
PROXY_BUILDS: list[str] = []


def _chat(request):
    return httpx.Response(200, json={
        "id": "c1", "object": "chat.completion", "model": "demo",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "hello"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})


class SSEStream(httpx.AsyncByteStream):
    """Chunked SSE body (MockTransport cannot re-iterate plain bytes)."""

    def __init__(self, parts):
        self.parts = parts

    async def __aiter__(self):
        for p in self.parts:
            yield p.encode()


def _sse():
    c = lambda o: "data: " + json.dumps(o) + "\n\n"
    return SSEStream([c({"choices": [{"index": 0, "delta": {"content": "yo"}}]}),
                      c({"choices": [], "usage": {"prompt_tokens": 4,
                                                  "completion_tokens": 1,
                                                  "total_tokens": 5}})
                      + "data: [DONE]\n\n"])


def app_handler(via):
    """One handler per egress: records which client the call left through."""
    def handler(request: httpx.Request) -> httpx.Response:
        CALLS.append((via, str(request.url)))
        try:
            wants_stream = bool(json.loads(request.content).get("stream"))
        except Exception:
            wants_stream = False
        return httpx.Response(200, stream=_sse()) if wants_stream else _chat(request)
    return handler


def patch_clients(monkeypatch):
    """Every AsyncClient the gateway builds gets a deterministic mock transport."""
    real = httpx.AsyncClient

    def patched(*a, **kw):
        kw.setdefault("transport", httpx.MockTransport(
            app_handler("proxy") if kw.get("proxy") else app_handler("direct")))
        return real(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    monkeypatch.setattr(NetPlane, "start", lambda self: None)

    def fake_build(self, proxy):
        PROXY_BUILDS.append(proxy)
        return real(transport=httpx.MockTransport(app_handler("proxy")))
    monkeypatch.setattr(EgressRegistry, "_build", fake_build)


def settings_for(tmp_path, clash=None):
    return Settings(master_keys=["sk-test"], db_path=str(tmp_path / "u.sqlite3"),
                    clash=clash or ClashConfig(), providers=[
                        ProviderSpec(name="openai", base_url="http://api.test/v1",
                                     keys=["sk-up-AAAA"], models={"demo": "demo-up"},
                                     embeddings={"demo-embed": "demo-emb"})])


def cfg(**kw):
    base = dict(enabled=True, controller="http://clash.test:9090", secret=SECRET,
                mixed_port=7890, interval=99999, timeout=2)
    base.update(kw)
    return ClashConfig(**base)


def controller(cfg_, handler):
    return ClashController(cfg_, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False))


def plane_for(cfg_, handler=None):
    """A plane whose controller *and* proxy clients share one mock transport."""
    h = handler or app_handler("direct")
    shared = httpx.AsyncClient(transport=httpx.MockTransport(h))
    ctl = controller(cfg_, h) if cfg_.enabled else None
    reg = EgressRegistry(shared, cfg_,
                         client_factory=lambda p: httpx.AsyncClient(
                             transport=httpx.MockTransport(h)))
    return NetPlane(cfg_, reg, ctl)


def test_controller_sends_bearer_secret_and_parses():
    seen = {}

    def h(request):
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "1.18.0", "meta": {}})
        if request.url.path == "/proxies":
            return httpx.Response(200, json={"proxies": {
                "PROXY-GRP": {"type": "Selector", "now": "hk-1", "hidden": False,
                               "all": ["hk-1", "jp-2"]}}})
        return httpx.Response(200, json={"delay": 137})

    async def run():
        ctl = controller(cfg(), h)
        assert (await ctl.version())["version"] == "1.18.0"
        assert await ctl.alive() is True
        px = await ctl.proxies()
        assert px["proxies"]["PROXY-GRP"]["now"] == "hk-1"
        assert await ctl.delay("hk-1") == 137.0
        assert await ctl.select("PROXY-GRP", "jp-2") == {
            "group": "PROXY-GRP", "node": "jp-2", "ok": True}
        await ctl.close()
    asyncio.run(run())
    assert seen["auth"] == "Bearer " + SECRET


def test_controller_delay_passes_health_url_and_expected():
    captured = {}

    def h(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"delay": 12})

    async def run():
        ctl = controller(cfg(), h)
        assert await ctl.delay("hk-1") == 12.0
        await ctl.close()
    asyncio.run(run())
    assert "url=https" in captured["url"] and "expected=204" in captured["url"]


def test_controller_reads_history_rules_memory_connections():
    def h(request):
        p = request.url.path
        if p.endswith("/history"):
            return httpx.Response(200, json=[{"delay": 10, "tested": 1}])
        if p == "/rules":
            return httpx.Response(200, json={"rules": [{"type": "DOMAIN-SUFFIX"}]})
        if p == "/memory":
            return httpx.Response(200, json={"inuse": 1234})
        if p == "/connections":
            return httpx.Response(200, json={"connections": [], "upload": 0})
        return httpx.Response(200, json={})

    async def run():
        ctl = controller(cfg(), h)
        assert (await ctl.history("PROXY-GRP"))[0]["delay"] == 10
        assert "rules" in await ctl.rules()
        assert (await ctl.memory())["inuse"] == 1234
        assert (await ctl.connections())["connections"] == []
        await ctl.close()
    asyncio.run(run())


def test_controller_select_uses_put_and_encodes_names():
    seen = []

    def h(request):
        seen.append((request.method, str(request.url), json.loads(request.content)))
        return httpx.Response(200)

    async def run():
        ctl = controller(cfg(), h)
        await ctl.select("PROXY GRP", "hk 1")
        await ctl.close()
    asyncio.run(run())
    assert seen[0][0] == "PUT" and "/proxies/PROXY%20GRP" in seen[0][1]
    assert seen[0][2] == {"name": "hk 1"}


def test_controller_flush_dns_falls_back_from_put_to_post():
    verbs = []

    def h(request):
        verbs.append(request.method)
        if request.method == "PUT":
            return httpx.Response(404, text="not found")
        return httpx.Response(200)

    async def run():
        ctl = controller(cfg(), h)
        out = await ctl.flush_dns()
        await ctl.close()
        return out
    assert asyncio.run(run()) == {"flushed": True, "method": "POST"}
    assert verbs == ["PUT", "POST"]


def test_controller_absent_degrades_instead_of_crashing():
    """A machine with no clash running: transport error -> False / ClashUnavailable."""
    def h(request):
        raise httpx.ConnectError("getaddrinfo failed", request=request)

    async def run():
        ctl = controller(cfg(), h)
        assert await ctl.alive() is False
        with pytest.raises(ClashUnavailable):
            await ctl.proxies()
        await ctl.close()
    asyncio.run(run())


def test_controller_rejects_bad_secret_with_a_status_code():
    def h(request):
        return httpx.Response(401, json={"error": "unauthorized"})

    async def run():
        ctl = controller(cfg(), h)
        assert await ctl.alive() is False
        try:
            await ctl.version()
        except ClashUnavailable as e:
            assert e.status == 401 and "secret" in str(e)
        else:
            raise AssertionError("expected ClashUnavailable")
        await ctl.close()
    asyncio.run(run())


def test_node_id_helpers_roundtrip():
    eid = node_id("PROXY-GRP", "hk-1")
    assert eid == "node:PROXY-GRP/hk-1"
    assert split_node(eid) == ("PROXY-GRP", "hk-1")
    assert split_node(proxy_id(PROXY)) == ("", "")
    assert split_node(DIRECT) == ("", "")


def test_registry_direct_is_the_shared_client_and_proxy_paths_are_separate():
    async def run():
        shared = httpx.AsyncClient(transport=httpx.MockTransport(app_handler("direct")))
        built = []

        def factory(proxy):
            built.append(proxy)
            return httpx.AsyncClient(transport=httpx.MockTransport(app_handler("proxy")))
        reg = EgressRegistry(shared, cfg(), client_factory=factory)
        assert reg.client_for(DIRECT) is shared          # st.http stays st.http
        reg.add(proxy_id(PROXY), PROXY)
        first = reg.client_for(proxy_id(PROXY))
        assert first is not shared and reg.client_for(proxy_id(PROXY)) is first
        assert built == [PROXY]
        assert reg.paths() == [DIRECT, proxy_id(PROXY)]
        assert reg.client_for("node:who/what") is shared  # unknown -> direct
        assert [i["egress"] for i in reg.info()] == [DIRECT, proxy_id(PROXY)]
        await reg.aclose()
        await shared.aclose()
    asyncio.run(run())


def test_registry_rebuilds_client_when_the_proxy_url_changes():
    async def run():
        shared = httpx.AsyncClient(transport=httpx.MockTransport(app_handler("direct")))
        made = []
        reg = EgressRegistry(shared, cfg(), client_factory=lambda p: (
            made.append(p),
            httpx.AsyncClient(transport=httpx.MockTransport(app_handler("proxy"))))[1])
        eid = proxy_id(PROXY)
        reg.add(eid, PROXY)
        first = reg.client_for(eid)
        reg.add(eid, "http://clash.test:7899")
        assert reg.client_for(eid) is not first
        assert made == [PROXY, "http://clash.test:7899"]
        await reg.aclose()
        await shared.aclose()
    asyncio.run(run())


def test_pick_prefers_the_low_latency_egress():
    net = plane_for(cfg())
    net.scores[("openai", DIRECT)] = EgressScore(DIRECT, "openai", ok=True,
                                                 delay_ms=400, probes=1)
    net.scores[("openai", proxy_id(PROXY))] = EgressScore(
        proxy_id(PROXY), "openai", ok=True, delay_ms=40, probes=1)
    assert net.pick("openai") == proxy_id(PROXY)
    assert net.ranked("openai")[0]["egress"] == proxy_id(PROXY)
    assert net.sort_key("openai")[1] == 40.0
    asyncio.run(net.registry.aclose())


def test_pick_stays_direct_without_a_measurable_win():
    net = plane_for(cfg(min_delay_ms=50))               # hysteresis floor
    net.scores[("openai", DIRECT)] = EgressScore(DIRECT, "openai", ok=True,
                                                 delay_ms=100, probes=1)
    net.scores[("openai", proxy_id(PROXY))] = EgressScore(
        proxy_id(PROXY), "openai", ok=True, delay_ms=90, probes=1)
    assert net.pick("openai") == DIRECT
    net.scores[("openai", proxy_id(PROXY))].delay_ms = 20
    assert net.pick("openai") == proxy_id(PROXY)
    asyncio.run(net.registry.aclose())


def test_pick_degrades_to_direct_when_proxy_is_dead_or_nothing_measured():
    net = plane_for(cfg())
    net.scores[("openai", DIRECT)] = EgressScore(DIRECT, "openai", ok=True,
                                                 delay_ms=900, probes=1)
    net.scores[("openai", proxy_id(PROXY))] = EgressScore(
        proxy_id(PROXY), "openai", ok=False, delay_ms=2000, consecutive_fail=3,
        fail_ratio=1.0, probes=3)
    assert net.pick("openai") == DIRECT                 # a dead path never wins
    assert net.sort_key("openai")[0] == 0               # direct still works
    net.scores[("openai", DIRECT)].ok = False           # now everything is dead
    assert net.pick("openai") == DIRECT                 # degraded, but honest
    assert net.sort_key("openai")[0] == 1               # and the provider demotes
    net2 = plane_for(cfg())
    assert net2.pick("openai") == DIRECT                # nothing measured yet
    assert net2.sort_key("openai") == (0, 0.0, 0.0)     # unknown stays neutral
    asyncio.run(net.registry.aclose())
    asyncio.run(net2.registry.aclose())


def test_mode_direct_and_proxy_force_the_choice():
    net = plane_for(cfg())
    net.scores[("openai", DIRECT)] = EgressScore(DIRECT, "openai", ok=True,
                                                 delay_ms=10, probes=1)
    net.scores[("openai", proxy_id(PROXY))] = EgressScore(
        proxy_id(PROXY), "openai", ok=True, delay_ms=500, probes=1)
    net.mode = "direct"
    assert net.pick("openai") == DIRECT
    net.mode = "proxy"
    assert net.pick("openai") == proxy_id(PROXY)
    net.mode = "auto"
    assert net.pick("openai") == DIRECT
    asyncio.run(net.registry.aclose())


def test_disabled_plane_never_reranks():
    net = plane_for(ClashConfig())                      # enabled: false
    assert net.smart is False
    net.scores[("openai", proxy_id(PROXY))] = EgressScore(
        proxy_id(PROXY), "openai", ok=True, delay_ms=1, probes=1)
    assert net.pick("openai") == DIRECT
    provs = [ProviderSpec(name="openai", base_url="http://api.test/v1", keys=["k"],
                          models={"m": "m"})]
    assert Pool(provs).candidates("m", "priority", net=net)[0].provider.name == "openai"
    asyncio.run(net.registry.aclose())


def test_smart_rerank_beats_static_priority():
    """A provider with better measured egress outranks a higher-priority dead one."""
    a = ProviderSpec(name="fast", base_url="http://fast.test/v1", keys=["k1"],
                     models={"m": "m"}, priority=1)
    b = ProviderSpec(name="dead", base_url="http://dead.test/v1", keys=["k2"],
                     models={"m": "m"}, priority=99)
    net = plane_for(cfg())
    net.scores[("fast", DIRECT)] = EgressScore(DIRECT, "fast", ok=True, delay_ms=30,
                                               probes=1)
    net.scores[("dead", DIRECT)] = EgressScore(DIRECT, "dead", ok=False, delay_ms=3000,
                                               consecutive_fail=2, fail_ratio=1.0,
                                               probes=2)
    pool = Pool([a, b])
    assert [s.provider.name for s in pool.candidates("m", "priority")] == ["dead", "fast"]
    assert [s.provider.name for s in pool.candidates("m", "priority", net=net)] == \
        ["fast", "dead"]
    asyncio.run(net.registry.aclose())


def test_probe_uses_clash_delay_for_nodes_and_real_timing_for_direct():
    seen = []

    def h(request):
        url = str(request.url)
        seen.append(url)
        if "nope.invalid" in url:
            raise httpx.ConnectError("getaddrinfo failed", request=request)
        if request.url.path.endswith("/delay"):
            return httpx.Response(200, json={"delay": 33})
        return httpx.Response(200)
    c = cfg()
    shared = httpx.AsyncClient(transport=httpx.MockTransport(h))
    reg = EgressRegistry(shared, c, client_factory=lambda p: httpx.AsyncClient(
        transport=httpx.MockTransport(h)))
    ctl = controller(c, h)
    probe = NetProbe(c, ctl, reg)
    reg.add(node_id("PROXY-GRP", "hk-1"), PROXY)

    async def run():
        assert await probe.measure(node_id("PROXY-GRP", "hk-1"),
                                   "http://api.test/v1") == (True, 33.0, "")
        ok, ms, err = await probe.measure(DIRECT, "http://api.test/v1")
        assert ok is True and ms >= 0 and err == ""
        bad, bad_ms, bad_err = await probe.measure(DIRECT, "http://nope.invalid/")
        assert bad is False and bad_err
        await reg.aclose()
        await ctl.close()
    asyncio.run(run())
    assert any("/proxies/hk-1/delay" in u for u in seen)


def test_refresh_scores_every_pair_and_survives_a_dead_controller():
    def h(request):
        return httpx.Response(200, json={"proxies": {}} if request.url.path
                              .startswith("/proxies") else {"ok": True})
    net = plane_for(cfg(), h)
    net.registry.add(proxy_id(PROXY), PROXY)
    net.set_targets([ProviderSpec(name="openai", base_url="http://api.test/v1",
                                  keys=["k"], models={"m": "m"})])

    async def run():
        res = await net.refresh()
        assert res["probes"] == 2 and res["alive"] is True
        assert {k[1] for k in net.scores} == {DIRECT, proxy_id(PROXY)}
        assert all(s.ok for s in net.scores.values())
        await net.registry.aclose()
        await net.controller.close()
    asyncio.run(run())

    def dead(request):
        raise httpx.ConnectError("refused", request=request)
    net2 = plane_for(cfg(), dead)
    net2.set_targets([ProviderSpec(name="openai", base_url="http://api.test/v1",
                                   keys=["k"], models={"m": "m"})])

    async def run2():
        res = await net2.refresh()
        assert res["alive"] is False and net2.pick("openai") == DIRECT
        await net2.registry.aclose()
        await net2.controller.close()
    asyncio.run(run2())


def test_discover_paths_registers_nodes_of_tracked_groups_only():
    def h(request):
        return httpx.Response(200, json={"proxies": {
            "PROXY-GRP": {"type": "Selector", "now": "hk-1", "hidden": False,
                           "all": ["hk-1", "jp-2"]},
            "AUTO": {"type": "URLTest", "hidden": True, "all": ["x"]}}})
    net = plane_for(cfg(groups=["PROXY-GRP"]), h)

    async def run():
        added = await net.discover_paths()
        assert node_id("PROXY-GRP", "jp-2") in added
        assert node_id("PROXY-GRP", "hk-1") in net.registry.paths()
        assert not any(p.startswith("node:AUTO") for p in net.registry.paths())
        await net.registry.aclose()
        await net.controller.close()
    asyncio.run(run())


def test_state_snapshot_describes_the_plane():
    net = plane_for(cfg())
    st = net.state()
    assert st["enabled"] is True and st["smart"] is True
    assert st["controller"] == "http://clash.test:9090"
    assert st["proxy"] == PROXY and DIRECT in st["paths"]
    asyncio.run(net.registry.aclose())


# ---- gateway wiring ------------------------------------------------------
@pytest.fixture()
def live(tmp_path, monkeypatch):
    """App with clash enabled: mocked clients, no background task, no network."""
    CALLS.clear()
    PROXY_BUILDS.clear()
    patch_clients(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path, cfg()))) as c:
        yield c


def _set(net, egress, ok=True, ms=100.0, fails=0.0, consec=0):
    net.scores[("openai", egress)] = EgressScore(egress, "openai", ok=ok, delay_ms=ms,
                                                 fail_ratio=fails,
                                                 consecutive_fail=consec, probes=1)


def test_lifespan_builds_the_plane_only_when_enabled(live):
    st = live.app.state.llm
    assert st.settings.clash.enabled is True
    assert isinstance(st.clash, ClashController) and isinstance(st.net, NetPlane)
    assert st.egress is st.net.registry and st.egress.client_for(DIRECT) is st.http
    assert st.router.net is st.net and st.router.egress is st.egress


def test_disabled_config_leaves_no_net_plane_and_no_extra_header(tmp_path, monkeypatch):
    CALLS.clear()
    patch_clients(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as c:   # ClashConfig() = off
        st = c.app.state.llm
        assert st.clash is None and st.net is None and st.egress is None
        r = c.post("/v1/chat/completions", headers=H, json={
            "model": "demo", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        assert "x-socket-egress" not in r.headers      # zero behaviour change
        assert [v for v, _u in CALLS] == ["direct"]
        assert c.get("/internal/net/probes", headers=H).status_code == 405
        assert c.post("/internal/net/probes", headers=H, json={}).status_code == 409
        assert "NET_ON=0" in c.get("/").text.replace(" ", "")


def test_chat_egress_header_and_usage_column(live):
    net = live.app.state.llm.net
    _set(net, DIRECT, ms=500.0)
    _set(net, proxy_id(PROXY), ms=25.0)
    r = live.post("/v1/chat/completions", headers=H, json={
        "model": "demo", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.headers["x-socket-egress"] == proxy_id(PROXY)
    assert any(v == "proxy" for v, _u in CALLS)
    assert PROXY_BUILDS == [PROXY]                    # one client per egress path
    assert live.app.state.llm.usage.recent(1)[0]["egress"] == proxy_id(PROXY)


def test_chat_falls_back_to_direct_when_proxy_is_dead(live):
    net = live.app.state.llm.net
    _set(net, proxy_id(PROXY), ok=False, ms=2000.0, fails=1.0, consec=2)
    CALLS.clear()
    r = live.post("/v1/chat/completions", headers=H, json={
        "model": "demo", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and r.headers["x-socket-egress"] == DIRECT
    assert [v for v, _u in CALLS] == ["direct"]


def test_embeddings_carry_the_egress(live):
    net = live.app.state.llm.net
    _set(net, DIRECT, ms=800.0)
    _set(net, proxy_id(PROXY), ms=5.0)
    e = live.post("/v1/embeddings", headers=H,
                  json={"model": "demo-embed", "input": "x"})
    assert e.status_code == 200 and e.headers["x-socket-egress"] == proxy_id(PROXY)


def test_smart_mode_reorders_candidates_for_a_dead_high_priority_provider(live):
    st = live.app.state.llm
    st.settings.providers.append(ProviderSpec(
        name="slow", base_url="http://slow.test/v1", keys=["sk-slow-AAAA"],
        models={"demo": "d2"}, priority=50))
    st.pool = Pool(st.settings.providers)
    st.attach_router()
    st.net.scores[("slow", DIRECT)] = EgressScore(DIRECT, "slow", ok=False,
                                                  delay_ms=3000, consecutive_fail=2,
                                                  fail_ratio=1.0, probes=2)
    _set(st.net, DIRECT, ms=60.0)
    order = [s.provider.name for s in st.pool.candidates("demo", "priority",
                                                         net=st.net)]
    assert order == ["openai", "slow"]                # health beats static priority
    plain = [s.provider.name for s in st.pool.candidates("demo", "priority")]
    assert plain == ["slow", "openai"]                # unchanged without the plane


# ---- discreet /internal/net surface --------------------------------------
def test_net_routes_are_absent_from_openapi_and_docs(live):
    paths = live.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if p.startswith("/internal")]
    assert "/internal/net/state" not in live.get("/docs").text


def test_net_routes_require_the_master_key(live):
    assert live.get("/internal/net/state").status_code == 401
    assert live.get("/internal/net/state", headers=H).status_code == 200


def test_net_state_and_scores_endpoints(live):
    net = live.app.state.llm.net
    _set(net, DIRECT, ms=42.0)
    st = live.get("/internal/net/state", headers=H).json()
    assert st["enabled"] is True and st["mode"] == "auto"
    assert st["controller"] == "http://clash.test:9090" and st["proxy"] == PROXY
    rows = live.get("/internal/net/scores", headers=H).json()["scores"]
    assert rows[0]["egress"] == DIRECT and rows[0]["delay_ms"] == 42.0


def test_net_probes_endpoint_refreshes_scores(live):
    res = live.post("/internal/net/probes", headers=H, json={}).json()
    assert res["probes"] >= 1 and res["alive"] is True
    assert live.app.state.llm.net.scores
    job = live.post("/internal/net/probes?async=1", headers=H, json={})
    assert job.status_code == 200 and job.json()["accepted"] is True


def test_net_mode_endpoint_validates(live):
    assert live.post("/internal/net/mode", headers=H,
                     json={"mode": "proxy"}).json()["mode"] == "proxy"
    assert live.app.state.llm.net.mode == "proxy"
    assert live.post("/internal/net/mode", headers=H,
                     json={"mode": "nope"}).status_code == 400
    assert live.post("/internal/net/mode", headers=H).status_code == 400


def test_net_select_endpoint_pins_a_group(live):
    r = live.put("/internal/net/select", headers=H,
                 json={"group": "PROXY-GRP", "node": "hk-1"})
    assert r.status_code == 200 and r.json()["egress"] == node_id("PROXY-GRP", "hk-1")
    assert live.put("/internal/net/select", headers=H, json={"group": "x"}).status_code == 400


def test_net_flush_endpoint_reaches_the_controller(live):
    r = live.post("/internal/net/flush", headers=H, json={"dns": True})
    assert r.status_code == 200 and r.json()["flushed"] is True
    assert live.post("/internal/net/flush", headers=H, json={"dns": False}).json() == \
        {"flushed": False}


def test_net_egress_preview_endpoint(live):
    net = live.app.state.llm.net
    _set(net, DIRECT, ms=300.0)
    _set(net, proxy_id(PROXY), ms=10.0)
    d = live.get("/internal/net/egress/openai", headers=H).json()
    assert d["pick"] == proxy_id(PROXY) and len(d["paths"]) == 2
    assert d["registry"][0]["egress"] == DIRECT and d["registry"][0]["shared"] is True
    assert live.get("/internal/net/egress/ghost", headers=H).status_code == 404


def test_net_endpoints_report_clash_down_without_500(live):
    def dead(request):
        raise httpx.ConnectError("connection refused", request=request)
    ctl = live.app.state.llm.net.controller
    old_client = ctl.http
    ctl.http = httpx.AsyncClient(transport=httpx.MockTransport(dead), trust_env=False)
    assert live.get("/internal/net/proxies", headers=H).json()["error"]
    assert live.post("/internal/net/flush", headers=H,
                     json={"dns": True}).status_code == 503
    assert live.put("/internal/net/select", headers=H,
                    json={"group": "g", "node": "n"}).status_code == 503
    asyncio.run(old_client.aclose())
    asyncio.run(ctl.http.aclose())


def test_dashboard_glyph_hidden_when_disabled(tmp_path, monkeypatch):
    patch_clients(monkeypatch)
    with TestClient(create_app(settings_for(tmp_path))) as c:
        html = c.get("/").text
        assert "net_glyph" in html and "NET_ON=0" in html.replace(" ", "")
        assert "class=\"netglyph off\"" in html
    with TestClient(create_app(settings_for(tmp_path, cfg()))) as c:
        html = c.get("/").text
        assert "NET_ON=1" in html.replace(" ", "")
        for skin in ("skin-a", "skin-b", "skin-c", "skin-d"):
            assert skin in html                       # four skins still intact


def test_clash_config_parses_and_defaults_off(tmp_path):
    import yaml
    db = str(tmp_path / "u.sqlite3").replace("\\", "/")
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "db_path": db, "providers": [{"name": "openai", "base_url": "http://a/v1",
                                      "keys": ["k"], "models": {"m": "m"}}],
        "clash": {"enabled": True, "controller": "http://127.0.0.1:9090",
                  "secret": "${CLASH_SECRET}", "mixed_port": 7897,
                  "groups": ["PROXY-GRP"], "provider_proxy": {"openai": "PROXY-GRP"},
                  "mode": "auto", "smart": True, "interval": 30, "timeout": 4}}),
        encoding="utf-8")
    os.environ["CLASH_SECRET"] = "topsecret"
    try:
        s = load_config(tmp_path / "config.yaml")
    finally:
        os.environ.pop("CLASH_SECRET", None)
    assert s.clash.enabled and s.clash.secret == "topsecret"
    assert s.clash.proxy_url_for() == "http://127.0.0.1:7897"
    assert s.clash.groups == ["PROXY-GRP"] and s.clash.provider_proxy["openai"]
    assert s.clash.interval == 30.0 and s.clash.timeout == 4.0
    assert ClashConfig().enabled is False             # default off for old configs
    assert clash_from_raw(None).enabled is False
    for bad in ({"enabled": True, "controller": "nope"}, {"mode": "weird"},
                {"interval": 0.2}, {"fail_ratio": 2}, {"provider_proxy": "x"}):
        with pytest.raises(ValueError):
            clash_from_raw(bad)


def test_usage_migrates_the_egress_column_in_place(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.sqlite3")
    legacy = ("CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY AUTOINCREMENT,"
              " ts REAL, alias TEXT, upstream TEXT, provider TEXT, key TEXT,"
              " status INTEGER, ms REAL, prompt INTEGER, completion INTEGER,"
              " total INTEGER, stream INTEGER, error TEXT)")
    sqlite3.connect(path).execute(legacy).close()
    u = Usage(path)                                   # v0.1 db -> gains egress
    u.log(alias="demo", provider="openai", key="k", status=200, egress=proxy_id(PROXY))
    assert u.recent(1)[0]["egress"] == proxy_id(PROXY)
    u.close()


def test_streaming_response_carries_egress_and_logs_it(live):
    net = live.app.state.llm.net
    _set(net, DIRECT, ms=600.0)
    _set(net, proxy_id(PROXY), ms=8.0)
    with live.stream("POST", "/v1/chat/completions", headers=H, json={
            "model": "demo", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}) as r:
        assert r.status_code == 200
        assert r.headers["x-socket-egress"] == proxy_id(PROXY)
        list(r.iter_raw())
    row = live.app.state.llm.usage.recent(1)[0]
    assert row["egress"] == proxy_id(PROXY) and row["stream"] == 1
    assert row["total"] == 5                          # usage sniffed from the SSE
    assert any(v == "proxy" for v, _u in CALLS)


def test_reload_rebuilds_the_net_plane(tmp_path, monkeypatch):
    """POST /admin/reload must not leave a stale plane behind."""
    import yaml
    patch_clients(monkeypatch)
    db = str(tmp_path / "u.sqlite3").replace("\\", "/")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "db_path": db, "master_keys": ["sk-test"],
        "providers": [{"name": "openai", "base_url": "http://api.test/v1",
                       "keys": ["sk-up-AAAA"], "models": {"demo": "demo-up"}}],
        "clash": cfg_dict(interval=42)}), encoding="utf-8")
    monkeypatch.setenv("SMSSOCKET_CONFIG", str(cfg_file))
    with TestClient(create_app(settings_for(tmp_path, cfg()))) as c:
        st = c.app.state.llm
        first = st.net
        assert first.cfg.interval == 99999
        r = c.post("/admin/reload", headers=H)
        assert r.status_code == 200 and r.json()["net_plane"] is True
        assert st.net is not first and st.net.cfg.interval == 42.0
        assert st.router.net is st.net and st.egress is st.net.registry
        cfg_file.write_text(yaml.safe_dump({
            "db_path": db, "master_keys": ["sk-test"],
            "providers": [{"name": "openai", "base_url": "http://api.test/v1",
                           "keys": ["sk-up-AAAA"], "models": {"demo": "demo-up"}}]}),
            encoding="utf-8")
        assert c.post("/admin/reload", headers=H).json()["net_plane"] is False
        assert st.net is None and st.clash is None     # disabled -> plane torn down
        assert st.router.net is None


def test_background_refresher_starts_and_stops():
    """lifespan owns one task; stop() cancels it and closes the proxy clients."""
    net = plane_for(cfg(interval=99999))

    async def run():
        net.start()
        assert net._task is not None and not net._task.done()
        net.start()                                   # idempotent
        await asyncio.sleep(0.05)                     # let the first refresh run
        assert net.last_refresh > 0
        await net.stop()
        assert net._task is None
    asyncio.run(run())


def cfg_dict(**kw):
    """clash: block as it would appear in config.yaml (for reload tests)."""
    c = cfg(**kw)
    return {"enabled": c.enabled, "controller": c.controller, "secret": c.secret,
            "mixed_port": c.mixed_port, "interval": c.interval, "timeout": c.timeout,
            "mode": c.mode, "smart": c.smart}
