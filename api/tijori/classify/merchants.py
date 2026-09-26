"""Merchant normalization (PLAN §7.4 step 2) and payee identity.

Brand patterns compile once at import; per-txn cost is one scan over ~130 regexes.
"""

import re

from tijori.classify.brands import BRANDS, Brand
from tijori.classify.narration import Narration

_COMPILED: tuple[tuple[Brand, re.Pattern[str]], ...] = tuple(
    (b, re.compile("|".join(f"(?:{p})" for p in b.patterns))) for b in BRANDS
)
BY_KEY: dict[str, Brand] = {b.key: b for b in BRANDS}

# Aggregator and channel prefixes that wrap the real merchant name on card and UPI lines.
_AGGREGATOR = re.compile(
    r"^(?:(?:POS\s*[\dX]*\s+|ECOM\s+|UPI-|RAZ\*|RAZORPAY\s*\*|RZP\*|PAYU\*|PYU\*|PU\*|PAYTM\*|CCA\*|BD\*|"
    r"IND\*|SP\*|SQ\s*\*|PP\*|PAYPAL\s*\*)\s*)+",
    re.I,
)
_CHANNEL_SUFFIX = re.compile(r"\s*/(?:HDFC|ICIC|SBIN|UTIB|YESB|KKBK|AXIS)\b.*$", re.I)


def strip_aggregators(text: str) -> str:
    return _CHANNEL_SUFFIX.sub("", _AGGREGATOR.sub("", text.strip())).strip()


def brand_haystack(n: Narration, *, include_remark: bool = True) -> str:
    """Text the brand patterns see. UPI lines use payee and handle only: VPA domains and IFSC
    codes (e.g. `@mairtel`, `AIRP0000001`) would otherwise trip brand patterns."""
    if n.channel == "upi":
        parts = [n.payee, n.vpa_handle, n.remark if include_remark else None]
    elif n.channel in ("neft", "imps"):
        parts = [n.payee]
    else:
        parts = [strip_aggregators(n.raw)]
    return " ".join(p for p in parts if p).upper()


def match_brand(n: Narration, *, include_remark: bool = True) -> Brand | None:
    hay = brand_haystack(n, include_remark=include_remark)
    if not hay:
        return None
    return next((b for b, rx in _COMPILED if rx.search(hay)), None)


def normalize_merchant(n: Narration, brand: Brand | None) -> str:
    """Canonical brand name, else the payee as printed, cleaned and title-cased."""
    if brand:
        return brand.name
    name = n.payee or strip_aggregators(n.raw)
    name = re.sub(r"\b\d{6,}\b", "", name)
    name = re.sub(r"\s+", " ", name).strip(" -/*.")
    return (name.title() if name.isupper() or name.islower() else name)[:60] or "Unknown"


# Merchant-QR handle shapes: Paytm QR/soundbox, PhonePe QR, BharatPe, Vyapar, GPay for Business.
MERCHANT_QR_HANDLES = re.compile(r"^(?:paytmqr|q\d{6,}|bharatpe|vyapar\.|paytm\.s|gpay-\d{6,})", re.I)


def name_key(payee: str, width: int) -> str:
    return re.sub(r"[^a-z0-9]", "", payee.lower())[:width]


def payee_key(n: Narration, brand: Brand | None) -> str:
    """Stable identity for payee memory: brand, else UPI handle, else masked account, else name."""
    if brand:
        return f"brand:{brand.key}"
    if n.vpa_key:
        # 10 chars of a QR handle ("bharatpe.9…", "vyapar.17…") is shared by many shops.
        if n.payee and MERCHANT_QR_HANDLES.match(n.vpa_key):
            return f"vpa:{n.vpa_key}|{name_key(n.payee, 6)}"
        return f"vpa:{n.vpa_key}"
    if n.masked_account:
        return f"acct:{n.masked_account[-4:]}"
    if n.payee:
        # 8 chars: SBI truncates payee names, so a longer key would split one person in two.
        return "name:" + name_key(n.payee, 8)
    return "text:" + re.sub(r"\d+", "#", re.sub(r"\s+", " ", n.raw.lower()))[:40]
