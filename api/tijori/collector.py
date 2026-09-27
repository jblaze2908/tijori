"""IMAP collector (PLAN §7.1): every 2 minutes, new mail in each member's label → raw store → parsers.

Read-only on the mailbox: EXAMINE and BODY.PEEK, so nothing is marked read or moved. A UID watermark
per mailbox (reset when UIDVALIDITY changes) makes each poll fetch only what is new. Every message is
stored raw first, so parsers can be re-run over history; each message is its own short transaction.

Per poll: one DNS + TLS + IMAP login per mailbox, one UID SEARCH, then FETCH in batches of 25. Per
message: a handful of member-scoped queries; a statement PDF adds qpdf + pdftotext (≈0.3 s).

Run: `python -m tijori.collector` (the compose `worker` service), or `--once` for one pass.
"""

import email
import hashlib
import imaplib
import logging
import re
import signal
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tijori.blobs import encrypt_existing, read_blob, store_blob
from tijori.db import MemberContext, make_engine, member_session
from tijori.imap_check import TIMEOUT_S, _public_only, normalize_password, quote_mailbox
from tijori.mailtext import body_text
from tijori.models import ComponentValue, Holding, MailSource, Price, RawAttachment, RawMessage, Statement
from tijori.parsers.cams_statement import CamsStatementParser
from tijori.parsers.cdsl_cas import CdslCasParser
from tijori.parsers import Message, ParseError, route
from tijori.parsers.alerts import AlertParser
from tijori.pdf import PdfError, is_pdf, pdf_to_text
from tijori.services import cards, loans, notify, prices, retention
from tijori.services import secrets as vault
from tijori.services.ingest import ingest_alert, ingest_statement, load_classifier
from tijori.settings import Settings, get_settings

log = logging.getLogger("tijori.collector")
POLL_S = 120
BATCH = 25
ACTOR = "collector"
ALERTS = AlertParser()
CAMS = CamsStatementParser()
CAS = CdslCasParser()
# Which vault password opens a sender's PDFs (the scheme each bank states in its mail).
SCHEMES: tuple[tuple[str, str], ...] = (
    ("hdfcbank", "hdfc"), ("hdfcbank", "hdfc_custid"), ("icici", "icici_card"), ("sbi", "sbi"), ("camsonline", "pan"), ("groww", "pan"),
    ("kfintech", "pan"), ("cdsl", "pan"),
)


@dataclass(frozen=True, slots=True)
class Box:
    id: int
    host: str
    port: int
    username: str
    label: str
    password: str
    uid_validity: int | None
    last_uid: int | None


def _members(engine: Engine) -> list[MemberContext]:
    with engine.connect() as c:
        return [MemberContext(r.member_id, r.household_id) for r in c.execute(text("SELECT * FROM collector_members()"))]


def _boxes(engine: Engine, ctx: MemberContext, settings: Settings) -> list[Box]:
    box = settings.secret_box()
    out = []
    with member_session(engine, ctx) as s:
        for m in s.scalars(select(MailSource).where(MailSource.member_id == ctx.member_id, MailSource.status == "ok")):
            pw = vault.get(s, ctx, box, vault.mail_source_name(m.id)) if box else None
            if pw:
                out.append(Box(m.id, m.host, m.port, m.username, m.label, pw, m.uid_validity, m.last_uid))
    return out


def _passwords(s: Session, ctx: MemberContext, settings: Settings, sender: str) -> list[str]:
    box = settings.secret_box()
    if box is None:
        return []
    names = [f"statement_password:scheme:{scheme}" for key, scheme in SCHEMES if key in sender]
    found = [p for n in names if (p := vault.get(s, ctx, box, n))]
    return found + vault.get_many(s, ctx, box, "statement_password:account:")


def _pdfs(msg: EmailMessage) -> list[tuple[str, bytes]]:
    out = []
    for part in msg.iter_attachments():
        name = part.get_filename() or ""
        data = part.get_payload(decode=True) or b""
        if name.lower().endswith(".pdf") or is_pdf(data):
            out.append((name, data))
    return out


