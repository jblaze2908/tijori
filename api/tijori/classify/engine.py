"""Deterministic classifier (PLAN §7.4). No AI: rules, payee memory, brand dictionary, heuristics.

Resolution order, first hit wins:
  1. member rules (VPA > merchant > narration regex) — a correction always sticks
  2. structural kind rules (salary, self-transfer, card bill, investment, fees, cash, ...)
  3. payee memory (>= 2 agreeing confirmations; conflicts go to the Inbox)
  4. household rules, then the brand dictionary
  5. UPI-handle heuristics (merchant QR under the cap → Local shops; people → Inbox)
  6. Inbox
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from tijori.classify.brands import Brand
from tijori.classify.kinds import KindInput, MemberProfile, structural_kind
from tijori.classify.memory import PayeeMemory, memory_key
from tijori.classify.merchants import MERCHANT_QR_HANDLES, match_brand, normalize_merchant, payee_key
from tijori.classify.narration import Narration, parse_narration
from tijori.classify.taxonomy import BY_NAME, CategoryDef, Kind

Direction = Literal["debit", "credit"]
ClassifiedBy = Literal["rule", "payee_memory", "dictionary", "heuristic", "user", "system"]

MAX_RULE_REGEX_LEN = 200


@dataclass(frozen=True, slots=True)
class TxnInput:
    occurred_at: date
    amount: Decimal
    direction: Direction
    narration: str
    account_id: int | None = None
    ref_no: str | None = None


@dataclass(frozen=True, slots=True)
class Identity:
    narration: Narration
    brand_core: Brand | None  # matched on payee + handle; kind rules use this
    brand: Brand | None  # debit UPI lines also consult the remark ("amazon … /Prime")
    merchant: str
    payee_key: str


@dataclass(frozen=True, slots=True)
class Decision:
    kind: Kind
    category: str | None
    classified_by: ClassifiedBy | None  # None: not filed, waiting in the Inbox
    rule_id: str  # what decided it, or why it is in the Inbox
    merchant: str
    payee_key: str
    vpa: str | None = None
    inbox_reason: str | None = None
    options: tuple[tuple[str, int], ...] = ()
    suggestion: str | None = None
    bucket: str | None = None  # set by Classifier from the category and the direction

    @property
    def filed(self) -> bool:
        return self.category is not None


@dataclass(frozen=True, slots=True)
class Rule:
    """A member or household rule. At least one of vpa / merchant / narration_regex is required."""

    id: int | str
    category: str
    scope: Literal["member", "household"] = "member"
    vpa: str | None = None
    merchant: str | None = None
    narration_regex: str | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    account_id: int | None = None
    direction: Direction | None = None
    kind: Kind | None = None
    priority: int = 0
    _rx: re.Pattern[str] | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not (self.vpa or self.merchant or self.narration_regex):
            raise ValueError("rule needs a vpa, merchant or narration_regex matcher")
        if self.narration_regex is not None:
            # Rules are member-authored; bound the pattern size to limit regex blow-ups.
            if len(self.narration_regex) > MAX_RULE_REGEX_LEN:
                raise ValueError("narration_regex too long")
            object.__setattr__(self, "_rx", re.compile(self.narration_regex, re.I))

    @classmethod
    def from_match_json(cls, rule_id: int | str, category: str, match: dict[str, Any], **kw: Any) -> "Rule":
        def dec(v: Any) -> Decimal | None:
            return None if v is None else Decimal(str(v))

        return cls(
            id=rule_id,
            category=category,
            vpa=match.get("vpa"),
            merchant=match.get("merchant"),
            narration_regex=match.get("narration_regex"),
            min_amount=dec(match.get("min_amount")),
            max_amount=dec(match.get("max_amount")),
            account_id=match.get("account_id"),
            direction=match.get("direction"),
            **kw,
        )

    @property
    def specificity(self) -> int:
        return 0 if self.vpa else 1 if self.merchant else 2

    def matches(self, txn: TxnInput, narr: Narration, merchant: str) -> bool:
        if self.direction and txn.direction != self.direction:
            return False
        if self.account_id is not None and txn.account_id != self.account_id:
            return False
        if self.min_amount is not None and txn.amount < self.min_amount:
            return False
        if self.max_amount is not None and txn.amount > self.max_amount:
            return False
        if self.vpa:
            # Exact on the 10-char prefix SBI keeps, so one rule covers both banks' spellings.
            return bool(narr.vpa_key) and self.vpa.lower()[:10] == narr.vpa_key
        if self.merchant:
            return self.merchant.casefold() == merchant.casefold()
        assert self._rx is not None
        return bool(self._rx.search(narr.raw))

    @property
    def rule_id(self) -> str:
        return f"rule:{self.id}"


def _ordered(rules: Iterable[Rule]) -> tuple[Rule, ...]:
    return tuple(sorted(rules, key=lambda r: (r.specificity, -r.priority, str(r.id))))


class Classifier:
    """Build once per batch: rules are sorted and memory loaded up front, not per txn.

    `categories` is the household's own list (custom ones included) over the defaults; a rule on a
    category that isn't in it is dropped.
    """

    def __init__(
        self,
        profile: MemberProfile | None = None,
        member_rules: Iterable[Rule] = (),
        household_rules: Iterable[Rule] = (),
        memory: PayeeMemory | None = None,
        categories: Mapping[str, CategoryDef] | None = None,
    ) -> None:
        self.profile = profile or MemberProfile()
        self.categories: Mapping[str, CategoryDef] = {**BY_NAME, **(categories or {})}
        self.member_rules = _ordered(r for r in member_rules if r.category in self.categories)
        self.household_rules = _ordered(r for r in household_rules if r.category in self.categories)
        self.memory = memory or PayeeMemory()

    def _finish(self, txn: TxnInput, d: Decision) -> Decision:
        if d.category is None:
            return d
        c = self.categories[d.category]
        bucket, kind = c.for_direction(txn.direction)
        return replace(d, bucket=bucket, kind=kind if c.credit_bucket else d.kind)

    @staticmethod
    def identify(txn: TxnInput) -> Identity:
        """Parse and normalize. Two brand scans for UPI debits with a remark (see Identity)."""
        narr = parse_narration(txn.narration)
        brand_core = match_brand(narr, include_remark=False)
        brand = brand_core
        if txn.direction == "debit" and narr.channel == "upi" and narr.remark:
            brand = match_brand(narr, include_remark=True)
        return Identity(narr, brand_core, brand, normalize_merchant(narr, brand), payee_key(narr, brand))

    def classify(self, txn: TxnInput) -> Decision:
        return self._finish(txn, self._decide(txn))

    def _decide(self, txn: TxnInput) -> Decision:
        ident = self.identify(txn)
        narr, brand_core, brand, merchant, key = (
            ident.narration, ident.brand_core, ident.brand, ident.merchant, ident.payee_key
        )
        base = dict(merchant=merchant, payee_key=key, vpa=narr.vpa)
        tentative: Kind = "spend" if txn.direction == "debit" else "income"

        for rule in self.member_rules:
            if rule.matches(txn, narr, merchant):
                return Decision(rule.kind or self.categories[rule.category].kind, rule.category, "rule", rule.rule_id,
                                **base)

        hit = structural_kind(
            KindInput(txn.direction, narr, brand_core, narr.raw.upper(), self.profile)
        )
        if hit:
            return Decision(hit.kind, hit.category, "rule", hit.rule_id,
                            **{**base, "merchant": hit.merchant or merchant})

        suggestion = None
        verdict = self.memory.lookup(memory_key(txn.direction, key))
        if verdict and verdict.status == "auto" and verdict.category in self.categories:
            return Decision(self.categories[verdict.category].kind, verdict.category, "payee_memory", f"memory:{key}",
                            options=verdict.options, **base)
        if verdict and verdict.status == "conflict":
            return Decision(tentative, None, None, "memory:conflict", inbox_reason="conflict",
                            options=verdict.options, **base)
        if verdict:
            suggestion = verdict.category

        for rule in self.household_rules:
            if rule.matches(txn, narr, merchant):
                return Decision(rule.kind or self.categories[rule.category].kind, rule.category, "rule", rule.rule_id,
                                **base)

        if brand and txn.direction == "debit":
            return Decision(self.categories[brand.category].kind, brand.category, "dictionary", f"dict:{brand.key}",
                            **base)

        return self._heuristic(txn, narr, tentative, suggestion, base)

    def _heuristic(
        self, txn: TxnInput, narr: Narration, tentative: Kind, suggestion: str | None, base: dict[str, Any]
    ) -> Decision:
        handle = narr.vpa_handle
        if txn.direction == "debit" and handle and MERCHANT_QR_HANDLES.match(handle):
            if txn.amount <= self.profile.local_shop_cap:
                return Decision("spend", "Local shops", "heuristic", "upi:merchant_qr", **base)
            return Decision(tentative, None, None, "upi:merchant_qr_over_cap", inbox_reason="merchant_over_cap",
                            suggestion=suggestion, **base)
        if handle or (narr.channel in ("imps", "neft") and narr.payee):
            return Decision(tentative, None, None, "upi:person", inbox_reason="person",
                            suggestion=suggestion, **base)
        return Decision(tentative, None, None, "inbox:unknown", inbox_reason="new_payee",
                        suggestion=suggestion, **base)

    def classify_batch(self, txns: Sequence[TxnInput]) -> list[Decision]:
        """Classify, then pair reversals: a debit sharing its ref no and amount with a reversal
        credit on the same account is the failed half of that pair."""
        decisions = [self.classify(t) for t in txns]
        reversed_refs = {
            (t.account_id, t.ref_no, t.amount)
            for t, d in zip(txns, decisions)
            if d.category == "Reversals" and t.direction == "credit" and t.ref_no
        }
        for i, (t, d) in enumerate(zip(txns, decisions)):
            if (
                t.direction == "debit"
                and t.ref_no
                and (t.account_id, t.ref_no, t.amount) in reversed_refs
                and not d.rule_id.startswith("rule:")
            ):
                decisions[i] = self._finish(t, replace(d, kind="refund", category="Reversals", classified_by="rule",
                                                       rule_id="link:reversal", inbox_reason=None, suggestion=None))
        return decisions
