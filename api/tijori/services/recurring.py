"""Recurring series (subscriptions, bills, SIPs) and rule-based alerts, detected from txn history at
read time (no AI). The member's decisions (confirm, dismiss, cancel, kind) live in `recurring` rows.

Per call: one grouped scan of the member's debits over ~2 years, plus two small lookups (decisions,
per-account coverage). History is a few thousand rows, so this stays cheap; callers that need it more
than once per request should pass the result along rather than call again.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session

from tijori.classify.brands import SUBSCRIPTIONS
from tijori.models import Account, Category, Observation, Recurring, Txn
from tijori.money import fmt
from tijori.services.common import MemberContext, account_label, audit, cycle_bounds, today_ist
from tijori.services.errors import NotFound

LOOKBACK_DAYS = 800  # two years, so a yearly charge is seen twice
MIN_CHARGES = {"weekly": 3, "monthly": 3, "quarterly": 3, "yearly": 2}
# Allowed gap between consecutive charges, per cadence; one gap of twice that (a skipped charge) is allowed.
CADENCES = {"weekly": (5, 9), "monthly": (25, 36), "quarterly": (84, 98), "yearly": (350, 380)}
# Days past the due date before a charge that hasn't shown up counts as late.
GRACE = {"weekly": 2, "monthly": 4, "quarterly": 10, "yearly": 20}
PER_MONTH = {"weekly": Decimal(52) / 12, "monthly": Decimal(1), "quarterly": Decimal(1) / 3, "yearly": Decimal(1) / 12}
STEP = Decimal("0.10")  # consecutive charges further apart than this count as a price change
VARIABLE_CATEGORIES = frozenset({"Bills & subscriptions", "Insurance"})
VARIABLE_SPREAD = Decimal(3)  # a variable bill's largest charge is at most 3× its smallest
DUE_SOON_DAYS = 7
PRICE_UP = Decimal("0.05")
ACTIVE = frozenset({"upcoming", "pending", "late"})
KINDS = ("subscription", "bill", "invest", "other")


@dataclass(frozen=True, slots=True)
class Charge:
    day: date
    amount: Decimal
    txn_id: int


@dataclass(frozen=True, slots=True)
class Series:
    key: str
    merchant: str
    cadence: str
    kind: str
    variable: bool
    amount_expected: Decimal
    last_amount: Decimal
    previous_amount: Decimal
    first_at: date
    last_at: date
    next_due: date
    seen_through: date
    state: str  # upcoming | pending (due, statement not in yet) | late | stopped | ended (cancelled by you)
    count: int
    account_id: int | None
    category: str | None
    bucket: str | None
    confirmed: bool
    manual: bool  # confirmed by you; detection alone wouldn't list it
    charges: tuple[Charge, ...]

    @property
    def active(self) -> bool:
        return self.state in ACTIVE


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
        skipped = [g for g in gaps if not lo <= g <= hi]
        if len(skipped) <= (1 if len(gaps) >= 3 else 0) and all(2 * lo <= g <= 2 * hi for g in skipped):
            return name
    return None


def _next_due(last: date, cadence: str) -> date:
    if cadence == "weekly":
        return last + timedelta(days=7)
    return _add_months(last, {"monthly": 1, "quarterly": 3, "yearly": 12}[cadence])


def _steps(amounts: list[Decimal]) -> int:
    return sum(1 for a, b in zip(amounts, amounts[1:]) if a > 0 and abs(b - a) > a * STEP)


def _amount_rule(amounts: list[Decimal], category: str | None) -> tuple[bool, bool]:
    """(accepted, variable). Fixed: at most one price change per six charges (a plan change, a price
    rise). Variable: a bill or premium whose charges stay within 3× of each other (electricity, postpaid)."""
    if min(amounts) <= 0:
        return False, False
    if _steps(amounts) <= max(1, len(amounts) // 6):
        return True, False
    if category in VARIABLE_CATEGORIES and max(amounts) <= min(amounts) * VARIABLE_SPREAD:
        return True, True
    return False, False


CLUSTER = Decimal("0.02")  # charges within 2% of each other are one of a payee's parallel series


def _clusters(txns: list[Any]) -> list[list[Any]]:
    """A payee's charges grouped by amount (±2%), each in date order; only groups of 2+."""
    groups: list[list[Any]] = []
    for c in sorted(txns, key=lambda c: c.amount):
        g = next((g for g in groups if abs(c.amount - g[0].amount) <= g[0].amount * CLUSTER), None)
        if g is None:
            groups.append([c])
        else:
            g.append(c)
    return [sorted(g, key=lambda c: (c.occurred_at, c.id)) for g in groups if len(g) >= 2 and len(g) < len(txns)]


