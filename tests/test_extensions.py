"""扩展程序：唯一识别码 · 导入验证 · 自带面板挂载（总设置 → 扩展程序）。

覆盖两条链：
1) 未配对的 bundle 导入 → 403，面板不挂载（GET panel → 403）；
2) 配对（把本机识别码写进 asset/SMSocket.identity）后导入 → 验证通过，
   自带面板出现在「扩展程序条目」里并可取回 HTML。
"""
import json
import re

import pytest
from fastapi.testclient import TestClient

from SMSocket import extensions, identity
from SMSocket.config import load_config
from SMSocket.gateway import create_app

CFG_TMPL = """listen: 127.0.0.1:8011
db_path: "{db}"
providers:
  - name: Alpha
    base_url: https://a.example/v1
    keys: [sk-alpha-secret-1]
    models: {{alpha-large: alpha-large-v1}}
"""

PANEL = "<!doctype html><title>SMSC panel</title><body>SMSC 自带面板</body>"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CFG_TMPL.format(db=str(tmp_path / "u.sqlite3").replace("\\", "/")),
                   encoding="utf-8")
    monkeypatch.setenv("SMSSOCKET_CONFIG", str(cfg))
    monkeypatch.setenv("SMSSOCKET_MASTER_KEY", "sk-ext")
    monkeypatch.setenv("SMSSOCKET_IDENTITY_FILE", str(tmp_path / "smsocket.identity"))
    client = TestClient(create_app(load_config(cfg)))
    bundle = tmp_path / "smsc"
    (bundle / "asset").mkdir(parents=True)
    (bundle / "asset" / "panel.html").write_text(PANEL, encoding="utf-8")
    (bundle / "asset" / "extension.json").write_text(json.dumps(
        {"name": "smsc", "title": "SMSC 网络平面面板", "version": "1.0",
         "panel": "panel.html"}, ensure_ascii=False), encoding="utf-8")
    return client, cfg, bundle, tmp_path


def H():
    return {"Authorization": "Bearer sk-ext"}


def test_install_id_is_stable_and_masked(tmp_path, monkeypatch):
    monkeypatch.setenv("SMSSOCKET_IDENTITY_FILE", str(tmp_path / "id"))
    first = identity.install_id()
    assert re.fullmatch(r"SMSOCKET(-[0-9A-F]{4}){5}", first), first
    assert identity.install_id() == first              # 重启后同一枚码
    masked = identity.mask(first)
    assert "…" in masked and first not in masked


def test_verify_needs_the_code_in_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("SMSSOCKET_IDENTITY_FILE", str(tmp_path / "id"))
    bundle = tmp_path / "b"
    (bundle / "asset").mkdir(parents=True)
    assert identity.verify(bundle)["verified"] is False
    assert identity.verify(bundle)["present"] is False
    (bundle / "asset" / "SMSocket.identity").write_text("SMSOCKET-DEAD-BEEF-DEAD-BEEF1234\n",
                                                        encoding="utf-8")
    v = identity.verify(bundle)
    assert v["verified"] is False and v["present"] is True and "不一致" in v["reason"]
    assert identity.write_asset_id(bundle)["ok"] is True
    assert identity.verify(bundle)["verified"] is True


def test_import_rejects_unpaired_bundle(env):
    client, cfg, bundle, _ = env
    r = client.post("/admin/extensions/import", json={"path": str(bundle)}, headers=H())
    assert r.status_code == 403, r.text
    assert "识别码" in r.text
    assert client.get("/admin/extensions", headers=H()).json()["items"] == []


def test_pair_then_import_mounts_the_panel(env):
    client, cfg, bundle, _ = env
    r = client.post("/admin/extensions/pair", json={"path": str(bundle)}, headers=H())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paired"]["ok"] is True
    item = body["item"]
    assert item["verified"] is True and item["panel_ready"] is True
    assert item["title"] == "SMSC 网络平面面板"
    # 识别码落进了 SMSC 的 asset 文件夹
    assert (bundle / "asset" / "SMSocket.identity").read_text(encoding="utf-8").strip() \
        == identity.install_id()
    # 自带面板可取回
    p = client.get("/admin/extensions/smsc/panel", headers=H())
    assert p.status_code == 200 and "SMSC 自带面板" in p.text
    # 注册表持久化进 config.yaml
    assert "extensions:" in cfg.read_text(encoding="utf-8")
    assert load_config(cfg).extensions[0].name == "smsc"
    # 列表视图：已挂载计数 + 掩码识别码
    lst = client.get("/admin/extensions", headers=H()).json()
    assert lst["mounted"] == 1 and "…" in lst["identity"]


def test_unverified_bundle_panel_is_403(env):
    client, cfg, bundle, _ = env
    client.post("/admin/extensions/pair", json={"path": str(bundle)}, headers=H())
    (bundle / "asset" / "SMSocket.identity").write_text("SMSOCKET-0000-0000-0000-0000FFFF\n",
                                                        encoding="utf-8")
    lst = client.get("/admin/extensions", headers=H()).json()
    assert lst["items"][0]["verified"] is False and lst["mounted"] == 0
    assert client.get("/admin/extensions/smsc/panel", headers=H()).status_code == 403


def test_remove_unmounts(env):
    client, cfg, bundle, _ = env
    client.post("/admin/extensions/pair", json={"path": str(bundle)}, headers=H())
    r = client.post("/admin/extensions/remove", json={"name": "smsc"}, headers=H())
    assert r.status_code == 200 and r.json()["count"] == 0
    assert client.get("/admin/extensions/smsc/panel", headers=H()).status_code == 404


def test_panel_path_stays_inside_bundle(tmp_path, monkeypatch):
    """manifest 把面板指向 bundle 之外 → 400，绝不送达宿主目录外的文件。"""
    from fastapi import HTTPException

    from SMSocket.config import ExtensionSpec
    monkeypatch.setenv("SMSSOCKET_IDENTITY_FILE", str(tmp_path / "id"))
    bundle = tmp_path / "b"
    (bundle / "asset").mkdir(parents=True)
    (tmp_path / "evil.html").write_text("<p>outside</p>", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        extensions.panel_path(bundle, ExtensionSpec(name="evil", path=str(bundle)),
                              {"panel": "../../evil.html"})
    assert exc.value.status_code == 400
    (bundle / "asset" / "panel.html").write_text("<p>ok</p>", encoding="utf-8")
    p = extensions.panel_path(bundle, ExtensionSpec(name="ok", path=str(bundle)),
                              {"panel": "panel.html"})
    assert p == (bundle / "asset" / "panel.html")


def test_dashboard_has_the_extension_ui(env):
    client, _, _, _ = env
    html = client.get("/").text
    for marker in ('<h2>扩展程序</h2>', '导入扩展程序', 'id="x_path"', '扩展程序路径',
                   'id="x_list"', '扩展程序条目', 'openSmsc()', '打开 SMSC',
                   '/admin/extensions', 'mountPanel', 'pairExt', 'importExt'):
        assert marker in html, marker
    # 层级：导入扩展程序 在 扩展程序 条目下，路径输入框在其下
    page = html
    assert page.index("<h2>扩展程序</h2>") < page.index("导入扩展程序")
    assert page.index("导入扩展程序") < page.index('id="x_path"')
    assert page.index('id="x_path"') < page.index("SMSC 网络平面")
    # 打开 SMSC 按钮在 SMSC 网络平面表单里
    assert page.index('SMSC 网络平面（智能分流）') < page.index('onclick="openSmsc()"')
