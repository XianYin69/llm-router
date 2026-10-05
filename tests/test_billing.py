"""Billing tests: currency of record, rate conversion, mixed-currency totals, admin API."""
import json

import pytest
import yaml
from fastapi.testclient import TestClient

from SMSocket.billing import Billing, DEFAULT_RATES, from_raw, parse_rates
from SMSocket.config import ProviderSpec, Settings, load_config
from SMSocket.gateway import create_app
from SMSocket.usage import Usage

KEY = "sk-billing-test"
CFG = """listen: 127.0.0.1:8011
db_path: "{db}"
currency: USD
billing:
  currency: CNY
  base: USD
  rates: {USD: 1, CNY: 7.5, EUR: 0.8}
pricing:
  us-model: {prompt: 2.0, completion: 8.0, currency: USD}
  cn-model: {prompt: 1.0, completion: 3.0, currency: CNY}
providers:
  - name: Alpha
    base_url: https://a.example/v1
    keys: [sk-alpha-secret-1]
    models: {us-model: us-model-v1, cn-model: cn-model-v1}
"""


def H():
    return {"authorization": "Bearer " + KEY}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    db = str(tmp_path / "usage.sqlite3").replace("\\", "/")
    cfg.write_text(CFG.replace(chr(123)+chr(100)+chr(98)+chr(125), db), encoding="utf-8")
    monkeypatch.setenv("SMSSOCKET_CONFIG", str(cfg))
    monkeypatch.setenv("SMSSOCKET_MASTER_KEY", KEY)
    monkeypatch.delenv("SMSSOCKET_NO_KEY", raising=False)
    with TestClient(create_app(load_config(cfg))) as c:
        yield c, cfg


# ---- unit: rate table ------------------------------------------------------
def test_convert_rounds_once_and_identity_is_exact():
    b = Billing(currency="CNY", base="USD", rates={"USD": 1.0, "CNY": 7.1})
    assert b.rate("USD", "USD") == 1.0
    assert b.convert(1.0, "USD", "CNY") == 7.1
    assert b.convert(7.1, "CNY", "USD") == 1.0
    assert b.convert(0.001, "USD", "CNY") == 0.0071


def test_unknown_currency_converts_to_zero_not_crash():
    b = Billing(rates={"USD": 1.0})
    assert b.rate("USD", "XXX") == 0.0
    assert b.convert(5, "USD", "XXX") == 0.0


def test_exotic_base_is_usable():
    b = from_raw({"currency": "BTC", "base": "BTC", "rates": {"BTC": 0.00002}})
    assert b.per_base("BTC") == 1.0
    assert b.rate("USD", "BTC") == pytest.approx(0.00002)


def test_from_raw_falls_back_to_legacy_currency_and_defaults():
    b = from_raw(None, "EUR")
    assert b.currency == "EUR" and b.base == "USD"
    assert b.rates["EUR"] == DEFAULT_RATES["EUR"]
    assert b.per_base("EUR") == pytest.approx(DEFAULT_RATES["EUR"])


def test_parse_rates_accepts_shapes():
    assert parse_rates({"USD": 1, "cny": "7.2"}) == {"USD": 1.0, "CNY": 7.2}
    assert parse_rates({"rates": {"USD": 1, "JPY": 150}}) == {"USD": 1.0, "JPY": 150.0}
    assert parse_rates({"rates": [{"code": "USD", "value": 1},
                                  {"currency": "GBP", "rate": "0.78"}]}) == {
        "USD": 1.0, "GBP": 0.78}
    assert parse_rates("nope") == {}


# ---- unit: cost per call ---------------------------------------------------
def test_cost_detail_keeps_pricing_currency_and_adds_display():
    s = Settings(currency="CNY", billing=Billing(currency="CNY", rates={"USD": 1, "CNY": 7.5}),
                 pricing={"m": {"prompt": 2.0, "completion": 8.0, "currency": "USD"}})
    cd = s.cost_detail("m", 1_000_000, 1_000_000)
    assert cd["amount"] == 10.0 and cd["currency"] == "USD"
    assert cd["display"] == 75.0 and cd["display_currency"] == "CNY"
    assert s.cost_of("m", 1_000_000, 0) == 2.0      # legacy float API preserved


def test_cost_detail_handles_per_request_and_missing_row():
    b = Billing(currency="USD")
    s = Settings(billing=b, pricing={"m": {"request": 0.05}})
    assert s.cost_detail("m", 10, 10)["amount"] == 0.05
    assert s.cost_detail("absent", 10, 10)["amount"] == 0.0


