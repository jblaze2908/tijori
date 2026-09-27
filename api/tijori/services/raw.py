"""The raw sources behind a txn: the email it came in (as plain text, never markup) or the statement
file. Files are served as downloads only, from the member's own blob directory."""

import email
from email import policy
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tijori.blobs import read_blob
from tijori.mailtext import body_text
from tijori.secretbox import SecretBox, SecretError
from tijori.models import Observation, RawAttachment, RawMessage, TxnObservation
from tijori.services.errors import NotFound

MAX_TEXT = 20_000


def _blob(root: Path, member_id: int, ref: str, box: SecretBox | None) -> bytes:
    try:
        return read_blob(root, member_id, ref, box)
    except (FileNotFoundError, SecretError):
        raise NotFound("file not found") from None


def sources_of(s: Session, member_id: int, root: Path, txn_id: int, box: SecretBox | None = None) -> list[dict[str, Any]]:
    rows = s.execute(
        select(RawMessage).join(Observation, Observation.raw_message_id == RawMessage.id)
        .join(TxnObservation, TxnObservation.observation_id == Observation.id)
        .where(TxnObservation.txn_id == txn_id, RawMessage.member_id == member_id).distinct()
    ).scalars().all()
    out = []
    for m in rows:
        files = s.execute(select(RawAttachment.id, RawAttachment.filename).where(
            RawAttachment.member_id == member_id, RawAttachment.raw_message_id == m.id,
            RawAttachment.purged_at.is_(None))).all()
        text = None
        if m.sender != "upload" and m.purged_at is None:
            try:
                msg = email.message_from_bytes(_blob(root, member_id, m.blob_ref, box), policy=policy.default)
                text = body_text(msg)[:MAX_TEXT]  # type: ignore[arg-type]
            except (OSError, NotFound):
                text = None
        out.append({"raw_message_id": m.id, "kind": "file" if m.sender == "upload" else "email", "sender": m.sender,
                    "subject": m.subject, "received_at": m.received_at, "text": text, "purged": m.purged_at is not None,
                    "files": [{"id": f.id, "filename": f.filename} for f in files]})
    return out


def attachment(s: Session, member_id: int, root: Path, attachment_id: int, box: SecretBox | None = None) -> tuple[str, bytes]:
    a = s.scalars(select(RawAttachment).where(RawAttachment.member_id == member_id, RawAttachment.id == attachment_id)).first()
    if a is None or a.purged_at is not None:
        raise NotFound("file not found (removed under your retention setting)")
    return a.filename or f"statement-{a.id}.pdf", _blob(root, member_id, a.blob_ref, box)
