"""Raw-file retention: stored emails and PDFs older than the member's setting are deleted; the rows and
everything parsed from them stay (purged_at is set). Mail still waiting for a parser or a password, or a statement
that didn't reconcile, is kept twice as long, so it can still be re-read. A file shared by two rows (the same PDF mailed twice) goes
only when no unpurged row points at it. Runs daily from the collector; one pass is a few indexed queries
plus one unlink per file."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.models import Member, RawAttachment, RawMessage, Statement
from tijori.services.members import DEFAULT_RETENTION_DAYS

log = logging.getLogger("tijori.retention")
DONE = ("parsed", "ignored")
WAITING = ("failed", "parser_needed", "needs_password")


def _unlink(s: Session, member_id: int, root: Path, ref: str) -> bool:
    live = s.scalar(select(RawMessage.id).where(RawMessage.member_id == member_id, RawMessage.blob_ref == ref,
                                                RawMessage.purged_at.is_(None)).limit(1)) or \
        s.scalar(select(RawAttachment.id).where(RawAttachment.member_id == member_id, RawAttachment.blob_ref == ref,
                                                RawAttachment.purged_at.is_(None)).limit(1))
    if live:
        return False
    path = (root / ref).resolve()
    if root.resolve() in path.parents and path.exists():
        path.unlink()
        return True
    return False


def purge(s: Session, ctx: MemberContext, root: Path, now: datetime | None = None) -> dict[str, int]:
    settings = s.scalar(select(Member.settings).where(Member.id == ctx.member_id)) or {}
    days = int(settings.get("raw_retention_days", DEFAULT_RETENTION_DAYS))
    if days <= 0:
        return {}
    now = now or datetime.now(UTC)
    cutoff, long_cutoff = now - timedelta(days=days), now - timedelta(days=2 * days)
    # A statement that didn't reconcile is re-read hourly (collector.retry_stored), so it waits like a failure.
    unreconciled = (select(RawAttachment.raw_message_id).join(Statement, Statement.raw_attachment_id == RawAttachment.id)
                    .where(Statement.member_id == ctx.member_id, Statement.reconciled_at.is_(None)))
    waiting = RawMessage.parse_status.in_(WAITING) | RawMessage.id.in_(unreconciled)
    msgs = s.scalars(select(RawMessage).where(
        RawMessage.member_id == ctx.member_id, RawMessage.purged_at.is_(None),
        or_(RawMessage.parse_status.in_(DONE) & ~waiting & (RawMessage.received_at < cutoff),
            waiting & (RawMessage.received_at < long_cutoff)))).all()
    out: dict[str, Any] = {"messages": 0, "files": 0}
    for m in msgs:
        refs = [m.blob_ref]
        for a in s.scalars(select(RawAttachment).where(RawAttachment.member_id == ctx.member_id,
                                                       RawAttachment.raw_message_id == m.id, RawAttachment.purged_at.is_(None))):
            a.purged_at = now
            refs.append(a.blob_ref)
        m.purged_at = now
        s.flush()
        out["files"] += sum(_unlink(s, ctx.member_id, root, r) for r in dict.fromkeys(refs))
        out["messages"] += 1
    return out
