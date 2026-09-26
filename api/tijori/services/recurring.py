"""Recurring series and rule-based alerts, detected from txn history at read time (no AI, no writes).

One grouped scan of the member's debits over the last ~13 months per call; the member's whole history
is a few thousand rows, so this stays a single cheap query. Callers that need it more than once per
request should pass the result along rather than call again.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tijori.models import Account, Category, Observation, Txn
from tijori.money import fmt
from tijori.services.common import account_label, cycle_bounds, today_ist

LOOKBACK_DAYS = 400
MIN_CHARGES = 3
AMOUNT_TOLERANCE = Decimal("0.10")  # every charge within ±10% of the series' median
# Allowed gap between consecutive charges, per cadence.
CADENCES = {"weekly": (5, 9), "monthly": (25, 36), "quarterly": (84, 98), "yearly": (350, 380)}
DUE_SOON_DAYS = 7
PRICE_UP = Decimal("0.05")


@dataclass(frozen=True, slots=True)
class Series:
    key: str
    merchant: str
    cadence: str
    amount_expected: Decimal
    last_amount: Decimal
    previous_amount: Decimal
    last_at: date
    next_due: date
    count: int
    account_id: int | None
    category: str | None
    bucket: str | None


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 28)


def _cadence(dates: list[date]) -> str | None:
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    for name, (lo, hi) in CADENCES.items():
        if all(lo <= g <= hi for g in gaps):
            return name
    return None


def _next_due(last: date, cadence: str) -> date:
    if cadence == "weekly":
        return last + timedelta(days=7)
    return _add_months(last, {"monthly": 1, "quarterly": 3, "yearly": 12}[cadence])


def detect(s: Session, member_id: int, today: date | None = None) -> list[Series]:
    """Active series: ≥3 debits to one payee on a steady cadence, amounts within ±10% of their median,
    and the next charge not overdue by more than half a period."""
    today = today or today_ist()
    key = payee_key_expr()
    rows = s.execute(
        select(key.label("k"), Txn.merchant_norm, Txn.occurred_at, Txn.amount, Txn.account_id, Category.name,
               Txn.bucket)
        .outerjoin(Category, Category.id == Txn.category_id)
        .where(Txn.member_id == member_id, Txn.direction == "debit",
               Txn.occurred_at >= today - timedelta(days=LOOKBACK_DAYS),
               # Card-bill payments repeat monthly but settle purchases; they are not a subscription.
               Txn.bucket.is_distinct_from("excluded"), Txn.bucket.is_distinct_from("card"), key.is_not(None))
        .order_by(key, Txn.occurred_at, Txn.id)
    ).all()
    groups: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        groups[r.k].append(r)
    out: list[Series] = []
    for k, charges in groups.items():
        # Several charges on one day (a duplicate, a split bill) count once for the cadence.
        by_day: dict[date, Any] = {}
        for c in charges:
            by_day.setdefault(c.occurred_at, c)
        days = sorted(by_day)
        if len(days) < MIN_CHARGES:
            continue
        cadence = _cadence(days)
        if cadence is None:
            continue
        amounts = [by_day[d].amount for d in days]
        mid = Decimal(median(amounts))
        if mid <= 0 or any(abs(a - mid) > mid * AMOUNT_TOLERANCE for a in amounts):
            continue
        last = by_day[days[-1]]
        due = _next_due(days[-1], cadence)
        hi = CADENCES[cadence][1]
        if (today - due).days > hi // 2:
            continue  # stopped
        out.append(Series(key=k, merchant=last.merchant_norm or k, cadence=cadence, amount_expected=last.amount,
                          last_amount=last.amount, previous_amount=by_day[days[-2]].amount, last_at=days[-1],
                          next_due=due, count=len(days), account_id=last.account_id, category=last.name,
                          bucket=last.bucket))
    out.sort(key=lambda x: (x.next_due, x.merchant))
    return out


def balances(s: Session, member_id: int) -> dict[int, tuple[Decimal, date]]:
    """Latest known balance per account: the balance printed after its newest statement line."""
    ranked = (
        select(Observation.account_id, Observation.balance_after, Observation.occurred_at,
               func.row_number().over(partition_by=Observation.account_id,
                                      order_by=(Observation.occurred_at.desc(), Observation.id.desc())).label("rn"))
        .where(Observation.member_id == member_id, Observation.balance_after.is_not(None),
               Observation.account_id.is_not(None))
        .subquery()
    )
    rows = s.execute(select(ranked.c.account_id, ranked.c.balance_after, ranked.c.occurred_at)
                     .where(ranked.c.rn == 1)).all()
    return {r.account_id: (r.balance_after, r.occurred_at) for r in rows}


def series_out(x: Series, labels: dict[int, str]) -> dict[str, Any]:
    return {"id": x.key, "merchant": x.merchant, "cadence": x.cadence, "amount_expected": fmt(x.amount_expected),
            "next_due": x.next_due, "last_at": x.last_at, "count": x.count, "category": x.category,
            "account": labels.get(x.account_id) if x.account_id else None}


def account_labels(s: Session, member_id: int) -> dict[int, str]:
    rows = s.execute(select(Account.id, Account.institution, Account.name, Account.mask)
                     .where(Account.member_id == member_id)).all()
    return {r.id: account_label(r.institution, r.name, r.mask) or r.institution for r in rows}


def list_recurring(s: Session, member_id: int) -> dict[str, Any]:
    labels = account_labels(s, member_id)
    return {"items": [series_out(x, labels) for x in detect(s, member_id)]}


def _inr(v: Decimal) -> str:
    return f"₹{v:,.0f}" if v == v.to_integral() else f"₹{v:,.2f}"


def alerts(s: Session, member_id: int, month: str, month_start_day: int = 1) -> dict[str, Any]:
    """Rule flags only. duplicate: same account, payee, amount and day, twice or more, in the month.
    bounce_risk: a series due within 7 days whose account's last known balance is below the charge.
    price_increase: a series whose latest charge is >5% above the one before."""
    start, end = cycle_bounds(month, month_start_day)
    today = today_ist()
    items: list[dict[str, Any]] = []
    pkey = payee_key_expr()
    dups = s.execute(
        select(pkey.label("k"), func.max(Txn.merchant_norm).label("merchant"), Txn.account_id, Txn.amount,
               Txn.occurred_at, func.count().label("n"), func.array_agg(Txn.id).label("ids"))
        .where(Txn.member_id == member_id, Txn.direction == "debit", Txn.occurred_at >= start, Txn.occurred_at < end,
               Txn.bucket.is_distinct_from("excluded"), pkey.is_not(None))
        .group_by(pkey, Txn.account_id, Txn.amount, Txn.occurred_at)
        .having(func.count() > 1)
        .order_by(Txn.occurred_at.desc())
    ).all()
    for d in dups:
        items.append({"id": f"dup:{min(d.ids)}", "kind": "duplicate", "severity": "bad",
                      "title": f"{d.merchant or d.k} charged {d.n}×",
                      "detail": f"{_inr(d.amount)} × {d.n} on {d.occurred_at:%d %b}", "txn_ids": sorted(d.ids)})
    series = detect(s, member_id, today)
    labels = account_labels(s, member_id)
    bal = balances(s, member_id)
    for x in series:
        if x.account_id in bal and today <= x.next_due <= today + timedelta(days=DUE_SOON_DAYS):
            amount, as_of = bal[x.account_id]
            if amount < x.amount_expected:
                items.append({"id": f"bounce:{x.key}", "kind": "bounce_risk", "severity": "warn",
                              "title": f"{x.merchant} {_inr(x.amount_expected)} due {x.next_due:%d %b}",
                              "detail": f"{labels.get(x.account_id, 'Account')} balance {_inr(amount)} "
                                        f"as of {as_of:%d %b}", "txn_ids": []})
        if start <= x.last_at < end and x.last_amount > x.previous_amount * (1 + PRICE_UP):
            items.append({"id": f"price:{x.key}", "kind": "price_increase", "severity": "warn",
                          "title": f"{x.merchant} up {_inr(x.last_amount - x.previous_amount)}",
                          "detail": f"{_inr(x.previous_amount)} → {_inr(x.last_amount)} on {x.last_at:%d %b}",
                          "txn_ids": []})
    return {"month": month, "items": items}


def committed_keys(series: list[Series]) -> set[str]:
    """Payee keys whose spend counts as committed (a live recurring series)."""
    return {x.key for x in series}


def payee_key_expr() -> Any:
    """The series identity in SQL, as detect() groups by it."""
    return func.coalesce(Txn.payee_key, func.lower(Txn.merchant_norm))
