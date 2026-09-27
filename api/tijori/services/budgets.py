"""Monthly budgets per category (PLAN §8): limit, optional rollover of last cycle's unspent amount, and
pace. Spend uses the one spend definition (reports.is_expense). Two grouped queries per call."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.models import Budget, Category, Txn
from tijori.money import ZERO, fmt
from tijori.services.common import audit, cycle_bounds, today_ist
from tijori.services.errors import Invalid, NotFound
from tijori.services.reports import expense_amount, is_expense

AHEAD = Decimal("1.10")  # spend more than 10% above the straight-line share of the limit is "ahead of pace"


def _spent(s: Session, member_id: int, start: date, end: date) -> dict[int, Decimal]:
    return dict(s.execute(select(Txn.category_id, func.sum(expense_amount())).where(
        Txn.member_id == member_id, is_expense(), Txn.occurred_at >= start, Txn.occurred_at < end,
        Txn.category_id.is_not(None)).group_by(Txn.category_id)).all())


def _previous(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - (m == 1)}-{12 if m == 1 else m - 1:02d}"


def budgets(s: Session, member_id: int, month: str, month_start_day: int = 1, today: date | None = None) -> dict[str, Any]:
    start, end = cycle_bounds(month, month_start_day)
    today = today or today_ist()
    days = (end - start).days
    day = max(0, min(days, (today - start).days + 1))
    rows = s.execute(select(Budget.category_id, Category.name, Budget.amount, Budget.rollover)
                     .join(Category, Category.id == Budget.category_id)
                     .where(Budget.member_id == member_id, Budget.period == "monthly")
                     .order_by(Category.sort_order, Category.name)).all()
    spent = _spent(s, member_id, start, end)
    prev = _spent(s, member_id, *cycle_bounds(_previous(month), month_start_day)) if any(r.rollover for r in rows) else {}
    items = []
    for r in rows:
        carry = max(ZERO, r.amount - prev.get(r.category_id, ZERO)) if r.rollover else ZERO
        limit = r.amount + carry
        used = spent.get(r.category_id, ZERO)
        expected = (limit * day / days).quantize(Decimal("0.01")) if days else limit
        state = "over" if used > limit else "ahead" if day and used > expected * AHEAD else "ok"
        items.append({"category_id": r.category_id, "category": r.name, "amount": fmt(r.amount), "carry": fmt(carry),
                      "limit": fmt(limit), "spent": fmt(used), "remaining": fmt(limit - used),
                      "expected_by_today": fmt(expected), "projected": fmt((used / day * days).quantize(Decimal("0.01")) if day else used),
                      "state": state, "rollover": r.rollover})
    return {"month": month, "day": day, "days": days, "items": items,
            "totals": {"limit": fmt(sum((Decimal(i["limit"]) for i in items), ZERO)),
                       "spent": fmt(sum((Decimal(i["spent"]) for i in items), ZERO))}}


def set_budget(s: Session, ctx: MemberContext, actor: str, category_id: int, amount: Decimal | None,
               rollover: bool) -> dict[str, Any]:
    c = s.get(Category, category_id)
    if c is None:
        raise NotFound("category not found")
    if c.bucket not in ("everyday", "oneoff"):
        raise Invalid("budgets are for spend categories")
    if amount is None or amount == 0:
        s.execute(delete(Budget).where(Budget.member_id == ctx.member_id, Budget.category_id == category_id))
        audit(s, ctx, actor, "budget.remove", f"category:{category_id}", {})
        return {"category_id": category_id, "amount": None, "rollover": False}
    stmt = pg_insert(Budget).values(member_id=ctx.member_id, category_id=category_id, period="monthly", amount=amount,
                                    rollover=rollover)
    s.execute(stmt.on_conflict_do_update(index_elements=["member_id", "category_id", "period"],
                                         set_={"amount": stmt.excluded.amount, "rollover": stmt.excluded.rollover}))
    audit(s, ctx, actor, "budget.set", f"category:{category_id}", {"rollover": rollover})
    return {"category_id": category_id, "amount": fmt(amount), "rollover": rollover}
