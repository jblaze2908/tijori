"""Aggregates: monthly summary, months with data, budgets, trends. Grouping runs in SQL; Python
only folds the already-aggregated rows."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, Integer, and_, bindparam, case, cast, exists, func, literal_column, or_, select, true
from sqlalchemy.orm import Session

from tijori.classify.taxonomy import EXPENSE_BUCKETS
from tijori.models import Budget, Category, Recurring, Txn
from tijori.money import ZERO, fmt
from tijori.services.common import month_bounds, previous_month, today_ist

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


def summary(s: Session, member_id: int, month: str) -> dict[str, Any]:
    """One grouped query covers the month and the one before it."""
    cur_start, cur_end = month_bounds(month)
    prev = previous_month(month)
    prev_start, _ = month_bounds(prev)
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
        "month": month, "previous_month": prev, "currency": "INR", "totals": totals, "previous_totals": prev_totals,
        "buckets": buckets,
        "categories": _category_lines(rows, "debit", EXPENSE_BUCKETS, with_uncategorized=True),
        "income_categories": _category_lines(rows, "credit", ("income",), with_uncategorized=False),
    }


def months(s: Session, member_id: int) -> dict[str, Any]:
    as_of = today_ist()
    m = func.to_char(func.date_trunc("month", Txn.occurred_at), "YYYY-MM").label("month")
    base = select(m, Txn.occurred_at).where(Txn.member_id == member_id).subquery()
    rows = s.execute(select(base.c.month, func.max(base.c.occurred_at).label("through"), func.count().label("n"))
                     .group_by(base.c.month).order_by(base.c.month)).all()
    return {"as_of": as_of, "items": [
        {"month": r.month, "through": r.through, "complete": month_bounds(r.month)[1] <= as_of, "txn_count": r.n}
        for r in rows]}


def budgets(s: Session, member_id: int, month: str) -> dict[str, Any]:
    start, end = month_bounds(month)
    spent = (select(Txn.category_id, func.sum(Txn.amount).label("spent"))
             .where(Txn.member_id == member_id, Txn.direction == "debit", Txn.occurred_at >= start,
                    Txn.occurred_at < end)
             .group_by(Txn.category_id).subquery())
    rows = s.execute(
        select(Budget.category_id, Category.name, Budget.amount, Budget.rollover,
               func.coalesce(spent.c.spent, 0).label("spent"))
        .join(Category, Category.id == Budget.category_id)
        .outerjoin(spent, spent.c.category_id == Budget.category_id)
        .where(Budget.member_id == member_id, Budget.period == "monthly")
        .order_by(Category.sort_order, Category.name)
    ).all()
    return {"month": month, "items": [
        {"category_id": r.category_id, "category": r.name, "amount": fmt(r.amount), "spent": fmt(r.spent),
         "remaining": fmt(r.amount - r.spent), "rollover": r.rollover} for r in rows]}


# --- trends --------------------------------------------------------------------------------

GRANULARITIES = ("week", "month", "quarter", "fy")
GROUP_BYS = ("total", "category", "merchant", "kind")


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


def _series_key(group_by: str) -> Any:
    if group_by == "total":
        # Committed: the merchant has an active recurring series (none until recurring detection, M2).
        committed = exists().where(Recurring.member_id == Txn.member_id, Recurring.status == "active",
                                   func.lower(Recurring.merchant_norm) == func.lower(Txn.merchant_norm))
        return case((committed, literal_column("'committed'")), else_=literal_column("'discretionary'"))
    if group_by == "category":
        return func.coalesce(Category.name, literal_column("'Uncategorized'"))
    if group_by == "merchant":
        return func.coalesce(Txn.merchant_norm, literal_column("'Unknown'"))
    return Txn.kind


def trends(s: Session, member_id: int, *, granularity: str, periods: int, group_by: str, end: date | None,
           month_start_day: int, limit: int) -> dict[str, Any]:
    grid = period_grid(end or today_ist(), granularity, periods, month_start_day)
    shift = bindparam("shift", month_start_day - 1, type_=Integer)
    if group_by == "kind":
        # Each kind in its natural direction: credits for income/refund, debits otherwise.
        natural = or_(and_(Txn.kind.in_(("income", "refund")), Txn.direction == "credit"),
                      and_(Txn.kind.not_in(("income", "refund")), Txn.direction == "debit"))
        amount, where = case((natural, Txn.amount), else_=-Txn.amount), true()
    else:
        # Spend: spend/fee/cash debits plus card bills (card spend until cards are itemised),
        # minus refund credits that are not reversal pairs.
        amount = case((Txn.direction == "debit", Txn.amount), else_=-Txn.amount)
        where = or_(and_(Txn.direction == "debit", or_(Txn.kind.in_(("spend", "fee", "cash")), Txn.bucket == "card")),
                    and_(Txn.direction == "credit", Txn.kind == "refund", Txn.bucket.is_distinct_from("excluded")))
    base = (
        select(_period_sql(granularity, shift).label("period_start"), _series_key(group_by).label("key"),
               amount.label("amount"))
        .outerjoin(Category, Category.id == Txn.category_id)
        .where(Txn.member_id == member_id, Txn.occurred_at >= grid[0][0], Txn.occurred_at <= grid[-1][1], where)
        .subquery()
    )
    rows = s.execute(select(base.c.period_start, base.c.key, func.sum(base.c.amount).label("amount"),
                            func.count().label("n")).group_by(base.c.period_start, base.c.key)).all()

    cells: dict[str, dict[date, list]] = defaultdict(dict)
    for r in rows:
        cells[r.key][r.period_start] = [r.amount, r.n]

    def merge(keys: list[str]) -> dict[date, list]:
        out: dict[date, list] = {}
        for k in keys:
            for ps, (a, n) in cells.get(k, {}).items():
                o = out.setdefault(ps, [ZERO, 0])
                o[0] += a
                o[1] += n
        return out

    if group_by == "total":
        cells["total"] = merge(["committed", "discretionary"])
        keys = ["total", "committed", "discretionary"]
    else:
        ranked = sorted(cells, key=lambda k: (-sum(v[0] for v in cells[k].values()), k))
        keys = ranked[:limit]
        if len(ranked) > limit:
            cells["Other"] = merge(ranked[limit:])
            keys.append("Other")
    series = []
    for k in keys:
        pts = [{"period_start": st, "amount": fmt(cells[k].get(st, [ZERO, 0])[0]),
                "count": cells[k].get(st, [ZERO, 0])[1]} for st, _ in grid]
        series.append({"key": k, "total": fmt(sum((Decimal(p["amount"]) for p in pts), ZERO)), "points": pts})
    return {"granularity": granularity, "group_by": group_by, "month_start_day": month_start_day,
            "periods": [{"start": st, "end": e} for st, e in grid], "series": series}
