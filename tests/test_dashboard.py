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
    for marker in ('id="page-socket"', 'data-p="socket"',
                   'id="s_active"', 'id="s_queued"', 'id="s_rej"',
                   'id="q_body"', 'id="b_rates"',
                   'id="c_out"', 'id="g_maxc"', 'id="g_qw"', 'id="g_ppc"',
                   'id="xactive"', 'id="xcurrency"',
                   # v0.5b: 模型探测 lives inside the provider page as a two-level tree
                   '提供商与大模型', 'id="pmtree"', 'id="pm_prog"', 'id="pm_seen"',
                   'data-p="providers"'):
        assert marker in html, marker


def test_dashboard_js_calls_the_new_endpoints(client):
    html = client.get("/").text
    for call in ("/concurrency", "/v1/batch", "/v1/batches/", "/admin/billing",
                 "/admin/billing/rates/refresh", "/admin/billing/convert",
                 "/admin/discover/status", "/admin/models",
                 "/admin/provider-models", "/admin/provider-models/refresh"):
        assert call in html, call


def test_dashboard_discover_page_is_gone(client):
    # v0.5b: the standalone probe page is gone - probing is automatic
    html = client.get("/").text
    for gone in ('id="page-discover"', 'data-p="discover"', 'function startDiscover',
                 'function paintDiscover', 'function applyDiscovered', '提供商与API'):
        assert gone not in html, gone


def test_dashboard_functions_present(client):
    html = client.get("/").text
    for fn in ("function loadSocket", "function runBatch", "function pollJob",
               "function saveBilling", "function fillBilling", "function convertTry",
               "function loadPM", "function paintPM", "function autoProbe",
               "function pollPM", "function togglePM", "function clearPM",
               "function tickSocket"):
        assert fn in html, fn


def test_settings_page_moves_smsc_under_extensions(client):
    """总设置：原「Clash 网络平面」更名 SMSC 并归入「扩展程序」条目下。

    只改可见文案与层级，控件 id（c_on/c_ctl/…）与 /admin/config 的 clash 键
    是线协议，不能动——所以断言按「标签变了、id 没变」两头钉。
    """
    html = client.get("/").text
    assert "扩展程序" in html
    assert "SMSC 网络平面" in html
    assert "Clash 网络平面" not in html                 # 旧标题彻底退场
    page = html[html.index('id="page-settings"'):html.index('id="page-socket"')]
    assert page.index("扩展程序") < page.index("SMSC 网络平面")   # 层级：条目在上
    assert page.index("SMSC 网络平面") < page.index('<form id="cf"')
    for cid in ('id="c_on"', 'id="c_ctl"', 'id="c_secret"', 'id="c_mode"',
                'id="c_groups"', 'id="c_note"'):
        assert cid in page, cid
    assert 'body={clash:' in html                       # 提交仍打 clash 键
