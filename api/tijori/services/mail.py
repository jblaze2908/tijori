"""IMAP mail sources: one mailbox label per source, its app password sealed by SecretBox."""

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.imap_check import PRESETS, ImapResult, check
from tijori.models import MailSource
from tijori.secretbox import SecretBox
from tijori.services import secrets as vault
from tijori.services.common import audit
from tijori.services.errors import Invalid, NotFound

_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
# Printable ASCII only: IMAP needs modified UTF-7 for anything else, and CR/LF would inject commands.
_LABEL = re.compile(r"^[\x20-\x7e]{1,100}$")
_PASSWORD = re.compile(r"^[^\x00\r\n]{1,256}$")


def _out(m: MailSource) -> dict[str, Any]:
    return {"id": m.id, "provider": m.provider, "host": m.host, "port": m.port, "email": m.username,
            "label": m.label, "status": m.status, "last_tested_at": m.last_tested_at,
            "last_error_code": m.last_error_code, "last_message_count": m.last_message_count,
            "last_poll_at": m.last_poll_at, "last_poll_error": m.last_poll_error, "last_ok_poll_at": m.last_ok_poll_at,
            "collecting": m.status == "ok", "created_at": m.created_at}


def _endpoint(provider: str, host: str | None, port: int | None) -> tuple[str, int]:
    if provider in PRESETS:
        return PRESETS[provider]
    if not host or not _HOST.match(host.lower()):
        raise Invalid("custom sources need a valid hostname (IP literals are not accepted)")
    port = port or 993
    if port != 993 and not 1024 <= port <= 65535:
        raise Invalid("port must be 993 or 1024-65535 (IMAP over TLS)")
    return host.lower(), port


def _check_inputs(email: str | None, password: str | None, label: str | None) -> None:
    if email is not None and not _EMAIL.match(email):
        raise Invalid("email is not valid")
    if password is not None and not _PASSWORD.match(password):
        raise Invalid("app password must be 1-256 characters without line breaks")
    if label is not None and not _LABEL.match(label):
        raise Invalid("label must be 1-100 printable ASCII characters")


def _get(s: Session, ctx: MemberContext, source_id: int) -> MailSource:
    m = s.scalars(select(MailSource).where(MailSource.member_id == ctx.member_id, MailSource.id == source_id)).first()
    if m is None:
        raise NotFound("mail source not found")
    return m


def list_sources(s: Session, ctx: MemberContext) -> dict[str, Any]:
    rows = s.scalars(select(MailSource).where(MailSource.member_id == ctx.member_id).order_by(MailSource.id)).all()
    return {"items": [_out(m) for m in rows]}


def create(s: Session, ctx: MemberContext, actor: str, box: SecretBox, *, provider: str, host: str | None,
           port: int | None, email: str, app_password: str, label: str) -> dict[str, Any]:
    _check_inputs(email, app_password, label)
    host, port = _endpoint(provider, host, port)
    m = MailSource(member_id=ctx.member_id, provider=provider, host=host, port=port, username=email.lower(),
                   label=label)
    s.add(m)
    s.flush()
    vault.put(s, ctx, box, vault.mail_source_name(m.id), app_password)
    audit(s, ctx, actor, "mail_source.create", f"mail_source:{m.id}", {"provider": provider, "host": host})
    return _out(m)


def update_source(s: Session, ctx: MemberContext, actor: str, box: SecretBox | None, source_id: int, *,
                  app_password: str | None, label: str | None) -> dict[str, Any]:
    _check_inputs(None, app_password, label)
    m = _get(s, ctx, source_id)
    if label is not None:
        m.label = label
    if app_password is not None:
        vault.put(s, ctx, vault.require(box), vault.mail_source_name(m.id), app_password)
    if label is not None or app_password is not None:
        m.status, m.last_error_code = "untested", None
    audit(s, ctx, actor, "mail_source.update", f"mail_source:{m.id}",
          {"label": label is not None, "password_rotated": app_password is not None})
    s.flush()
    return _out(m)


def delete_source(s: Session, ctx: MemberContext, actor: str, source_id: int) -> None:
    m = _get(s, ctx, source_id)
    vault.remove(s, ctx, vault.mail_source_name(m.id))
    s.delete(m)
    audit(s, ctx, actor, "mail_source.delete", f"mail_source:{source_id}", {})


def load_for_test(s: Session, ctx: MemberContext, box: SecretBox, source_id: int) -> tuple[MailSource, str]:
    m = _get(s, ctx, source_id)
    password = vault.get(s, ctx, box, vault.mail_source_name(m.id))
    if password is None:
        raise Invalid("this source has no app password; set one with PATCH")
    return m, password


def run_test(host: str, port: int, username: str, password: str, label: str) -> ImapResult:
    """Network I/O (DNS + TLS + IMAP, 10 s timeout): call outside any DB transaction."""
    return check(host, port, username, password, label)


def record_test(s: Session, ctx: MemberContext, actor: str, source_id: int, result: ImapResult) -> dict[str, Any]:
    m = _get(s, ctx, source_id)
    m.status = "ok" if result.ok else "error"
    m.last_tested_at = datetime.now(UTC)
    if result.ok:
        m.last_message_count = result.message_count
    m.last_error_code = result.error_code
    audit(s, ctx, actor, "mail_source.test", f"mail_source:{m.id}", {"ok": result.ok, "error": result.error_code})
    return {"ok": result.ok, "message_count": result.message_count, "error_code": result.error_code}


def check_inputs(provider: str, host: str | None, port: int | None, email: str, app_password: str,
                 label: str) -> tuple[str, int]:
    """Validate credentials for a test-before-save; returns the endpoint to connect to."""
    _check_inputs(email, app_password, label)
    return _endpoint(provider, host, port)
