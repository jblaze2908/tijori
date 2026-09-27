"""Read endpoints (docs/api.md). Each request runs in one member-scoped transaction; queries also
filter member_id explicitly, because RLS lets a grantee read shared rows and those must never
leak into the caller's own totals."""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status
from sqlalchemy import or_, select

from tijori.app.deps import MemberDep
from tijori.app.schemas import (
    BackupStatus,
    LinkCandidates,
    LoanDetail,
    LoanPicker,
    Loans,
    McpTokens,
    RawSources,
    Accounts,
    Alerts,
    CardList,
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
from tijori.services import (alerts, budgets, cards, loans, mcp_tokens, members, networth, ops, raw, recurring, reports,
                             sources, txn_edit, txns)
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
    paid_with: Literal["bank", "card"] | None = None,
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
                       min_amount=min_amount, max_amount=max_amount, month_start_day=msd, sort=sort,
                       paid_with=paid_with)
    return txns.list_txns(db.session, db.ctx.member_id, f, page, page_size)


@router.get("/transactions/{txn_id}/link-candidates", response_model=LinkCandidates)
def link_candidates(db: MemberDep, txn_id: Annotated[int, Path(ge=1)]) -> dict:
    return {"items": txn_edit.link_candidates(db.session, db.ctx.member_id, txn_id)}


@router.get("/transactions/{txn_id}/sources", response_model=RawSources)
def txn_sources(request: Request, db: MemberDep, txn_id: Annotated[int, Path(ge=1)]) -> dict:
    return {"items": raw.sources_of(db.session, db.ctx.member_id, request.app.state.settings.blob_dir, txn_id,
                                  request.app.state.settings.secret_box())}


@router.get("/raw/attachments/{attachment_id}")
def raw_attachment(request: Request, db: MemberDep, attachment_id: Annotated[int, Path(ge=1)]) -> Response:
    """The original statement file, as a download (never rendered inline)."""
    name, data = raw.attachment(db.session, db.ctx.member_id, request.app.state.settings.blob_dir, attachment_id,
                                request.app.state.settings.secret_box())
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120] or "statement.pdf"
    return Response(data, media_type="application/pdf" if data[:5] == b"%PDF-" else "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"', "X-Content-Type-Options": "nosniff",
                             "Cache-Control": "private, no-store"})


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
             "credit_bucket": c.credit_bucket, "parent_id": c.parent_id,
             "scope": "household" if c.member_id is None else "member"} for c in rows]


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
    return budgets.budgets(db.session, db.ctx.member_id, month, month_start_day(db.session, db.ctx.member_id))


@router.get("/recurring", response_model=RecurringList)
def get_recurring(db: MemberDep) -> dict:
    return recurring.list_recurring(db.session, db.ctx.member_id)


@router.get("/cards", response_model=CardList)
def get_cards(db: MemberDep) -> dict:
    return cards.card_status(db.session, db.ctx.member_id)


@router.get("/loans", response_model=Loans)
def get_loans(db: MemberDep) -> dict:
    return loans.list_loans(db.session, db.ctx.member_id)


@router.get("/loans/for-txn/{txn_id}", response_model=LoanPicker)
def loan_picker(db: MemberDep, txn_id: Annotated[int, Path(ge=1)]) -> dict:
    return loans.for_txn(db.session, db.ctx.member_id, txn_id)


@router.get("/loans/{loan_id}", response_model=LoanDetail)
def get_loan(db: MemberDep, loan_id: Annotated[int, Path(ge=1)]) -> dict:
    return loans.get_loan(db.session, db.ctx.member_id, loan_id)


@router.get("/ops/backup", response_model=BackupStatus)
def backup_status(db: MemberDep) -> dict:
    return ops.backup_status(db.session)


@router.get("/mcp/tokens", response_model=McpTokens)
def get_mcp_tokens(db: MemberDep) -> dict:
    return {"items": mcp_tokens.list_tokens(db.session, db.ctx.member_id)}


@router.get("/alerts", response_model=Alerts)
def get_alerts(db: MemberDep, month: Annotated[str, Query(pattern=MONTH_PATTERN)]) -> dict:
    return alerts.month_alerts(db.session, db.ctx.member_id, month, month_start_day(db.session, db.ctx.member_id))


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