def _received(msg: EmailMessage) -> datetime:
    try:
        return parsedate_to_datetime(msg["Date"]).astimezone(UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def process(s: Session, ctx: MemberContext, settings: Settings, source_id: int, uid: int, raw: bytes) -> str:
    """One message. Returns its parse_status. Idempotent: a message already stored is skipped."""
    sha = hashlib.sha256(raw).hexdigest()
    if s.scalar(select(RawMessage.id).where(RawMessage.member_id == ctx.member_id, RawMessage.sha256 == sha)):
        return "duplicate"
    msg: EmailMessage = email.message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]
    sender = parseaddr(msg.get("From", ""))[1].lower()
    subject = re.sub(r"\s+", " ", str(msg.get("Subject", "")))[:500]
    _, ref = store_blob(settings.blob_dir, ctx.member_id, raw, settings.secret_box())
    rm = RawMessage(member_id=ctx.member_id, received_at=_received(msg), message_id=(msg.get("Message-ID") or "")[:998] or None,
                    sender=sender, subject=subject, sha256=sha, blob_ref=ref, parse_status="pending",
                    mail_source_id=source_id, mail_uid=uid)
    s.add(rm)
    s.flush()
    atts = []
    for name, data in _pdfs(msg):
        digest, bref = store_blob(settings.blob_dir, ctx.member_id, data, settings.secret_box())
        att = RawAttachment(member_id=ctx.member_id, raw_message_id=rm.id, filename=name[:300], sha256=digest, blob_ref=bref)
        s.add(att)
        atts.append((att, data))
    s.flush()
    rm.parse_status = _route(s, ctx, settings, rm, msg, atts)
    return rm.parse_status


def _route(s: Session, ctx: MemberContext, settings: Settings, rm: RawMessage, msg: EmailMessage,
           atts: list[tuple[RawAttachment, bytes]]) -> str:
    status = "ignored"
    m = Message(text=body_text(msg), sender=rm.sender, subject=rm.subject)
    if ALERTS.match(m):
        clf = load_classifier(s, ctx)
        for obs in ALERTS.parse(m):
            ingest_alert(s, ctx, clf, obs, rm, ALERTS.name, ALERTS.version)
        cards.link_card_payments(s, ctx.member_id)
        status = "parsed"
    for att, data in atts:
        status = _statement(s, ctx, settings, rm.sender or "", rm, att, data) or status
    return status


# PDFs in the label that carry no transactions of their own (holdings come from the monthly CAS).
NOT_TRANSACTIONS = re.compile(r"Contract Note|Statement of Accounts? (?:for|of) (?:Securities|Funds)|Portfolio Disclosure|Ledger|Purchase Confirmation|"
                              r"Processing of (Additional )?Purchase|Year End Statement|Fixed Deposit Advice|Amazon ?Pay|"
                              r"Transaction Confirmation|Transaction request is processed|Holding Statement|"
                              r"Quarterly settlement|Refund initiated|Add Money|Margin Statement|AGTS Report|Welcome to", re.I)
DAY_S = 86400
_last_purge: dict[int, float] = {}
RETRY_S = 3600
RETRY = ("failed", "parser_needed", "needs_password")
_last_retry: dict[int, float] = {}


def retry_stored(engine: Engine, ctx: MemberContext, settings: Settings) -> dict[str, int]:
    """Hourly: re-route stored messages a parser or password was missing for, and statements that didn't
    reconcile (ingest is idempotent, so a fixed parser adds only the missing lines). Reads blobs, no IMAP."""
    if time.monotonic() - _last_retry.get(ctx.member_id, -RETRY_S) < RETRY_S:
        return {}
    _last_retry[ctx.member_id] = time.monotonic()
    with member_session(engine, ctx) as s:
        unreconciled = (select(RawAttachment.raw_message_id).join(Statement, Statement.raw_attachment_id == RawAttachment.id)
                        .where(Statement.member_id == ctx.member_id, Statement.reconciled_at.is_(None)))
        ids = list(s.scalars(select(RawMessage.id).where(
            RawMessage.member_id == ctx.member_id, RawMessage.mail_source_id.is_not(None), RawMessage.purged_at.is_(None),
            RawMessage.parse_status.in_(RETRY) | RawMessage.id.in_(unreconciled))))
    counts: dict[str, int] = {}
    for rid in ids:
        with member_session(engine, ctx) as s:
            try:
                rm = s.get(RawMessage, rid)
                box = settings.secret_box()
                msg = email.message_from_bytes(read_blob(settings.blob_dir, ctx.member_id, rm.blob_ref, box), policy=policy.default)
                atts = [(a, read_blob(settings.blob_dir, ctx.member_id, a.blob_ref, box)) for a in s.scalars(
                    select(RawAttachment).where(RawAttachment.member_id == ctx.member_id, RawAttachment.raw_message_id == rid))]
                st = _route(s, ctx, settings, rm, msg, atts)  # type: ignore[arg-type]
                if st != rm.parse_status:
                    rm.parse_status = st
                s.commit()
            except Exception:
                s.rollback()
                log.exception("retry failed: raw_message=%s", rid)
                st = "failed"
        counts[st] = counts.get(st, 0) + 1
    return counts


