"""Read endpoints (docs/api.md). Each request runs in one member-scoped transaction; queries also
filter member_id explicitly, because RLS lets a grantee read shared rows and those must never
leak into the caller's own totals."""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import or_, select

from tijori.app.deps import MemberDep
from tijori.app.schemas import (
    Accounts,
    Alerts,
    FilingStats,
    Holdings,
    LiveNetWorth,
    ParseQueue,
    RecurringList,
    Rules,
    Budgets,
    CategoryOut,
    InboxGroupPage,
    InboxPage,
    Me,
    Months,
    NetWorth,
    SettingsOut,
    Summary,
    Trends,
    TxnDetail,
    TxnPage,
)
from tijori.classify.taxonomy import KINDS
from tijori.models import Category
from tijori.services import members, networth, recurring, reports, sources, txns
from tijori.services.common import month_start_day, today_ist

router = APIRouter(prefix="/api")

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
MAX_PAGE_SIZE = 200
Page = Annotated[int, Query(ge=1, le=100_000)]
PageSize = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


def _unprocessable(field: str, msg: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, [{"loc": ["query", field], "msg": msg}])


@router.get("/me", response_model=Me)
def get_me(db: MemberDep) -> dict:
    return members.me(db.session, db.ctx)


@router.get("/months", response_model=Months)
def get_months(db: MemberDep) -> dict:
    return reports.months(db.session, db.ctx.member_id, month_start_day(db.session, db.ctx.member_id))


@router.get("/accounts", response_model=Accounts)
def get_accounts(db: MemberDep) -> dict:
    return members.accounts(db.session, db.ctx.member_id)


@router.get("/summary", response_model=Summary)
def summary(db: MemberDep, month: Annotated[str, Query(pattern=MONTH_PATTERN)]) -> dict:
    return reports.summary(db.session, db.ctx.member_id, month, month_start_day(db.session, db.ctx.member_id))


@router.get("/transactions", response_model=TxnPage)
def transactions(
    db: MemberDep,
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    account: Annotated[list[int] | None, Query(max_length=20)] = None,
    category: Annotated[list[str] | None, Query(max_length=40)] = None,
    kind: Annotated[Literal[KINDS] | None, Query()] = None,  # type: ignore[valid-type]
    direction: Annotated[Literal["debit", "credit"] | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    min_amount: Annotated[Decimal | None, Query(alias="min", ge=0, max_digits=14, decimal_places=2)] = None,
    max_amount: Annotated[Decimal | None, Query(alias="max", ge=0, max_digits=14, decimal_places=2)] = None,
    sort: Literal[txns.SORTS] = "date_desc",  # type: ignore[valid-type]
    page: Page = 1,
    page_size: PageSize = 50,
) -> dict:
    if any(a < 1 for a in account or []):
        raise _unprocessable("account", "must be account ids")
    if any(not re.fullmatch(r"none|\d{1,18}", c) for c in category or []):
        raise _unprocessable("category", "must be category ids or none")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise _unprocessable("min", "must not exceed max")
    if date_from and date_to and date_from > date_to:
        raise _unprocessable("from", "must not be after to")
    msd = month_start_day(db.session, db.ctx.member_id) if month else 1
    f = txns.TxnFilter(month=month, date_from=date_from, date_to=date_to, accounts=tuple(account or ()),
                       categories=tuple(category or ()), kind=kind, direction=direction, q=q,
                       min_amount=min_amount, max_amount=max_amount, month_start_day=msd, sort=sort)
    return txns.list_txns(db.session, db.ctx.member_id, f, page, page_size)


@router.get("/transactions/{txn_id}", response_model=TxnDetail)
def transaction(db: MemberDep, txn_id: Annotated[int, Path(ge=1)]) -> dict:
    return txns.get_txn(db.session, db.ctx.member_id, txn_id)


@router.get("/categories", response_model=list[CategoryOut])
def categories(db: MemberDep) -> list[dict]:
    rows = db.session.scalars(
        select(Category)
        .where(Category.household_id == db.ctx.household_id,
               or_(Category.member_id.is_(None), Category.member_id == db.ctx.member_id))
        .order_by(Category.sort_order, Category.name)
    ).all()
    return [{"id": c.id, "name": c.name, "description": c.description, "kind": c.kind, "bucket": c.bucket,
             "parent_id": c.parent_id, "scope": "household" if c.member_id is None else "member"} for c in rows]


@router.get("/inbox", response_model=InboxPage | InboxGroupPage)
def inbox(db: MemberDep, group: Literal["txn", "payee"] = "txn", page: Page = 1,
          page_size: PageSize = 50) -> dict:
    if group == "payee":
        return txns.inbox_by_payee(db.session, db.ctx.member_id, page, page_size)
    return txns.inbox(db.session, db.ctx.member_id, page, page_size)


@router.get("/networth", response_model=NetWorth)
def get_networth(db: MemberDep) -> dict:
    return networth.list_networth(db.session, db.ctx.member_id)


@router.get("/settings", response_model=SettingsOut)
def get_settings(db: MemberDep) -> dict:
    return members.get_settings(db.session, db.ctx.member_id)


@router.get("/trends", response_model=Trends)
def trends(
    db: MemberDep,
    granularity: Literal[reports.GRANULARITIES] = "month",  # type: ignore[valid-type]
    periods: Annotated[int, Query(ge=1, le=60)] = 12,
    group_by: Literal[reports.GROUP_BYS] = "total",  # type: ignore[valid-type]
    end: date | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict:
    msd = month_start_day(db.session, db.ctx.member_id)
    return reports.trends(db.session, db.ctx.member_id, granularity=granularity, periods=periods,
                          group_by=group_by, end=end, month_start_day=msd, limit=limit)


@router.get("/budgets", response_model=Budgets)
def get_budgets(db: MemberDep, month: Annotated[str, Query(pattern=MONTH_PATTERN)]) -> dict:
    return reports.budgets(db.session, db.ctx.member_id, month, month_start_day(db.session, db.ctx.member_id))


@router.get("/recurring", response_model=RecurringList)
def get_recurring(db: MemberDep) -> dict:
    return recurring.list_recurring(db.session, db.ctx.member_id)


@router.get("/alerts", response_model=Alerts)
def get_alerts(db: MemberDep, month: Annotated[str, Query(pattern=MONTH_PATTERN)]) -> dict:
    return recurring.alerts(db.session, db.ctx.member_id, month, month_start_day(db.session, db.ctx.member_id))


@router.get("/inbox/stats", response_model=FilingStats)
def inbox_stats(db: MemberDep, month: Annotated[str, Query(pattern=MONTH_PATTERN)]) -> dict:
    return txns.filing_stats(db.session, db.ctx.member_id, month, month_start_day(db.session, db.ctx.member_id))


@router.get("/rules", response_model=Rules)
def get_rules(db: MemberDep) -> dict:
    return txns.list_rules(db.session, db.ctx)


@router.get("/networth/live", response_model=LiveNetWorth)
def networth_live(db: MemberDep) -> dict:
    return networth.live(db.session, db.ctx.member_id, today_ist())


@router.get("/holdings", response_model=Holdings)
def get_holdings(db: MemberDep) -> dict:
    return networth.holdings(db.session, db.ctx.member_id)


@router.get("/sources/queue", response_model=ParseQueue)
def parse_queue(db: MemberDep) -> dict:
    return sources.parse_queue(db.session, db.ctx.member_id)
