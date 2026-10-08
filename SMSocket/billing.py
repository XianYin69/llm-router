"""Billing: currency of record, exchange-rate table, cost conversion.

Config block (all optional):

  billing:
    currency: CNY          # currency every total is displayed in
    base: USD              # every rate below is "units of currency per 1 base"
    precision: 6
    rates: {USD: 1, CNY: 7.1, EUR: 0.92}
    rates_url: https://... # optional live table (GET -> {"USD":1,...} or {"rates":{...}})

`pricing:` rows keep their own currency (default = billing.base), so a provider
priced in USD and a provider priced in CNY can be summed into one display
currency. Amounts are never re-rounded mid-chain: convert() works in float and
rounds once at the end.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Static fallback table: units per 1 USD. Deliberately approximate - override in
# config (billing.rates) or refresh live via /admin/billing/rates/refresh.
DEFAULT_RATES = {
    "USD": 1.0, "CNY": 7.1, "EUR": 0.92, "GBP": 0.78, "JPY": 155.0,
    "HKD": 7.8, "KRW": 1350.0, "RUB": 92.0, "TWD": 32.0, "SGD": 1.35,
    "AUD": 1.5, "CAD": 1.37, "INR": 83.0, "BRL": 5.4, "AED": 3.67,
    "SAR": 3.75, "THB": 36.0, "VND": 25400.0, "MYR": 4.7, "IDR": 15800.0,
    "CHF": 0.88, "NZD": 1.64, "SEK": 10.6, "PLN": 4.0, "TRY": 34.0,
    "UAH": 41.5, "PHP": 58.0, "MXN": 19.5, "ZAR": 18.6, "ILS": 3.7,
}
SYMBOLS = {"USD": "$", "CNY": "¥", "JPY": "¥", "EUR": "€", "GBP": "£", "KRW": "₩",
           "HKD": "HK$", "TWD": "NT$", "AUD": "A$", "CAD": "C$", "SGD": "S$",
           "VND": "₫", "INR": "₹", "THB": "฿", "RUB": "₽", "BRL": "R$",
           "MXN": "MX$", "ZAR": "R", "ILS": "₪", "TRY": "₺", "PLN": "zł",
           "SEK": "kr", "CHF": "CHF", "NZD": "NZ$", "MYR": "RM", "IDR": "Rp",
           "AED": "AED", "SAR": "SAR", "UAH": "₴", "PHP": "₱"}


def norm(cur: str | None, default: str = "USD") -> str:
    c = str(cur or default).strip().upper()
    return c or default


@dataclass
class Billing:
    currency: str = "USD"          # display currency of record
    base: str = "USD"              # what rates are expressed against
    rates: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RATES))
    precision: int = 6
    rates_url: str = ""

    # ---- rates -------------------------------------------------------------
    def per_base(self, cur: str) -> float:
        """Units of `cur` per 1 unit of `base`."""
        c = norm(cur)
        if c == norm(self.base):
            return 1.0
        r = self.rates.get(c)
        if r is None:
            return 1.0 if c == "USD" and self.base == "USD" else 0.0
        b = self.rates.get(norm(self.base)) or 1.0
        return float(r) / float(b)

    def rate(self, frm: str, to: str) -> float:
        """Multiplicative factor frm -> to (0.0 = unknown currency)."""
        f, t = norm(frm), norm(to)
        if f == t:
            return 1.0
        pf, pt = self.per_base(f), self.per_base(t)
        return pt / pf if pf else 0.0

    def convert(self, amount: float, frm: str, to: str | None = None) -> float:
        to = to or self.currency
        return round(float(amount or 0.0) * self.rate(frm, to), self.precision)

    def symbol(self, cur: str | None = None) -> str:
        return SYMBOLS.get(norm(cur), norm(cur))

    # ---- cost --------------------------------------------------------------
    def cost(self, prompt: int, completion: int, row: dict | None,
             pricing_currency: str | None = None, cached: int = 0,
             cache_write: int = 0) -> dict:
        """Price a call from a pricing row -> amount in row currency + display.

        `cached` tokens are billed at the row's `cache_read` rate instead of the
        full input rate (defect #2: that rate was read into a variable and then
        ignored, so the cache discount was structurally always 0). `cache_write`
        uses `cache_write`/`cache_creation` when the row prices it, else the
        input rate - never free, never invented.
        """
        row = row or {}
        src = norm(pricing_currency or row.get("currency") or row.get("cur") or self.base)
        p = float(row.get("prompt", 0) or 0)
        c = float(row.get("completion", 0) or 0)
        cache = float(row.get("cache_read", row.get("cached", 0)) or 0)
        cw_rate = float(row.get("cache_write", row.get("cache_creation", 0)) or 0) or p
        req = float(row.get("request", row.get("per_request", 0)) or 0)
        # cached / cache-write tokens are SUBSETS of prompt: partition it so no
        # token is priced twice (prompt = miss + hit + wt).
        pin = max(0, int(prompt or 0))
        hit = min(max(0, int(cached or 0)), pin)
        wt = min(max(0, int(cache_write or 0)), pin - hit)
        miss = pin - hit - wt
        amount = (miss * p + hit * cache + wt * cw_rate + completion * c) / 1e6 + req
        amount = round(amount, max(self.precision, 8))
        return {"amount": amount, "currency": src,
                "display": self.convert(amount, src), "display_currency": self.currency,
                "rate": self.rate(src, self.currency)}

    def table(self) -> dict:
        return {"currency": self.currency, "symbol": self.symbol(),
                "base": self.base, "precision": self.precision,
                "rates": dict(self.rates), "symbols": dict(SYMBOLS),
                "supported": sorted(self.rates), "rates_url": self.rates_url}

    def as_config(self) -> dict:
        out = {"currency": self.currency, "base": self.base,
               "precision": self.precision, "rates": dict(self.rates)}
        if self.rates_url:
            out["rates_url"] = self.rates_url
        return out


def from_raw(raw: dict | None, legacy_currency: str = "USD") -> Billing:
    """Build Billing from the config `billing:` block (or the old `currency:` key)."""
    b = Billing()
    raw = raw or {}
    b.currency = norm(raw.get("currency") or legacy_currency)
    b.base = norm(raw.get("base") or raw.get("base_currency") or "USD")
    b.precision = int(raw.get("precision", 6) or 6)
    b.rates_url = str(raw.get("rates_url") or raw.get("rates_endpoint") or "")
    rates = dict(DEFAULT_RATES)
    user = raw.get("rates") or {}
    if isinstance(user, dict):
        for k, v in user.items():
            try:
                rates[norm(k)] = float(v)
            except (TypeError, ValueError):
                pass
    if b.base not in rates:          # keep the table usable even for exotic bases
        rates[b.base] = 1.0
    b.rates = rates
    if b.currency not in rates:
        rates[b.currency] = 1.0
    return b


def parse_rates(payload) -> dict[str, float]:
    """Accept {CUR: n}, {"rates": {CUR: n}}, {"rates": [{code, value}]} shapes."""
    if isinstance(payload, dict) and isinstance(payload.get("rates"), list):
        out = {}
        for item in payload["rates"]:
            if isinstance(item, dict):
                code = item.get("code") or item.get("currency") or item.get("symbol")
                val = item.get("value") or item.get("rate") or item.get("price")
                if code and val is not None:
                    try:
                        out[norm(code)] = float(val)
                    except (TypeError, ValueError):
                        pass
        return out
    src = payload.get("rates") if isinstance(payload, dict) and "rates" in payload else payload
    if not isinstance(src, dict):
        return {}
    out = {}
    for k, v in src.items():
        try:
            out[norm(k)] = float(v)
        except (TypeError, ValueError):
            pass
    return out