def _statement(s: Session, ctx: MemberContext, settings: Settings, sender: str, rm: RawMessage, att: RawAttachment,
               data: bytes) -> str:
    text_ = None
    for pw in [None, *_passwords(s, ctx, settings, sender)]:
        try:
            text_ = pdf_to_text(data, pw)
            break
        except PdfError:
            continue
    if text_ is None:
        return "needs_password"
    if CAS.match(Message(text=text_)):
        cas = CAS.parse(Message(text=text_))
        _components(s, ctx, cas.components, cas.as_of)
        return _holdings(s, ctx, cas.lines, "cdsl_cas")
    if CAMS.match(Message(text=text_)):
        return _holdings(s, ctx, CAMS.parse_holdings(Message(text=text_)))
    parser = route(Message(text=text_, sender=sender, filename=att.filename))
    if parser is None:
        return "ignored" if NOT_TRANSACTIONS.search(f"{rm.subject or ''} {att.filename or ''}") else "parser_needed"
    try:
        stmts = parser.parse_statements(Message(text=text_)) if hasattr(parser, "parse_statements") \
            else [parser.parse_statement(Message(text=text_))]  # type: ignore[attr-defined]
        for st in stmts:
            with s.begin_nested():  # one bad account section doesn't lose the others
                ingest_statement(s, ctx, ACTOR, st, filename=att.filename, sha256=att.sha256, blob_ref=att.blob_ref,
                                 raw=(rm, att))
    except (ParseError, ValueError) as exc:
        log.warning("statement parse failed: raw_message=%s parser=%s (%s)", rm.id, parser.name, type(exc).__name__)
        return "failed"
    return "parsed"


def _store_failed(engine: Engine, ctx: MemberContext, settings: Settings, source_id: int, uid: int, raw: bytes) -> None:
    """Keep the raw message as `failed` so it shows in the parse queue and can be re-run; the watermark
    moves on either way, so one bad message can't hold the mailbox back."""
    sha = hashlib.sha256(raw).hexdigest()
    with member_session(engine, ctx) as s:
        if s.scalar(select(RawMessage.id).where(RawMessage.member_id == ctx.member_id, RawMessage.sha256 == sha)):
            return
        msg = email.message_from_bytes(raw, policy=policy.default)
        _, ref = store_blob(settings.blob_dir, ctx.member_id, raw, settings.secret_box())
        s.add(RawMessage(member_id=ctx.member_id, received_at=_received(msg), sender=parseaddr(msg.get("From", ""))[1].lower(),
                         subject=re.sub(r"\s+", " ", str(msg.get("Subject", "")))[:500], sha256=sha, blob_ref=ref,
                         parse_status="failed", mail_source_id=source_id, mail_uid=uid))
        s.commit()


def _components(s: Session, ctx: MemberContext, values: tuple[tuple[str, Any], ...], as_of: Any) -> None:
    for key, amount in values:
        cv = pg_insert(ComponentValue).values(member_id=ctx.member_id, key=key, amount=amount, as_of=as_of, source="statement")
        s.execute(cv.on_conflict_do_update(constraint="uq_component_value_member_id_key_as_of", set_={"amount": cv.excluded.amount}))


def _holdings(s: Session, ctx: MemberContext, lines: list[Any], source: str = "cams") -> str:
    """Units per scheme as of the statement's NAV date, and that NAV as a price point. Idempotent."""
    for h in lines:
        q = select(Holding).where(Holding.member_id == ctx.member_id, Holding.as_of == h.as_of, Holding.source == source)
        seen = s.scalars(q.where(Holding.isin == h.isin) if h.isin else q.where(Holding.name == h.name)).first()
        if seen is None:
            s.add(Holding(member_id=ctx.member_id, isin=h.isin, name=h.name, units=h.units, as_of=h.as_of, source=source))
        else:  # a re-read statement (a fixed parser) corrects the row in place
            seen.units, seen.name = h.units, h.name
        if h.isin:
            s.execute(pg_insert(Price).values(isin_or_symbol=h.isin, date=h.as_of, close=h.nav, source=source[:16])
                      .on_conflict_do_nothing())
    return "parsed"


