"""Aggregates: monthly summary, months with data, budgets, trends. Grouping runs in SQL; Python
only folds the already-aggregated rows."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, Integer, Text, and_, bindparam, case, cast, false, func, literal_column, or_, select, true
from sqlalchemy.orm import Session

from tijori.classify.taxonomy import EXPENSE_BUCKETS
from tijori.models import Budget, Category, Txn
from tijori.money import ZERO, fmt
from tijori.services import loans, recurring
from tijori.services.common import cycle_bounds, previous_month, today_ist

SUMMARY_BUCKETS = ("everyday", "card", "oneoff", "invest", "income")


def _totals(rows: list[Any]) -> dict[str, Any]:
    t: dict[str, Decimal] = defaultdict(lambda: ZERO)
    count = 0
    for r in rows:
        count += r.n
        if r.direction == "debit":
            if r.category_id is None:
                t["uncategorized"] += r.amount
            elif r.bucket in (*EXPENSE_BUCKETS, "invest"):
                t[r.bucket] += r.amount
        else:
            if r.bucket == "income":
                t["income"] += r.amount
                if r.kind == "refund":
                    t["refunds"] += r.amount
            if r.name == "Salary":
                t["salary"] += r.amount
    expense = t["everyday"] + t["oneoff"] + t["card"] + t["uncategorized"]
    return {
        "expense": fmt(expense), "everyday": fmt(t["everyday"]), "card": fmt(t["card"]),
        "oneoff": fmt(t["oneoff"]), "uncategorized": fmt(t["uncategorized"]), "invest": fmt(t["invest"]),
        "income": fmt(t["income"]), "refunds": fmt(t["refunds"]), "salary": fmt(t["salary"]),
        "salary_minus_expense": fmt(t["salary"] - expense), "txn_count": count,
    }


def _category_lines(rows: list[Any], direction: str, buckets: tuple[str, ...],
                    with_uncategorized: bool) -> list[dict[str, Any]]:
    acc: dict[int | None, dict[str, Any]] = {}
    for r in rows:
        if r.direction != direction:
            continue
        if r.category_id is None and not with_uncategorized:
            continue
        if r.category_id is not None and r.bucket not in buckets:
            continue
        line = acc.setdefault(r.category_id, {"name": r.name or "Uncategorized", "bucket": r.bucket,
                                              "cur": ZERO, "prev": ZERO, "n": 0})
        if r.period == "cur":
            line["cur"] += r.amount
            line["n"] += r.n
        else:
            line["prev"] += r.amount
    lines = [
        {"category_id": cid, "name": v["name"], "bucket": v["bucket"], "amount": fmt(v["cur"]),
         "previous_amount": fmt(v["prev"]), "change": fmt(v["cur"] - v["prev"]), "txn_count": v["n"]}
        for cid, v in acc.items()
    ]
    return sorted(lines, key=lambda c: (-Decimal(c["amount"]), -Decimal(c["previous_amount"]), c["name"]))


def summary(s: Session, member_id: int, month: str, month_start_day: int = 1) -> dict[str, Any]:
    """One grouped query covers the month cycle and the one before it."""
    cur_start, cur_end = cycle_bounds(month, month_start_day)
    prev = previous_month(month)
    prev_start, _ = cycle_bounds(prev, month_start_day)
    base = (
        select(case((Txn.occurred_at >= cur_start, literal_column("'cur'")), else_=literal_column("'prev'"))
               .label("period"), Txn.direction, Txn.bucket, Txn.kind, Txn.category_id, Txn.amount)
        .where(Txn.member_id == member_id, Txn.occurred_at >= prev_start, Txn.occurred_at < cur_end)
        .subquery()
    )
    rows = s.execute(
        select(base.c.period, base.c.direction, base.c.bucket, base.c.kind, base.c.category_id, Category.name,
               func.sum(base.c.amount).label("amount"), func.count().label("n"))
        .outerjoin(Category, Category.id == base.c.category_id)
        .group_by(base.c.period, base.c.direction, base.c.bucket, base.c.kind, base.c.category_id, Category.name)
    ).all()
    totals = _totals([r for r in rows if r.period == "cur"])
    prev_totals = _totals([r for r in rows if r.period == "prev"])
    buckets = []
    for b in SUMMARY_BUCKETS:
        a, p = Decimal(totals[b]), Decimal(prev_totals[b])
        buckets.append({"bucket": b, "amount": fmt(a), "previous_amount": fmt(p), "change": fmt(a - p)})
    return {
        "month": month, "previous_month": prev, "currency": "INR", "month_start_day": month_start_day,
        "period": {"start": cur_start, "end": cur_end - timedelta(days=1)},
        "totals": totals, "previous_totals": prev_totals,
        "buckets": buckets,
        "categories": _category_lines(rows, "debit", EXPENSE_BUCKETS, with_uncategorized=True),
        "income_categories": _category_lines(rows, "credit", ("income",), with_uncategorized=False),
        "loans": loans.month_lines(s, member_id, cur_start, cur_end),
    }


def months(s: Session, member_id: int, month_start_day: int = 1) -> dict[str, Any]:
    """Month cycles with data. A cycle is labelled by the calendar month it starts in."""
    as_of = today_ist()
    shift = bindparam("shift", month_start_day - 1, type_=Integer)
    m = func.to_char(Txn.occurred_at - shift, "YYYY-MM").label("month")
    base = select(m, Txn.occurred_at).where(Txn.member_id == member_id).subquery()
    rows = s.execute(select(base.c.month, func.max(base.c.occurred_at).label("through"), func.count().label("n"))
                     .group_by(base.c.month).order_by(base.c.month)).all()
    items = []
    for r in rows:
        start, end = cycle_bounds(r.month, month_start_day)
        items.append({"month": r.month, "start": start, "end": end - timedelta(days=1), "through": r.through,
                      "complete": end <= as_of, "txn_count": r.n})
    return {"as_of": as_of, "month_start_day": month_start_day, "items": items}


# --- trends --------------------------------------------------------------------------------

GRANULARITIES = ("week", "month", "quarter", "fy")
GROUP_BYS = ("total", "category", "merchant", "kind", "account")


def _period_sql(granularity: str, shift: Any) -> Any:
    """Bucket start in SQL; `shift` = month_start_day - 1 (weeks ignore it). Twin of period_start()."""
    d = Txn.occurred_at
    if granularity == "week":
        return cast(func.date_trunc("week", d), Date)
    if granularity in ("month", "quarter"):
        return cast(func.date_trunc(granularity, d - shift) + func.make_interval(0, 0, 0, shift), Date)
    fy_year = cast(func.extract("year", (d - shift) - literal_column("interval '3 months'")), Integer)
    return func.make_date(fy_year, 4, 1) + shift


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def period_start(d: date, granularity: str, month_start_day: int) -> date:
    if granularity == "week":
        return d - timedelta(days=d.weekday())
    shifted = d - timedelta(days=month_start_day - 1)
    if granularity == "month":
        first = shifted.replace(day=1)
    elif granularity == "quarter":
        first = date(shifted.year, (shifted.month - 1) // 3 * 3 + 1, 1)
    else:
        first = date(shifted.year if shifted.month >= 4 else shifted.year - 1, 4, 1)
    return first + timedelta(days=month_start_day - 1)


def _next_start(start: date, granularity: str, month_start_day: int) -> date:
    if granularity == "week":
        return start + timedelta(days=7)
    step = {"month": 1, "quarter": 3, "fy": 12}[granularity]
    return _add_months(start.replace(day=1), step) + timedelta(days=month_start_day - 1)


def period_grid(end: date, granularity: str, periods: int, month_start_day: int) -> list[tuple[date, date]]:
    starts = [period_start(end, granularity, month_start_day)]
    for _ in range(periods - 1):
        starts.append(period_start(starts[-1] - timedelta(days=1), granularity, month_start_day))
    starts.reverse()
    return [(st, _next_start(st, granularity, month_start_day) - timedelta(days=1)) for st in starts]


def _committed(keys: set[str]) -> Any:
    """Committed: the payee has a live recurring series (services/recurring.detect)."""
    return recurring.payee_key_expr().in_(keys) if keys else false()


def is_expense() -> Any:
    """The one spend definition, shared with /api/summary: debits in the everyday, one-off and
    card buckets, plus uncategorized debits. Refund credits are reported apart, never netted."""
    return and_(Txn.direction == "debit", or_(Txn.bucket.in_(EXPENSE_BUCKETS), Txn.category_id.is_(None)))


def _measure() -> Any:
    """For group_by=total: which summary figure a txn feeds, or NULL for none."""
    return case(
        (is_expense(), literal_column("'expense'")),
        (and_(Txn.direction == "credit", Txn.bucket == "income"), literal_column("'income'")),
        (and_(Txn.direction == "debit", Txn.bucket == "invest"), literal_column("'invested'")),
        else_=None,
    )


def trends(s: Session, member_id: int, *, granularity: str, periods: int, group_by: str, end: date | None,
           month_start_day: int, limit: int) -> dict[str, Any]:
    grid = period_grid(end or today_ist(), granularity, periods, month_start_day)
    shift = bindparam("shift", month_start_day - 1, type_=Integer)
    period = _period_sql(granularity, shift).label("period_start")
    in_range = and_(Txn.member_id == member_id, Txn.occurred_at >= grid[0][0], Txn.occurred_at <= grid[-1][1])
    cells: dict[str, dict[date, list]] = defaultdict(dict)

    def add(key: str, ps: date, amount: Decimal, n: int) -> None:
        cell = cells[key].setdefault(ps, [ZERO, 0])
        cell[0] += amount
        cell[1] += n

    if group_by == "total":
        committed = _committed(recurring.committed_keys(recurring.detect(s, member_id)))
        base = (select(period, _measure().label("measure"), committed.label("committed"),
                       (Txn.kind == "refund").label("refund"), Txn.amount)
                .where(in_range).subquery())
        rows = s.execute(
            select(base.c.period_start, base.c.measure, base.c.committed, base.c.refund,
                   func.sum(base.c.amount).label("amount"), func.count().label("n"))
            .where(base.c.measure.is_not(None))
            .group_by(base.c.period_start, base.c.measure, base.c.committed, base.c.refund)
        ).all()
        for r in rows:
            if r.measure == "expense":
                add("total", r.period_start, r.amount, r.n)
                add("committed" if r.committed else "discretionary", r.period_start, r.amount, r.n)
            else:
                add(r.measure, r.period_start, r.amount, r.n)
                if r.measure == "income" and r.refund:
                    add("refunds", r.period_start, r.amount, r.n)
        keys = ["total", "committed", "discretionary", "income", "refunds", "invested"]
    else:
        if group_by == "kind":
            # Each kind in its natural direction: credits for income/refund, debits otherwise.
            natural = or_(and_(Txn.kind.in_(("income", "refund")), Txn.direction == "credit"),
                          and_(Txn.kind.not_in(("income", "refund")), Txn.direction == "debit"))
            amount, where, key = case((natural, Txn.amount), else_=-Txn.amount), true(), Txn.kind
        else:
            amount, where = Txn.amount, is_expense()
            if group_by == "category":
                key = func.coalesce(Category.name, literal_column("'Uncategorized'"))
            elif group_by == "account":
                key = func.coalesce(cast(Txn.account_id, Text), literal_column("'none'"))
            else:
                key = func.coalesce(Txn.merchant_norm, literal_column("'Unknown'"))
        base = (select(period, key.label("key"), amount.label("amount"))
                .outerjoin(Category, Category.id == Txn.category_id).where(in_range, where).subquery())
        rows = s.execute(select(base.c.period_start, base.c.key, func.sum(base.c.amount).label("amount"),
                                func.count().label("n")).group_by(base.c.period_start, base.c.key)).all()
        labels = recurring.account_labels(s, member_id) if group_by == "account" else None
        for r in rows:
            k = r.key if labels is None else ("No account" if r.key == "none" else labels.get(int(r.key), r.key))
            add(k, r.period_start, r.amount, r.n)
        ranked = sorted(cells, key=lambda k: (-sum(v[0] for v in cells[k].values()), k))
        keys = ranked[:limit]
        if len(ranked) > limit:
            for k in ranked[limit:]:
                for ps, (a, n) in cells[k].items():
                    add("Other", ps, a, n)
            keys.append("Other")
    series = []
    for k in keys:
        pts = [{"period_start": st, "amount": fmt(cells[k].get(st, [ZERO, 0])[0]),
                "count": cells[k].get(st, [ZERO, 0])[1]} for st, _ in grid]
        series.append({"key": k, "total": fmt(sum((Decimal(p["amount"]) for p in pts), ZERO)), "points": pts})
    return {"granularity": granularity, "group_by": group_by, "month_start_day": month_start_day,
            "periods": [{"start": st, "end": e} for st, e in grid], "series": series}