# ---- integration: config + admin ------------------------------------------
def test_load_config_reads_billing_block():
    s = Settings()
    assert s.billing.currency == "USD" and s.billing.rates["USD"] == 1.0


def test_snapshot_exposes_billing(client):
    c, _ = client
    snap = c.get("/admin/config", headers=H()).json()
    assert snap["billing"]["currency"] == "CNY"
    assert snap["billing"]["rates"]["CNY"] == 7.5


def test_put_billing_changes_currency_and_merges_rates(client):
    c, cfg = client
    r = c.put("/admin/billing", headers=H(),
              json={"currency": "EUR", "rates": {"EUR": 0.95, "JPY": 160}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["billing"]["currency"] == "EUR"
    assert body["billing"]["rates"]["USD"] == 1.0          # old rows kept
    assert body["billing"]["rates"]["JPY"] == 160.0        # new row added
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["currency"] == "EUR"
    assert c.get("/stats").json()["currency"] == "EUR"


def test_put_billing_rejects_junk(client):
    c, _ = client
    assert c.put("/admin/billing", headers=H(), json={}).status_code == 400
    assert c.put("/admin/billing", headers=H(), json={"rates": {"USD": "abc"}}).status_code == 400
    assert c.put("/admin/billing", headers=H(), json={"precision": 99}).status_code == 400
    assert c.put("/admin/billing", headers=H(), json={"rates_url": "ftp://x"}).status_code == 400


def test_convert_endpoint_dry_run(client):
    c, _ = client
    j = c.get("/admin/billing/convert", headers=H(),
              params={"amount": 10, "frm": "USD", "to": "CNY"}).json()
    assert j["converted"] == 75.0 and j["rate"] == 7.5
    assert c.get("/admin/billing/convert", headers=H(),
                 params={"frm": "USD", "to": "ZZZ"}).status_code == 404


def test_refresh_rates_requires_source(client, monkeypatch):
    c, _ = client
    assert c.post("/admin/billing/rates/refresh", headers=H(), json={}).status_code == 400
    import SMSocket.billing_routes as br
    monkeypatch.setattr(br, "fetch_rates", lambda url, timeout=8.0: {"USD": 1.0, "CNY": 8.2})
    r = c.post("/admin/billing/rates/refresh", headers=H(), json={"url": "https://x/rates"})
    assert r.status_code == 200, r.text
    assert r.json()["billing"]["rates"]["CNY"] == 8.2


# ---- integration: mixed-currency accounting --------------------------------
def test_usage_totals_convert_each_pricing_currency(tmp_path):
    u = Usage(str(tmp_path / "u.sqlite3"))
    b = Billing(currency="CNY", rates={"USD": 1.0, "CNY": 7.5})
    for alias, cur, amt in (("us", "USD", 10.0), ("cn", "CNY", 15.0)):
        disp = b.convert(amt, cur, "CNY")
        u.log(alias=alias, provider="p", key="k", status=200, total=100,
              cost=amt, cost_currency=cur, cost_display=disp, display_currency="CNY")
    rows = {r["currency"]: r for r in u.by_currency()}
    assert rows["USD"]["cost"] == 10.0 and rows["CNY"]["cost"] == 15.0
    assert round(rows["USD"]["display"] + rows["CNY"]["display"], 6) == 90.0
    u.close()


def test_stats_money_block_reports_display_currency(client, tmp_path):
    """Cost written by the router lands in both pricing and display currency."""
    c, _ = client
    st = c.app.state.llm
    cd = st.settings.cost_detail("us-model", 1_000_000, 1_000_000)
    assert cd["amount"] == 10.0 and cd["currency"] == "USD" and cd["display"] == 75.0
    st.usage.log(alias="us-model", provider="Alpha", key="k", status=200, total=3,
                 prompt=1_000_000, completion=1_000_000, cost=cd["amount"],
                 cost_currency=cd["currency"], cost_display=cd["display"],
                 display_currency=cd["display_currency"])
    j = c.get("/stats").json()
    assert j["currency"] == "CNY" and j["symbol"] == "¥"
    assert j["money"]["total"] == 75.0
    assert j["money"]["by_currency"][0]["rate_to_display"] == 7.5
    v = c.get("/v1/usage", headers=H()).json()
    assert v["totals"]["cost"] == 75.0 and v["currency"] == "CNY"
    assert c.get("/v1/usage", headers=H(), params={"currency": "USD"}).json()["totals"]["cost"] == 10.0
