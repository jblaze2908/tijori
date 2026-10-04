"""How current the member's data is, in one compact block: per account the newest txn and how far its feeds
reach (services/coverage), per mailbox whether it is being read. Every value is read from the tables."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tijori.models import Account, Txn
from tijori.services import coverage
from tijori.services.common import account_label


def freshness(s: Session, member_id: int, now: datetime | None = None) -> dict[str, Any]:
    """Five queries whatever the account count (one here, four in coverage.feeds/mailboxes). MCP runs this after
    every ledger read (app/mcp.py FRESH), so it must never become a query per account."""
    now = now or datetime.now(UTC)
    txns = (select(Txn.account_id, func.max(Txn.occurred_at).label("last_txn_at"),
                   func.max(Txn.created_at).label("last_recorded_at"))
            .where(Txn.member_id == member_id).group_by(Txn.account_id).subquery())
    rows = s.execute(select(Account, txns.c.last_txn_at, txns.c.last_recorded_at)
                     .outerjoin(txns, txns.c.account_id == Account.id)
                     .where(Account.member_id == member_id).order_by(Account.institution, Account.id)).all()
    feed = coverage.feeds(s, member_id)
    boxes = coverage.mailboxes(s, member_id, now)
    by_id = {m["id"]: m for m in boxes}
    none = coverage.Feed(None, None, None, None, None)
    accounts = []
    for a, txn_at, recorded in rows:
        f = feed.get(a.id, none)
        accounts.append({"id": a.id, "label": account_label(a.institution, a.name, a.mask), "kind": a.kind,
                         "last_txn_at": txn_at, "last_recorded_at": recorded, "last_seen_at": f.last_seen_at,
                         "covered_through": f.covered_through, "last_statement_end": f.last_statement_end,
                         "live_through": coverage.live_through(a.institution, a.kind, f, by_id)})
    return {"generated_at": now, "accounts": accounts,
            "mailboxes": [{"id": m["id"], "label": m["label"], "last_ok_poll_at": m["last_ok_poll_at"],
                           "healthy": m["healthy"], "problem": m["problem"]} for m in boxes]}
