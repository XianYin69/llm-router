"""Smoke tests with a stubbed upstream (no network)."""
import json
import types

import pytest

from llmrouter import config as cfg
from llmrouter import router as router_mod
from llmrouter import upstream
from llmrouter.app import create_app
from fastapi.testclient import TestClient

YAML = """
master_keys: [sk-test]
providers:
  fake:
    base_url: http://fake/v1
    api_keys: [k1, k2]
    models: [fake-1]
routes:
  demo: [fake/fake-1]
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text(YAML, encoding="utf-8")

    async def fake_chat(provider, key, payload):
        if key == "k1":
            raise upstream.UpstreamError(429, "rate limited", True)
        return {"id": "c1", "object": "chat.completion", "model": payload["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    monkeypatch.setattr(router_mod, "chat", fake_chat)
    app = create_app(p)
    with TestClient(app) as c:
        yield c


def test_auth_required(client):
    assert client.get("/v1/models").status_code == 401
    r = client.get("/v1/models", headers={"Authorization": "Bearer sk-test"})
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["data"]] == ["demo", "fake-1"]


def test_health_and_dashboard(client):
    assert client.get("/healthz").json()["ok"] is True
    assert "llm-router" in client.get("/").text


def test_key_pool_fallback(client):
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-test"},
                    json={"model": "demo", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "demo"                      # public name preserved
    assert body["_routed_via"] == "fake/fake-1"
    assert body["choices"][0]["message"]["content"] == "hi"
    st = client.get("/stats").json()["keys"]["demo"]
    assert st["requests"] == 1 and st["completion_tokens"] == 2
    assert client.get("/pool").json()["fake"]["cooling"] == 1   # k1 parked on 429


def test_unknown_model(client):
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-test"},
                    json={"model": "nope", "messages": []})
    assert r.status_code == 404
