"""Shared helpers for the service layer: dates in IST, the txn read shape, masking, audit."""

import hashlib
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.models import Account, AuditLog, Category, Txn
from tijori.money import fmt

# India has no DST, so a fixed offset is exact and needs no tz database in the image.
IST = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def today_ist() -> date:
    return datetime.now(IST).date()


def month_bounds(month: str) -> tuple[date, date]:
    """[first day, first day of next month) for 'YYYY-MM'."""
    y, m = (int(p) for p in month.split("-"))
    return date(y, m, 1), date(y + (m == 12), m % 12 + 1, 1)


def cycle_bounds(month: str, month_start_day: int = 1) -> tuple[date, date]:
    """[start, end) of the month cycle labelled 'YYYY-MM': it starts on `month_start_day` of that
    month (1 = calendar month; 25 = the 25th to the 24th, a salary cycle)."""
    start, end = month_bounds(month)
    shift = timedelta(days=month_start_day - 1)
    return start + shift, end + shift


def month_start_day(s: Session, member_id: int) -> int:
    """One primary-key lookup; every month-based endpoint needs it."""
    from tijori.models import Member

    settings = s.scalar(select(Member.settings).where(Member.id == member_id)) or {}
    return int(settings.get("month_start_day", 1))


def previous_month(month: str) -> str:
    y, m = (int(p) for p in month.split("-"))
    return f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"


def account_label(institution: str | None, name: str | None, mask: str | None) -> str | None:
    if institution is None:
        return None
    return f"{name or institution} ••{mask}" if mask else (name or institution)


def txn_query() -> Select[Any]:
    return (
        select(Txn, Category.name.label("category_name"), Account.institution, Account.name.label("account_name"),
               Account.kind.label("account_kind"), Account.mask)
        .outerjoin(Category, Category.id == Txn.category_id)
        .outerjoin(Account, Account.id == Txn.account_id)
    )


def account_ref(account_id: int | None, institution: str | None, name: str | None, kind: str | None,
                mask: str | None) -> dict[str, Any] | None:
    if account_id is None:
        return None
    return {"id": account_id, "institution": institution, "name": name,
            "label": account_label(institution, name, mask), "kind": kind, "mask": mask}


def txn_out(row: Any) -> dict[str, Any]:
    """API shape of one txn. UPI handles are shown in full: the member reads only their own data
    (Jai's call, 2026-09-27). Anything leaving Tijori, such as MCP, must mask them itself."""
    t: Txn = row.Txn
    vpa, narration = t.vpa, t.narration
    return {
        "id": t.id, "occurred_at": t.occurred_at, "posted_at": t.posted_at, "amount": fmt(t.amount),
        "currency": t.currency, "direction": t.direction, "kind": t.kind, "merchant": t.merchant_norm,
        "counterparty": t.counterparty, "vpa": vpa, "payee_key": t.payee_key, "narration": narration,
        "account": account_ref(t.account_id, row.institution, row.account_name, row.account_kind, row.mask),
        "category": {"id": t.category_id, "name": row.category_name} if t.category_id is not None else None,
        "bucket": t.bucket, "classified_by": t.classified_by, "rule_id": t.rule_id,
        "review_reason": t.review_reason, "status": t.status, "sources": list(t.sources or []),
        "notes": t.notes, "tags": list(t.tags or []),
    }


def audit(s: Session, ctx: MemberContext, actor: str, action: str, target: str | None,
          detail: dict[str, Any] | None = None) -> None:
    s.add(AuditLog(member_id=ctx.member_id, actor=actor, action=action, target=target,
                   at=datetime.now(UTC), detail_json=detail or {}))


def category_by_ref(s: Session, ctx: MemberContext, category_id: int | None, name: str | None) -> Category | None:
    """A category visible to the member (RLS hides other households), by id or by name."""
    if category_id is not None:
        return s.get(Category, category_id)
    return s.scalars(
        select(Category).where(Category.household_id == ctx.household_id, Category.name == name)
        .order_by(Category.member_id.is_(None)).limit(1)
    ).first()
