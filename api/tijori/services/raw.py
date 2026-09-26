"""The raw sources behind a txn: the email it came in (as plain text, never markup) or the statement
file. Files are served as downloads only, from the member's own blob directory."""

import email
from email import policy
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tijori.mailtext import body_text
from tijori.models import Observation, RawAttachment, RawMessage, TxnObservation
from tijori.services.errors import NotFound

MAX_TEXT = 20_000


def _blob(root: Path, ref: str) -> bytes:
    path = (root / ref).resolve()
    if root.resolve() not in path.parents:  # refs are ours, but never follow one outside the store
        raise NotFound("file not found")
    return path.read_bytes()


def sources_of(s: Session, member_id: int, root: Path, txn_id: int) -> list[dict[str, Any]]:
    rows = s.execute(
        select(RawMessage).join(Observation, Observation.raw_message_id == RawMessage.id)
        .join(TxnObservation, TxnObservation.observation_id == Observation.id)
        .where(TxnObservation.txn_id == txn_id, RawMessage.member_id == member_id).distinct()
    ).scalars().all()
    out = []
    for m in rows:
        files = s.execute(select(RawAttachment.id, RawAttachment.filename).where(
            RawAttachment.member_id == member_id, RawAttachment.raw_message_id == m.id)).all()
        text = None
        if m.sender != "upload":
            try:
                msg = email.message_from_bytes(_blob(root, m.blob_ref), policy=policy.default)
                text = body_text(msg)[:MAX_TEXT]  # type: ignore[arg-type]
            except (OSError, NotFound):
                text = None
        out.append({"raw_message_id": m.id, "kind": "file" if m.sender == "upload" else "email", "sender": m.sender,
                    "subject": m.subject, "received_at": m.received_at, "text": text,
                    "files": [{"id": f.id, "filename": f.filename} for f in files]})
    return out


def attachment(s: Session, member_id: int, root: Path, attachment_id: int) -> tuple[str, bytes]:
    a = s.scalars(select(RawAttachment).where(RawAttachment.member_id == member_id, RawAttachment.id == attachment_id)).first()
    if a is None:
        raise NotFound("file not found")
    return a.filename or f"statement-{a.id}.pdf", _blob(root, a.blob_ref)
