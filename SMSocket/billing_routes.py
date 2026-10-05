"""Billing admin routes: currency of record, rate table, live rate refresh."""
from __future__ import annotations

import json
import urllib.request

from fastapi import APIRouter, HTTPException, Request

from .billing import DEFAULT_RATES, norm, parse_rates
from .config import load_config

MAX_RATES = 200


def billing_map(val) -> dict:
    """UI payload -> config `billing:` block."""
    if not isinstance(val, dict):
        raise HTTPException(400, detail={"error": {"message": "billing must be an object",
                                                   "type": "invalid_request_error"}})
    out: dict = {}
    for k in ("currency", "base"):
        if val.get(k):
            out[k] = norm(str(val[k]))
    if val.get("precision") is not None:
        p = int(val["precision"])
        if not 0 <= p <= 12:
            raise HTTPException(400, detail={"error": {"message": "precision must be 0..12",
                                                       "type": "invalid_request_error"}})
        out["precision"] = p
    if "rates_url" in val:
        url = str(val.get("rates_url") or "").strip()
        if url and not url.startswith("http"):
            raise HTTPException(400, detail={"error": {"message": "rates_url must start with http",
                                                       "type": "invalid_request_error"}})
        out["rates_url"] = url
    rates = val.get("rates")
    if isinstance(rates, dict):
        clean = {}
        for k, v in rates.items():
            if v in (None, ""):
                continue
            try:
                clean[norm(str(k))] = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, detail={"error": {
                    "message": f"rate for {k} must be a number", "type": "invalid_request_error"}})
        if len(clean) > MAX_RATES:
            raise HTTPException(400, detail={"error": {"message": "too many rates",
                                                       "type": "invalid_request_error"}})
        out["rates"] = clean
    return out


def fetch_rates(url: str, timeout: float = 8.0) -> dict[str, float]:
    """Pull a rate table from the internet (no API key needed for the defaults)."""
    if not url:
        return {}
    req = urllib.request.Request(url, headers={"user-agent": "SMSocket/0.3"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))
    rates = parse_rates(data)
    if not rates:
        raise HTTPException(422, detail={"error": {"message": "no rates found in response",
                                                   "type": "invalid_request_error"}})
    return rates


def build(st, dep) -> APIRouter:
    """Mounted by admin.build(); `dep` = master-key dependency list."""
    r = APIRouter(dependencies=dep)

    @r.get("/admin/billing")
    async def get_billing():
        b = st.settings.billing
        return {"billing": b.as_config(), "table": b.table(),
                "defaults": DEFAULT_RATES,
                "pricing": st.settings.pricing,
                "currency": b.currency, "symbol": b.symbol()}

    @r.put("/admin/billing")
    async def put_billing(request: Request):
        """Set the billing currency / rate table; writes config and hot-applies."""
        from .admin import cfg_path, read_raw, write_raw
        body = await request.json()
        block = billing_map(body.get("billing") if isinstance(body, dict)
                            and "billing" in body else body)
        if not block:
            raise HTTPException(400, detail={"error": {"message": "nothing to set",
                                                       "type": "invalid_request_error"}})
        path = cfg_path()
        data = read_raw(path)
        cur = data.get("billing") or {}
        if isinstance(cur.get("rates"), dict) and isinstance(block.get("rates"), dict):
            merged = dict(cur["rates"])
            merged.update(block["rates"])
            block = {**cur, **block, "rates": merged}
        else:
            block = {**cur, **block}
        data["billing"] = block
        data["currency"] = norm(block.get("currency") or data.get("currency") or "USD")
        if "pricing" in body:
            from .admin import pricing_map
            data["pricing"] = pricing_map(body["pricing"])
        write_raw(path, data)
        from .admin import apply_state
        applied = apply_state(st)          # rebuilds router + gate on the new settings
        b = st.settings.billing            # apply_state swaps in the reloaded Settings
        return {"ok": True, "billing": b.as_config(), "table": b.table(),
                "pricing": st.settings.pricing, "applied": applied}

    @r.post("/admin/billing/rates/refresh")
    async def refresh_rates(request: Request):
        """Re-pull exchange rates (billing.rates_url, or ?url= override)."""
        url = ""
        try:
            body = await request.json()
            url = str((body or {}).get("url") or "")
        except Exception:
            pass
        b = st.settings.billing
        url = url or b.rates_url
        if not url:
            raise HTTPException(400, detail={"error": {
                "message": "billing.rates_url is not configured",
                "type": "invalid_request_error"}})
        rates = fetch_rates(url)
        from .admin import cfg_path, read_raw, write_raw
        path = cfg_path()
        data = read_raw(path)
        block = data.get("billing") or {}
        merged = dict(block.get("rates") or b.rates)
        merged.update(rates)
        data["billing"] = {**block, "rates": merged}
        write_raw(path, data)
        from .admin import apply_state
        apply_state(st)
        return {"ok": True, "updated": len(rates), "source": url,
                "billing": st.settings.billing.as_config()}

    @r.get("/admin/billing/convert")
    async def convert(amount: float = 1.0, frm: str = "USD", to: str = ""):
        """Dry-run a conversion against the live rate table."""
        b = st.settings.billing
        dst = norm(to) if to else b.currency
        rate = b.rate(frm, dst)
        if not rate:
            raise HTTPException(404, detail={"error": {
                "message": f"unknown currency: {frm if not rate else dst}",
                "type": "invalid_request_error"}})
        return {"amount": amount, "from": norm(frm), "to": dst, "rate": round(rate, 8),
                "converted": b.convert(amount, frm, dst), "symbol": b.symbol(dst)}

    return r
