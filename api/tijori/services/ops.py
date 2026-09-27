"""What host jobs report back (ops_event), such as each nightly off-site backup. One query per call."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tijori.models import OpsEvent

BACKUP_STALE = timedelta(days=2)


def backup_status(s: Session) -> dict[str, Any]:
    rows = s.execute(select(OpsEvent.ok, OpsEvent.at, OpsEvent.detail).where(OpsEvent.kind == "backup")
                     .order_by(OpsEvent.at.desc()).limit(30)).all()
    last = rows[0] if rows else None
    ok = next((r for r in rows if r.ok), None)
    return {"configured": bool(rows), "last_run_at": last.at if last else None, "last_ok": bool(last and last.ok),
            "last_detail": last.detail if last else None, "last_ok_at": ok.at if ok else None,
            "stale": bool(rows) and (ok is None or datetime.now(UTC) - ok.at > BACKUP_STALE)}