def kind_of(key: str, category: str | None, bucket: str | None) -> str:
    if bucket == "invest":
        return "invest"
    if key.startswith("brand:") and key[6:] in SUBSCRIPTIONS:
        return "subscription"
    if category in VARIABLE_CATEGORIES:
        return "bill"
    return "other"


def _state(cadence: str, due: date, seen: date, today: date, ended: bool) -> str:
    if ended:
        return "ended"
    if due > today:
        return "upcoming"
    if seen < due + timedelta(days=GRACE[cadence]):
        return "pending"
    if (seen - due).days <= CADENCES[cadence][1] // 2:
        return "late"
    return "stopped"


def _seen_through(s: Session, member_id: int) -> tuple[dict[int, date], date | None]:
    """Newest txn date per account: how far that account's statements and alerts reach."""
    rows = s.execute(select(Txn.account_id, func.max(Txn.occurred_at)).where(Txn.member_id == member_id)
                     .group_by(Txn.account_id)).all()
    per = {a: d for a, d in rows if a is not None}
    return per, max((d for _, d in rows), default=None)


def detect(s: Session, member_id: int, today: date | None = None, *, include_dismissed: bool = False) -> list[Series]:
    """Every series, active or not: ≥3 debits to one payee on a steady cadence (2 for yearly), with steady
    or bill-like amounts, plus payees you confirmed by hand. Dismissed payees are left out unless asked."""
    today = today or today_ist()
    key = payee_key_expr()
    decisions = {r.payee_key: r for r in s.scalars(select(Recurring).where(Recurring.member_id == member_id,
                                                                            Recurring.payee_key.is_not(None)))}
    rows = s.execute(
        select(key.label("k"), Txn.id, Txn.merchant_norm, Txn.occurred_at, Txn.amount, Txn.account_id,
               Category.name, Txn.bucket)
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
    seen_per, seen_any = _seen_through(s, member_id)
    out: list[Series] = []

    def evaluate(k: str, txns: list[Any], label: str | None = None) -> Series | None:
        d = decisions.get(k)
        if d is not None and d.decision == "dismissed" and not include_dismissed:
            return None
        confirmed = d is not None and d.decision == "confirmed"
        # Several charges on one day (a duplicate, a split bill) count once for the cadence.
        by_day: dict[date, Any] = {}
        for c in txns:
            by_day.setdefault(c.occurred_at, c)
        days = sorted(by_day)
        last = by_day[days[-1]]
        amounts = [by_day[x].amount for x in days]
        cadence = _cadence(days) if len(days) >= 2 else None
        ok, variable = _amount_rule(amounts, last.name) if cadence else (False, False)
        detected = bool(cadence and ok and len(days) >= MIN_CHARGES[cadence])
        if not detected and not confirmed:
            return None
        if not detected:
            cadence, variable = d.cadence, False  # type: ignore[union-attr]
        assert cadence is not None
        expected = Decimal(median(amounts[-3:])) if variable else last.amount
        if not detected and d is not None and d.amount_expected:
            expected = d.amount_expected
        due = _next_due(days[-1], cadence)
        seen = seen_per.get(last.account_id, seen_any) if last.account_id else seen_any
        state = _state(cadence, due, seen or today, today, d is not None and d.status == "ended")
        if d is not None and d.decision == "dismissed":
            state = "dismissed"
        return Series(
            key=k, merchant=label or last.merchant_norm or k, cadence=cadence,
            kind=(d.kind if d is not None and d.kind else kind_of(k.split("@")[0], last.name, last.bucket)),
            variable=variable, amount_expected=expected, last_amount=last.amount,
            previous_amount=by_day[days[-2]].amount if len(days) > 1 else last.amount,
            first_at=days[0], last_at=days[-1], next_due=due, seen_through=seen or today, state=state,
            count=len(days), account_id=last.account_id, category=last.name, bucket=last.bucket,
            confirmed=confirmed, manual=confirmed and not detected,
            charges=tuple(Charge(x, by_day[x].amount, by_day[x].id) for x in days[-12:]))

    for k, txns in groups.items():
        whole = evaluate(k, txns)
        if whole is not None:
            out.append(whole)
            continue
        # Several fixed charges to one payee (four SIPs to one fund house) are separate series.
        for cl in _clusters(txns):
            amount = Decimal(median(c.amount for c in cl)).quantize(Decimal("1"))
            name = cl[-1].merchant_norm or k
            x = evaluate(f"{k}@{amount}", cl, f"{name} · ₹{amount:,}")
            if x is not None:
                out.append(x)
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


def _change(x: Series) -> dict[str, Any] | None:
    """The latest price change (>5% between consecutive charges); variable bills have none."""
    if x.variable:
        return None
    for prev, cur in zip(reversed(x.charges[:-1]), reversed(x.charges)):
        if prev.amount > 0 and abs(cur.amount - prev.amount) > prev.amount * PRICE_UP:
            return {"from": fmt(prev.amount), "to": fmt(cur.amount), "at": cur.day}
    return None


def monthly_cost(x: Series) -> Decimal:
    return (x.amount_expected * PER_MONTH[x.cadence]).quantize(Decimal("0.01"))


def series_out(x: Series, labels: dict[int, str]) -> dict[str, Any]:
    amounts = [c.amount for c in x.charges]
    return {"id": x.key, "merchant": x.merchant, "kind": x.kind, "cadence": x.cadence, "state": x.state,
            "variable": x.variable, "amount_expected": fmt(x.amount_expected),
            "amount_min": fmt(min(amounts)), "amount_max": fmt(max(amounts)),
            "monthly_cost": fmt(monthly_cost(x)), "yearly_cost": fmt(monthly_cost(x) * 12),
            "next_due": x.next_due, "first_at": x.first_at, "last_at": x.last_at, "seen_through": x.seen_through,
            "count": x.count, "category": x.category,
            "account": labels.get(x.account_id) if x.account_id else None,
            "confirmed": x.confirmed, "manual": x.manual, "change": _change(x),
            "charges": [{"date": c.day, "amount": fmt(c.amount), "txn_id": c.txn_id} for c in x.charges]}


def account_labels(s: Session, member_id: int) -> dict[int, str]:
    rows = s.execute(select(Account.id, Account.institution, Account.name, Account.mask)
                     .where(Account.member_id == member_id)).all()
    return {r.id: account_label(r.institution, r.name, r.mask) or r.institution for r in rows}


def list_recurring(s: Session, member_id: int, today: date | None = None) -> dict[str, Any]:
    today = today or today_ist()
    labels = account_labels(s, member_id)
    series = detect(s, member_id, today, include_dismissed=True)
    live = [x for x in series if x.active]
    spend = sum((monthly_cost(x) for x in live if x.kind != "invest"), Decimal(0))
    soon = [x for x in live if x.state == "upcoming" and x.next_due <= today + timedelta(days=30)]
    return {
        "items": [series_out(x, labels) for x in series if x.state != "dismissed"],
        "dismissed": [{"id": x.key, "merchant": x.merchant} for x in series if x.state == "dismissed"],
        "candidates": candidates(s, member_id, {x.key.split("@")[0] for x in series}, labels, today),
        "totals": {"monthly": fmt(spend), "yearly": fmt(spend * 12),
                   "invest_monthly": fmt(sum((monthly_cost(x) for x in live if x.kind == "invest"), Decimal(0))),
                   "active": len(live),
                   "next_30_days": fmt(sum((x.amount_expected for x in soon), Decimal(0))),
                   "next_30_days_count": len(soon)},
    }


CANDIDATE_DAYS = 400


def candidates(s: Session, member_id: int, listed: set[str], labels: dict[int, str], today: date) -> list[dict[str, Any]]:
    """Subscription-like payees charged only once or twice in the last 400 days (too few for a cadence):
    a known subscription brand, or anything filed under Bills & subscriptions. One grouped query."""
    key = payee_key_expr()
    decided = set(s.scalars(select(Recurring.payee_key).where(Recurring.member_id == member_id,
                                                              Recurring.decision == "dismissed")))
    rows = s.execute(
        select(key.label("k"), func.max(Txn.merchant_norm).label("merchant"), func.count().label("n"),
               func.max(Txn.occurred_at).label("last_at"), func.max(Category.name).label("category"),
               func.array_agg(aggregate_order_by(Txn.amount, Txn.occurred_at.desc())).label("amounts"),
               func.array_agg(aggregate_order_by(Txn.account_id, Txn.occurred_at.desc())).label("accounts"))
        .outerjoin(Category, Category.id == Txn.category_id)
        .where(Txn.member_id == member_id, Txn.direction == "debit", key.is_not(None),
               Txn.occurred_at >= today - timedelta(days=CANDIDATE_DAYS),
               Txn.bucket.is_distinct_from("excluded"), Txn.bucket.is_distinct_from("card"))
        .group_by(key).having(func.count() <= 2)
    ).all()
    out = []
    for r in rows:
        if r.k in listed or r.k in decided:
            continue
        brand = r.k.startswith("brand:") and r.k[6:] in SUBSCRIPTIONS
        if not (brand or r.category == "Bills & subscriptions"):
            continue
        out.append({"id": r.k, "merchant": r.merchant or r.k, "count": r.n, "last_at": r.last_at,
                    "amount": fmt(r.amounts[0]), "category": r.category,
                    "account": labels.get(r.accounts[0]) if r.accounts[0] else None,
                    "kind": "subscription" if brand else "bill"})
    out.sort(key=lambda x: x["last_at"], reverse=True)
    return out


def decide(s: Session, ctx: MemberContext, actor: str, key: str, decision: str, cadence: str | None,
           amount: Decimal | None, kind: str | None, ended: bool | None) -> dict[str, Any]:
    """Record your call on a payee's series. decision "auto" forgets it, so detection decides again."""
    row = s.scalars(select(Recurring).where(Recurring.member_id == ctx.member_id, Recurring.payee_key == key)).first()
    if decision == "auto":
        if row is not None:
            s.execute(delete(Recurring).where(Recurring.id == row.id))
        audit(s, ctx, actor, "recurring.reset", f"payee:{key}", {})
        return {"id": key, "decision": None}
    base, _, amount_part = key.partition("@")  # "payee@amount" names one of a payee's parallel series
    if amount_part and not re.fullmatch(r"\d{1,12}", amount_part):
        raise NotFound("no payments to this payee")
    q = select(Txn.merchant_norm, Txn.amount).where(Txn.member_id == ctx.member_id, Txn.direction == "debit",
                                                     payee_key_expr() == base)
    if amount_part:
        q = q.where(Txn.amount.between(Decimal(amount_part) * (1 - CLUSTER), Decimal(amount_part) * (1 + CLUSTER)))
    last = s.execute(q.order_by(Txn.occurred_at.desc(), Txn.id.desc()).limit(1)).first()
    if last is None:
        raise NotFound("no payments to this payee")
    if row is None:
        row = Recurring(member_id=ctx.member_id, payee_key=key)
        s.add(row)
    row.merchant_norm = (last.merchant_norm or key)[:120]
    row.cadence = cadence or row.cadence or "monthly"
    row.amount_expected = amount if amount is not None else (row.amount_expected or last.amount)
    row.decision = decision
    if kind is not None:
        row.kind = kind
    if ended is not None:
        row.status = "ended" if ended else "active"
    row.updated_at = datetime.now(UTC)
    s.flush()
    audit(s, ctx, actor, f"recurring.{decision}", f"payee:{key}",
          {"cadence": row.cadence, "kind": row.kind, "status": row.status})
    return {"id": key, "decision": decision}


def _inr(v: Decimal) -> str:
    return f"₹{v:,.0f}" if v == v.to_integral() else f"₹{v:,.2f}"


def alerts(s: Session, member_id: int, month: str, month_start_day: int = 1) -> dict[str, Any]:
    """Rule flags only. duplicate: same account, payee, amount and day, twice or more, in the month.
    bounce_risk: a series due within 7 days whose account's last known balance is below the charge.
    price_increase: a steady-priced series whose latest charge is >5% above the one before.
    missed: a series whose charge is past due + grace in statements that already reach past it."""
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
    series = [x for x in detect(s, member_id, today) if x.active]
    labels = account_labels(s, member_id)
    bal = balances(s, member_id)
    for x in series:
        if x.state == "late" and end > today:
            items.append({"id": f"late:{x.key}", "kind": "missed", "severity": "warn",
                          "title": f"{x.merchant} not seen",
                          "detail": f"Expected {x.next_due:%d %b} · {_inr(x.amount_expected)} · "
                                    f"statements through {x.seen_through:%d %b}", "txn_ids": []})
        if x.account_id in bal and today <= x.next_due <= today + timedelta(days=DUE_SOON_DAYS):
            amount, as_of = bal[x.account_id]
            if amount < x.amount_expected:
                items.append({"id": f"bounce:{x.key}", "kind": "bounce_risk", "severity": "warn",
                              "title": f"{x.merchant} {_inr(x.amount_expected)} due {x.next_due:%d %b}",
                              "detail": f"{labels.get(x.account_id, 'Account')} balance {_inr(amount)} "
                                        f"as of {as_of:%d %b}", "txn_ids": []})
        if not x.variable and start <= x.last_at < end and x.last_amount > x.previous_amount * (1 + PRICE_UP):
            items.append({"id": f"price:{x.key}", "kind": "price_increase", "severity": "warn",
                          "title": f"{x.merchant} up {_inr(x.last_amount - x.previous_amount)}",
                          "detail": f"{_inr(x.previous_amount)} → {_inr(x.last_amount)} on {x.last_at:%d %b}",
                          "txn_ids": []})
    return {"month": month, "items": items}


def committed_keys(series: list[Series]) -> set[str]:
    """Payee keys whose spend counts as committed (a live recurring series)."""
    return {x.key.split("@")[0] for x in series if x.active}


def payee_key_expr() -> Any:
    """The series identity in SQL, as detect() groups by it."""
    return func.coalesce(Txn.payee_key, func.lower(Txn.merchant_norm))
