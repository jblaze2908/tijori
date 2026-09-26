"""Source health for Settings: raw messages no parser could read, and recent statement uploads."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tijori.models import Account, RawAttachment, RawMessage, Statement
from tijori.money import fmt
from tijori.services.common import account_label

RECENT_UPLOADS = 5


def parse_queue(s: Session, member_id: int) -> dict[str, Any]:
    """Unread formats grouped by sender and subject (an upload's subject is its filename). Two queries."""
    rows = s.execute(
        select(RawMessage.sender, RawMessage.subject, RawMessage.parse_status, func.count().label("n"),
               func.min(RawMessage.received_at).label("first"), func.max(RawMessage.received_at).label("last"))
        .where(RawMessage.member_id == member_id, RawMessage.parse_status.in_(("parser_needed", "failed")))
        .group_by(RawMessage.sender, RawMessage.subject, RawMessage.parse_status)
        .order_by(func.max(RawMessage.received_at).desc())
    ).all()
    uploads = s.execute(
        select(RawMessage.id, RawMessage.subject, RawMessage.received_at, RawMessage.parse_status,
               Statement.period_start, Statement.period_end, Statement.diff, Statement.reconciled_at,
               Account.institution, Account.name, Account.mask)
        .outerjoin(RawAttachment, RawAttachment.raw_message_id == RawMessage.id)
        .outerjoin(Statement, Statement.raw_attachment_id == RawAttachment.id)
        .outerjoin(Account, Account.id == Statement.account_id)
        .where(RawMessage.member_id == member_id, RawMessage.sender == "upload")
        .order_by(RawMessage.received_at.desc(), RawMessage.id.desc()).limit(RECENT_UPLOADS)
    ).all()
    return {
        "unparsed": [{"sender": r.sender, "subject": r.subject, "status": r.parse_status, "count": r.n,
                      "first_seen": r.first, "last_seen": r.last} for r in rows],
        "uploads": [{"id": u.id, "filename": u.subject, "received_at": u.received_at, "status": u.parse_status,
                     "account": account_label(u.institution, u.name, u.mask),
                     "period_start": u.period_start, "period_end": u.period_end,
                     "diff": fmt(u.diff) if u.diff is not None else None,
                     "reconciled": u.reconciled_at is not None} for u in uploads],
    }
