"""Dashboard markup tests: the new panels exist and the page still renders."""
import pytest
from fastapi.testclient import TestClient

from SMSocket.config import load_config
from SMSocket.gateway import create_app

CFG_TMPL = """listen: 127.0.0.1:8011
db_path: {db}
billing:
  currency: CNY
  rates: {USD: 1, CNY: 7.5}
max_concurrency: 6
providers:
  - name: Alpha
    base_url: https://a.example/v1
    keys: [sk-alpha-secret-1]
    models: {alpha-large: alpha-large-v1}
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    db = str(tmp_path / "u.sqlite3").replace("\\", "/")
    cfg.write_text(CFG_TMPL.replace("{db}", '"' + db + '"'), encoding="utf-8")
    monkeypatch.setenv("SMSSOCKET_CONFIG", str(cfg))
    monkeypatch.setenv("SMSSOCKET_MASTER_KEY", "sk-dash")
    with TestClient(create_app(load_config(cfg))) as c:
        yield c


def test_dashboard_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200 and "SMSocket 控制台" in r.text


def test_dashboard_has_the_new_pages(client):
    html = client.get("/").text
    for marker in ('id="page-socket"', 'id="page-discover"', 'data-p="socket"',
                   'data-p="discover"', 'id="s_active"', 'id="s_queued"', 'id="s_rej"',
                   'id="q_body"', 'id="d_prov"', 'id="dres"', 'id="b_rates"',
                   'id="c_out"', 'id="g_maxc"', 'id="g_qw"', 'id="g_ppc"',
                   'id="xactive"', 'id="xcurrency"'):
        assert marker in html, marker


def test_dashboard_js_calls_the_new_endpoints(client):
    html = client.get("/").text
    for call in ("/concurrency", "/v1/batch", "/v1/batches/", "/admin/billing",
                 "/admin/billing/rates/refresh", "/admin/billing/convert",
                 "/admin/discover", "/admin/discover/status", "/admin/models/apply",
                 "/admin/models"):
        assert call in html, call


def test_dashboard_functions_present(client):
    html = client.get("/").text
    for fn in ("function loadSocket", "function runBatch", "function pollJob",
               "function saveBilling", "function fillBilling", "function convertTry",
               "function startDiscover", "function pollDiscover", "function paintDiscover",
               "function applyDiscovered", "function loadCatalog", "function tickSocket"):
        assert fn in html, fn
