"""
currency.py
Currency conversion utility to standardize receipts and category spend to any preferred user currency
(e.g., LKR, USD, EUR, GBP, INR, AUD, CAD, SGD, AED, etc.).
Supports live exchange rates with local 12-hour file caching and built-in offline fallbacks.
"""

import os
import json
import time
import urllib.request
import urllib.error

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".currency_cache.json")
CACHE_TTL = 12 * 3600  # 12 hours

# Popular supported currencies with symbols
SUPPORTED_CURRENCIES = [
    {"code": "LKR", "name": "Sri Lankan Rupee", "symbol": "Rs"},
    {"code": "USD", "name": "US Dollar", "symbol": "$"},
    {"code": "EUR", "name": "Euro", "symbol": "€"},
    {"code": "GBP", "name": "British Pound", "symbol": "£"},
    {"code": "INR", "name": "Indian Rupee", "symbol": "₹"},
    {"code": "AUD", "name": "Australian Dollar", "symbol": "A$"},
    {"code": "CAD", "name": "Canadian Dollar", "symbol": "C$"},
    {"code": "SGD", "name": "Singapore Dollar", "symbol": "S$"},
    {"code": "AED", "name": "UAE Dirham", "symbol": "AED"},
    {"code": "SAR", "name": "Saudi Riyal", "symbol": "SAR"},
    {"code": "QAR", "name": "Qatari Riyal", "symbol": "QAR"},
    {"code": "JPY", "name": "Japanese Yen", "symbol": "¥"},
    {"code": "CNY", "name": "Chinese Yuan", "symbol": "¥"},
    {"code": "MYR", "name": "Malaysian Ringgit", "symbol": "RM"},
    {"code": "THB", "name": "Thai Baht", "symbol": "฿"},
    {"code": "NZD", "name": "New Zealand Dollar", "symbol": "NZ$"},
    {"code": "CHF", "name": "Swiss Franc", "symbol": "CHF"},
    {"code": "KWD", "name": "Kuwaiti Dinar", "symbol": "KD"},
]

# Reliable default exchange rates to LKR (fallback if offline or API unreachable)
FALLBACK_RATES_TO_LKR = {
    "LKR": 1.0,
    "USD": 331.60,
    "EUR": 355.00,
    "GBP": 425.00,
    "INR": 3.49,
    "AUD": 215.00,
    "CAD": 240.00,
    "SGD": 250.00,
    "JPY": 2.15,
    "AED": 90.30,
    "QAR": 91.00,
    "SAR": 88.40,
    "CNY": 46.50,
    "MYR": 74.00,
    "THB": 9.50,
    "NZD": 195.00,
    "CHF": 375.00,
    "KRW": 0.24,
    "HKD": 42.50,
    "KWD": 1080.00,
    "PKR": 1.19,
}

SYMBOL_MAP = {
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "₹": "INR",
    "INR": "INR",
    "RS": "LKR",
    "RS.": "LKR",
    "SLR": "LKR",
    "SLRS": "LKR",
    "LKR": "LKR",
    "¥": "JPY",
    "JPY": "JPY",
    "A$": "AUD",
    "AUD": "AUD",
    "C$": "CAD",
    "CAD": "CAD",
    "S$": "SGD",
    "SGD": "SGD",
    "AED": "AED",
    "SAR": "SAR",
    "QAR": "QAR",
    "PKR": "PKR",
}

_memory_cache = {
    "rates": dict(FALLBACK_RATES_TO_LKR),
    "timestamp": 0,
    "is_live": False,
}


def normalize_currency(curr_str: str) -> str:
    """Normalize input currency symbol or text into a standard 3-letter code."""
    if not curr_str:
        return "LKR"
    clean = str(curr_str).strip().upper()
    if clean in SYMBOL_MAP:
        return SYMBOL_MAP[clean]
    # Check by stripping common punctuation
    clean_alpha = "".join(ch for ch in clean if ch.isalnum() or ch == "$").upper()
    if clean_alpha in SYMBOL_MAP:
        return SYMBOL_MAP[clean_alpha]
    if len(clean_alpha) == 3:
        return clean_alpha
    return "LKR"


