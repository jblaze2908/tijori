"""Structural kind rules. Order matters: first hit wins.

Fees precede investments (a bounced Groww mandate is a fee), reversals precede self-transfer
(a reversal names your own handle), and redemptions precede interest ("PRIN AND INT").
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from tijori.classify.brands import Brand
from tijori.classify.narration import Narration
from tijori.classify.taxonomy import Kind, kind_of

DEFAULT_LOCAL_SHOP_CAP = Decimal("500")


@dataclass(frozen=True, slots=True)
class MemberProfile:
    """Per-member facts the structural rules need. Stored in `member.classify_config`."""

    own_names: tuple[str, ...] = ()
    own_vpas: tuple[str, ...] = ()
    own_account_masks: tuple[str, ...] = ()
    investment_account_masks: tuple[str, ...] = ()
    employer_patterns: tuple[str, ...] = ()
    local_shop_cap: Decimal = DEFAULT_LOCAL_SHOP_CAP

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> "MemberProfile":
        data = data or {}
        return cls(
            own_names=tuple(data.get("own_names", ())),
            own_vpas=tuple(v.lower() for v in data.get("own_vpas", ())),
            own_account_masks=tuple(str(m)[-4:] for m in data.get("own_account_masks", ())),
            investment_account_masks=tuple(str(m)[-4:] for m in data.get("investment_account_masks", ())),
            employer_patterns=tuple(data.get("employer_patterns", ())),
            local_shop_cap=Decimal(str(data.get("local_shop_cap", DEFAULT_LOCAL_SHOP_CAP))),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "own_names": list(self.own_names),
            "own_vpas": list(self.own_vpas),
            "own_account_masks": list(self.own_account_masks),
            "investment_account_masks": list(self.investment_account_masks),
            "employer_patterns": list(self.employer_patterns),
            "local_shop_cap": str(self.local_shop_cap),
        }


@dataclass(frozen=True, slots=True)
class KindHit:
    rule_id: str
    kind: Kind
    category: str
    merchant: str | None = None


@dataclass(frozen=True, slots=True)
class KindInput:
    direction: str
    narration: Narration
    brand: Brand | None  # matched without the remark
    text: str  # upper-cased raw narration
    profile: MemberProfile = field(default_factory=MemberProfile)


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


_FEES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_rx(r"FAIL INSUF", r"INSUFFICIENT", r"ACH RET", r"ECS RET", r"RETURN CH", r"BOUNCE"), "Mandate bounce fee"),
    (_rx(r"IMPS P2P", r"IMPS CHG", r"NEFT CHG"), "Transfer fee"),
    (_rx(r"SMART_RENEWAL", r"SMS ALERT", r"SMS CHG", r"SMSCHG"), "SMS alert fee"),
    (_rx(r"ANNUAL FEE", r"\bAMC\b", r"DEBIT CARD FEE", r"CARD ISSUANCE"), "Card fee"),
    (_rx(r"MIN(?:IMUM)? BAL", r"NON[- ]MAINT", r"\bCHRGS?\b", r"\bCHARGES?\b", r"GST ON"), "Bank charges"),
)
_REVERSAL = _rx(r"^REV[-/ ]", r"\bREV-UPI", r"REVERSAL", r"\bRVSL\b")
_REFUND = _rx(r"REFUND", r"UPIRET", r"\bRFND\b", r"CASHBACK", r"\bRETURN\b")
_REDEMPTION = _rx(r"AUTO_REDEEM", r"AUTO_REDEMPTION", r"\bFD CLOSURE\b", r"\bTD CLOSURE\b", r"\bMATURITY\b")
_SALARY = _rx(r"\bSALARY\b", r"\bSAL (?:JAN|FEB|MAR|APR|MAY|JUNE?|JULY?|AUG|SEPT?|OCT|NOV|DEC)\b")
_INTEREST = _rx(r"\bINTEREST\b", r"\bINT\.? ?(?:PD|PAID|CR)\b", r"\bSB INT\b")
_DIVIDEND = _rx(r"\bDIV\b", r"DIVIDEND", r"FNLDIV", r"INTDIV", r"FINDIV", r"CEMTEX DEP", r"\bACH ?C[R-]", r"ACHCR")
_CASH = _rx(r"SELF\s*-?\s*CHQ", r"CASH\s*WDL", r"ATM\s*WDL", r"\bATW\b", r"\bNFS\b", r"CASH WITHDRAWAL", r"\bATM\b")
_CARD_BILL = _rx(r"CC ?PAYMENT", r"CREDIT ?CARD ?(?:BILL|PAYMENT)", r"CARD ?BILL")


def _squash_name(s: str) -> str:
    return re.sub(r"[^A-Z]", "", s.upper())


def _is_own_name(payee: str | None, profile: MemberProfile) -> bool:
    """Banks truncate names (SBI keeps ~8 chars), so a long-enough prefix of an own name counts."""
    if not payee:
        return False
    p = _squash_name(payee)
    if len(p) < 6:
        return False
    return any((o := _squash_name(n)).startswith(p) or p.startswith(o) for n in profile.own_names)


def _is_own_vpa(vpa: str | None, profile: MemberProfile) -> bool:
    if not vpa or len(vpa) < 6:
        return False
    return any(o.startswith(vpa) or vpa.startswith(o) or o[:10] == vpa[:10] for o in profile.own_vpas)


def _mask_in(n: Narration, masks: tuple[str, ...]) -> bool:
    m = n.masked_account
    return bool(m and len(m) >= 4 and m[-4:] in masks)


def _fee(k: KindInput) -> KindHit | None:
    if k.direction != "debit":
        return None
    for rx, label in _FEES:
        if rx.search(k.text):
            return KindHit("kind:fee", "fee", "Bank charges", label)
    return None


def _reversal(k: KindInput) -> KindHit | None:
    if k.direction == "credit" and _REVERSAL.search(k.text):
        return KindHit("kind:reversal", "refund", "Reversals", "Reversal")
    return None


def _refund(k: KindInput) -> KindHit | None:
    if k.direction != "credit":
        return None
    if _REFUND.search(k.text):
        return KindHit("kind:refund", "refund", "Refunds", k.brand.name if k.brand else None)
    # A credit from a brand you buy from is a refund; investment brands are redemptions.
    if k.brand and kind_of(k.brand.category) in ("spend", "fee"):
        return KindHit("kind:refund_from_brand", "refund", "Refunds", k.brand.name)
    return None


def _self_transfer(k: KindInput) -> KindHit | None:
    n, p = k.narration, k.profile
    if _is_own_vpa(n.vpa, p) or _mask_in(n, p.own_account_masks) or _is_own_name(n.payee, p):
        return KindHit("kind:self_transfer", "transfer", "Self transfer", "Self transfer")
    return None


def _card_bill(k: KindInput) -> KindHit | None:
    if k.direction != "debit":
        return None
    if (k.brand and k.brand.category == "Card bill payment") or _CARD_BILL.search(k.text):
        return KindHit("kind:card_bill", "transfer", "Card bill payment", k.brand.name if k.brand else None)
    return None


def _investment(k: KindInput) -> KindHit | None:
    n, p = k.narration, k.profile
    to_investment = (k.brand and k.brand.category == "Investments") or _mask_in(n, p.investment_account_masks)
    if k.direction == "debit" and to_investment:
        name = k.brand.name if k.brand else "Deposit account"
        return KindHit("kind:investment", "investment", "Investments", name)
    if k.direction == "credit" and (to_investment or _REDEMPTION.search(k.text)):
        return KindHit("kind:redemption", "investment", "Investment redemptions",
                       k.brand.name if k.brand else "Deposit maturity")
    return None


def _salary(k: KindInput) -> KindHit | None:
    if k.direction != "credit":
        return None
    # Employer names are member-supplied: plain substrings, never regexes.
    if _SALARY.search(k.text) or any(p.upper() in k.text for p in k.profile.employer_patterns):
        return KindHit("kind:salary", "income", "Salary", "Salary")
    return None


def _interest(k: KindInput) -> KindHit | None:
    if k.direction == "credit" and _INTEREST.search(k.text):
        return KindHit("kind:interest", "income", "Interest", "Interest")
    return None


def _dividend(k: KindInput) -> KindHit | None:
    if k.direction == "credit" and _DIVIDEND.search(k.text):
        return KindHit("kind:dividend", "income", "Dividends")
    return None


def _cash(k: KindInput) -> KindHit | None:
    if k.direction == "debit" and (k.narration.channel == "cash" or _CASH.search(k.text)):
        return KindHit("kind:cash", "cash", "Cash", "Cash withdrawal")
    return None


KIND_RULES: tuple[Callable[[KindInput], KindHit | None], ...] = (
    _fee,
    _reversal,
    _refund,
    _self_transfer,
    _card_bill,
    _investment,
    _salary,
    _interest,
    _dividend,
    _cash,
)


def structural_kind(k: KindInput) -> KindHit | None:
    return next((hit for rule in KIND_RULES if (hit := rule(k))), None)