def _set_poll(engine: Engine, ctx: MemberContext, source_id: int, **values: Any) -> None:
    with member_session(engine, ctx) as s:
        s.execute(update(MailSource).where(MailSource.id == source_id, MailSource.member_id == ctx.member_id)
                  .values(last_poll_at=datetime.now(UTC), **values))
        s.commit()


def poll_box(engine: Engine, ctx: MemberContext, settings: Settings, b: Box) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        _public_only(b.host, b.port)
        conn = imaplib.IMAP4_SSL(b.host, b.port, ssl_context=ssl.create_default_context(), timeout=TIMEOUT_S * 6)
    except (OSError, PermissionError):
        _set_poll(engine, ctx, b.id, last_poll_error="connect_failed")
        return counts
    try:
        conn.login(b.username, normalize_password(b.host, b.password))
        typ, _ = conn.select(quote_mailbox(b.label), readonly=True)
        if typ != "OK":
            _set_poll(engine, ctx, b.id, last_poll_error="mailbox_not_found")
            return counts
        validity = int(conn.response("UIDVALIDITY")[1][0] or 0)
        since = (b.last_uid or 0) if validity == b.uid_validity else 0
        typ, data = conn.uid("SEARCH", None, f"UID {since + 1}:*")
        uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > since)
        for i in range(0, len(uids), BATCH):
            chunk = uids[i:i + BATCH]
            typ, resp = conn.uid("FETCH", ",".join(map(str, chunk)), "(UID BODY.PEEK[])")
            for part in resp:
                if not isinstance(part, tuple):
                    continue
                uid = int(re.search(rb"UID (\d+)", part[0]).group(1))  # type: ignore[union-attr]
                with member_session(engine, ctx) as s:
                    try:
                        st = process(s, ctx, settings, b.id, uid, part[1])
                        s.commit()
                    except Exception:  # one bad message never stops the mailbox
                        s.rollback()
                        log.exception("message failed: source=%s uid=%s", b.id, uid)
                        st = "failed"
                if st == "failed":
                    _store_failed(engine, ctx, settings, b.id, uid, part[1])
                counts[st] = counts.get(st, 0) + 1
            _set_poll(engine, ctx, b.id, uid_validity=validity, last_uid=chunk[-1], last_poll_error=None)
        if not uids:
            _set_poll(engine, ctx, b.id, uid_validity=validity, last_poll_error=None)
    except imaplib.IMAP4.error:
        _set_poll(engine, ctx, b.id, last_poll_error="imap_error")
    finally:
        try:
            conn.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
    return counts


def run_once(engine: Engine, settings: Settings) -> None:
    try:
        prices.refresh_navs(engine, [c for c in _members(engine)])
    except Exception:  # prices are a nicety; a failed fetch never blocks mail
        log.exception("NAV refresh failed")
    for ctx in _members(engine):
        for b in _boxes(engine, ctx, settings):
            counts = poll_box(engine, ctx, settings, b)
            if counts:
                log.info("member=%s source=%s %s", ctx.member_id, b.id, counts)
        again = retry_stored(engine, ctx, settings)
        if again:
            log.info("member=%s retried %s", ctx.member_id, again)
        with member_session(engine, ctx) as s:
            if n := loans.auto_attach(s, ctx.member_id):
                log.info("member=%s attached %d txns to loans", ctx.member_id, n)
            s.commit()
        if time.monotonic() - _last_purge.get(ctx.member_id, -DAY_S) >= DAY_S:
            _last_purge[ctx.member_id] = time.monotonic()
            with member_session(engine, ctx) as s:
                gone = retention.purge(s, ctx, settings.blob_dir)
                s.commit()
            if gone:
                log.info("member=%s retention %s", ctx.member_id, gone)
    try:
        notify.run(engine, _members(engine), settings)
    except Exception:  # notifications never block collection
        log.exception("notify failed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    engine = make_engine(settings.database_url.get_secret_value())
    stop = False

    def _stop(*_: Any) -> None:
        nonlocal stop
        stop = True

    box = settings.secret_box()
    if box is not None:  # files written before encryption at rest existed are sealed once, in place
        for ctx in _members(engine):
            sealed = encrypt_existing(settings.blob_dir, ctx.member_id, box)
            if sealed:
                log.info("member=%s sealed %s stored files", ctx.member_id, sealed)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stop:
        started = time.monotonic()
        try:
            run_once(engine, settings)
        except Exception:
            log.exception("poll failed")
        if "--once" in sys.argv:
            break
        while not stop and time.monotonic() - started < POLL_S:
            time.sleep(1)


if __name__ == "__main__":
    main()