def _load_cached_rates():
    """Load rates from local disk cache if still valid."""
    global _memory_cache
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            ts = data.get("timestamp", 0)
            if time.time() - ts < CACHE_TTL:
                _memory_cache["rates"] = data.get("rates", FALLBACK_RATES_TO_LKR)
                _memory_cache["timestamp"] = ts
                _memory_cache["is_live"] = True
                return _memory_cache["rates"]
    except Exception:
        pass
    return None


def _save_cached_rates(rates: dict):
    """Save freshly fetched rates to local disk cache."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.time(),
                "rates": rates,
            }, f, indent=2)
    except Exception:
        pass


def fetch_live_rates() -> dict:
    """
    Fetch exchange rates from free open API with timeout and caching.
    Derives each currency's rate against LKR.
    """
    global _memory_cache

    if time.time() - _memory_cache["timestamp"] < CACHE_TTL and _memory_cache["is_live"]:
        return _memory_cache["rates"]

    cached = _load_cached_rates()
    if cached:
        return cached

    rates_to_lkr = dict(FALLBACK_RATES_TO_LKR)
    try:
        req = urllib.request.Request(
            "https://open.er-api.com/v6/latest/USD",
            headers={"User-Agent": "bill-scanner-app/1.0"}
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_rates = data.get("rates", {})
            usd_to_lkr = float(raw_rates.get("LKR", FALLBACK_RATES_TO_LKR["USD"]))

            rates_to_lkr["USD"] = round(usd_to_lkr, 2)
            rates_to_lkr["LKR"] = 1.0

            for curr, rate_vs_usd in raw_rates.items():
                try:
                    r = float(rate_vs_usd)
                    if r > 0:
                        # 1 USD = usd_to_lkr LKR; 1 USD = r CURR -> 1 CURR = (usd_to_lkr / r) LKR
                        rates_to_lkr[curr.upper()] = round(usd_to_lkr / r, 4)
                except Exception:
                    continue

            _memory_cache["rates"] = rates_to_lkr
            _memory_cache["timestamp"] = time.time()
            _memory_cache["is_live"] = True
            _save_cached_rates(rates_to_lkr)
            return rates_to_lkr
    except Exception:
        _memory_cache["rates"] = rates_to_lkr
        _memory_cache["timestamp"] = time.time()
        _memory_cache["is_live"] = False
        return rates_to_lkr


def get_rate_to_lkr(currency: str) -> float:
    """Return the exchange rate of the given currency to 1 LKR."""
    curr = normalize_currency(currency)
    if curr == "LKR":
        return 1.0
    rates = fetch_live_rates()
    if curr in rates:
        return float(rates[curr])
    if curr in FALLBACK_RATES_TO_LKR:
        return float(FALLBACK_RATES_TO_LKR[curr])
    return 1.0


def get_rate(from_currency: str, to_currency: str) -> float:
    """
    Return the conversion rate from from_currency to to_currency.
    Formula: (from_currency -> LKR) / (to_currency -> LKR).
    """
    f = normalize_currency(from_currency)
    t = normalize_currency(to_currency)
    if f == t:
        return 1.0
    r_from = get_rate_to_lkr(f)
    r_to = get_rate_to_lkr(t)
    if r_to <= 0:
        return 1.0
    return r_from / r_to


def convert_currency(amount: float, from_currency: str, to_currency: str) -> float:
    """Convert an amount from from_currency into to_currency, rounded to 2 decimal places."""
    try:
        amt = float(amount or 0.0)
    except (ValueError, TypeError):
        amt = 0.0
    rate = get_rate(from_currency, to_currency)
    return round(amt * rate, 2)


def convert_to_lkr(amount: float, currency: str) -> float:
    """Legacy helper for backward compatibility."""
    return convert_currency(amount, currency, "LKR")


def get_common_exchange_rates(base_currency: str = "LKR") -> list:
    """Return a list of popular currency rates relative to base_currency for UI display."""
    base = normalize_currency(base_currency)
    common_codes = ["USD", "EUR", "GBP", "INR", "LKR", "AUD", "SGD", "AED"]
    result = []
    for code in common_codes:
        if code == base:
            continue
        rate = get_rate(code, base)
        result.append({
            "currency": code,
            "rate": f"{rate:,.2f}" if rate >= 1 else f"{rate:.4f}",
        })
    return result
