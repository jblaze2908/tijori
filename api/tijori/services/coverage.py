"""Coverage: for an account and a day, can Tijori say every transaction is in, or is a feed behind?

- `confirmed`: a reconciled statement covers the day. A statement with no lines counts (services/ingest).
- `live`: a rule reads this account's alerts, one has already come in through a mailbox, and that mailbox was
  read to the end at least ALERT_LAG after the day closed (IST). Alerts would have arrived; that isn't proof,
  since not every kind of transaction sends one.
- `unknown`: anything else, with the reason.

Computed on request: six queries for `coverage()` whatever the range or number of accounts, four for `feeds()`.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tijori.models import Account, MailSource, Observation, RawMessage, Statement, Txn
from tijori.parsers.alerts import COVERED, SENDER_INSTITUTION, AlertParser
from tijori.services.common import IST, account_ref

STALE = timedelta(minutes=30)  # 15 missed 2-minute polls; a blip shorter than that heals on its own
ALERT_LAG = timedelta(hours=1)  # a bank mails the alert minutes after the txn, so a late-night one lands after midnight
STATEMENT_EVERY = timedelta(days=31)
MAX_DAYS = 62
FEED_KINDS = ("bank", "card")  # the kinds statements and alerts exist for
UNREAD = ("parser_needed", "failed")


@dataclass(frozen=True, slots=True)
class Feed:
    last_seen_at: datetime | None  # when the newest alert for the account arrived
    mail_source_id: int | None  # the mailbox it came through
    coverage_pct: float | None
    covered_through: date | None  # end of the newest reconciled statement
    last_statement_end: date | None  # reconciled or not


def _ist(at: datetime) -> str:
    return at.astimezone(IST).strftime("%d %b %H:%M IST")


def mailboxes(s: Session, member_id: int, now: datetime | None = None) -> list[dict[str, Any]]:
    """Each mailbox and, when it isn't being read, why. One query."""
    now = now or datetime.now(UTC)
    out = []
    for m in s.scalars(select(MailSource).where(MailSource.member_id == member_id).order_by(MailSource.id)):
        err = f" (last error: {m.last_poll_error})" if m.last_poll_error else ""
        if m.status != "ok":
            problem = f"skipped by the collector: status is {m.status}; test the mailbox to resume"
        elif m.last_ok_poll_at is None:
            problem = f"never read to the end{err}"
        elif now - m.last_ok_poll_at > STALE:
            problem = f"not read since {_ist(m.last_ok_poll_at)}{err}"
        else:
            problem = None
        out.append({"id": m.id, "label": m.label, "email": m.username, "status": m.status, "collecting": m.status == "ok",
                    "last_poll_at": m.last_poll_at, "last_ok_poll_at": m.last_ok_poll_at,
                    "last_poll_error": m.last_poll_error, "last_tested_at": m.last_tested_at,
                    "healthy": problem is None, "problem": problem})
    return out


def feeds(s: Session, member_id: int) -> dict[int, Feed]:
    """Per account: the newest alert and its mailbox, the share of statement lines also seen as alerts, and
    how far reconciled statements reach. Three grouped queries."""
    alerts = (select(Observation.account_id, RawMessage.received_at, RawMessage.mail_source_id,
                     func.min(Observation.occurred_at).over(partition_by=Observation.account_id).label("first_day"),
                     func.row_number().over(partition_by=Observation.account_id,
                                            order_by=(RawMessage.received_at.desc(), RawMessage.id.desc())).label("rn"))
              .join(RawMessage, RawMessage.id == Observation.raw_message_id)
              .where(Observation.member_id == member_id, Observation.parser == AlertParser.name,
                     Observation.account_id.is_not(None))
              .subquery())
    latest = {r.account_id: r for r in s.execute(select(alerts).where(alerts.c.rn == 1))}
    # Only lines dated after the account's first alert: before it, nothing could have been seen live.
    first = (select(Observation.account_id, func.min(Observation.occurred_at).label("day"))
             .where(Observation.member_id == member_id, Observation.parser == AlertParser.name)
             .group_by(Observation.account_id).subquery())
    shares = {r.account_id: (r.n, r.seen) for r in s.execute(
        select(Txn.account_id, func.count().label("n"), func.count().filter(Txn.sources.contains(["alert"])).label("seen"))
        .join(first, first.c.account_id == Txn.account_id)
        .where(Txn.member_id == member_id, Txn.split_of.is_(None), Txn.sources.contains(["statement"]),
               Txn.occurred_at >= first.c.day)
        .group_by(Txn.account_id))}
    stmts = {r.account_id: r for r in s.execute(
        select(Statement.account_id, func.max(Statement.period_end).label("any_end"),
               func.max(Statement.period_end).filter(Statement.reconciled_at.is_not(None)).label("ok_end"))
        .where(Statement.member_id == member_id).group_by(Statement.account_id))}
    out = {}
    for aid in latest.keys() | shares.keys() | stmts.keys():
        a, (n, seen), st = latest.get(aid), shares.get(aid, (0, 0)), stmts.get(aid)
        out[aid] = Feed(a.received_at if a else None, a.mail_source_id if a else None,
                        round(100 * seen / n, 1) if n else None,
                        st.ok_end if st else None, st.any_end if st else None)
    return out


def live_through(institution: str, kind: str, f: Feed, boxes: dict[int, dict[str, Any]]) -> datetime | None:
    """How far alerts for the account have been read: its alert mailbox's last full read, while it is collected."""
    box = boxes.get(f.mail_source_id) if f.mail_source_id is not None else None
    return box["last_ok_poll_at"] if (institution, kind) in COVERED and box and box["collecting"] else None


def _span(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]


def coverage(s: Session, member_id: int, date_from: date, date_to: date, now: datetime | None = None) -> dict[str, Any]:
    """Per bank or card account, per day in [date_from, date_to]: state, reason and txn count, plus mailbox
    health. The route caps the range at MAX_DAYS."""
    now = now or datetime.now(UTC)
    today = now.astimezone(IST).date()
    boxes = {m["id"]: m for m in mailboxes(s, member_id, now)}
    feed = feeds(s, member_id)
    accounts = s.execute(select(Account.id, Account.institution, Account.name, Account.kind, Account.mask)
                         .where(Account.member_id == member_id, Account.kind.in_(FEED_KINDS))
                         .order_by(Account.institution, Account.id)).all()
    periods: dict[int, list[Any]] = defaultdict(list)
    for p in s.execute(select(Statement.account_id, Statement.period_start, Statement.period_end, Statement.reconciled_at)
                       .where(Statement.member_id == member_id, Statement.period_end >= date_from,
                              Statement.period_start <= date_to)):
        periods[p.account_id].append(p)
    counts = {(r.account_id, r.occurred_at): r.n for r in s.execute(
        select(Txn.account_id, Txn.occurred_at, func.count().label("n"))
        .where(Txn.member_id == member_id, Txn.split_of.is_(None), Txn.occurred_at.between(date_from, date_to))
        .group_by(Txn.account_id, Txn.occurred_at))}
    ist_day = func.date(func.timezone("Asia/Kolkata", RawMessage.received_at))
    unread: dict[tuple[str, date], int] = defaultdict(int)
    for r in s.execute(select(RawMessage.sender, ist_day.label("day"), func.count().label("n"))
                       .where(RawMessage.member_id == member_id, RawMessage.sender.in_(list(SENDER_INSTITUTION)),
                              RawMessage.parse_status.in_(UNREAD),
                              RawMessage.received_at >= datetime.combine(date_from, time(), IST),
                              RawMessage.received_at < datetime.combine(date_to + timedelta(days=1), time(), IST))
                       .group_by(RawMessage.sender, ist_day)):
        unread[(SENDER_INSTITUTION[r.sender], r.day)] += r.n

    out = []
    for a in accounts:
        f = feed.get(a.id) or Feed(None, None, None, None, None)
        alerted = (a.institution, a.kind) in COVERED
        box = boxes.get(f.mail_source_id) if f.mail_source_id is not None else None
        through = live_through(a.institution, a.kind, f, boxes)
        if f.last_statement_end:
            later = f"statements reach {f.last_statement_end}; the next is due after ~{f.last_statement_end + STATEMENT_EVERY}"
        else:
            later = "no statement yet"
        if not alerted:
            no_live = f"no alerts are read for {a.institution} {a.kind} accounts; {later}"
        elif f.last_seen_at is None:
            no_live = f"no alert from this account has reached Tijori; check its alerts go to the mailbox label; {later}"
        elif box is None:
            no_live = f"the mailbox its alerts came through is gone; {later}"
        elif not box["collecting"]:
            no_live = f"mailbox {box['label']} {box['problem']}"
        else:
            no_live = None
        days = []
        for d in _span(date_from, date_to):
            inside = [p for p in periods[a.id] if p.period_start <= d <= p.period_end]
            state, reason = "unknown", None
            if any(p.reconciled_at is not None for p in inside):
                state = "confirmed"
            elif d > today:
                reason = "in the future"
            elif no_live:
                reason = no_live
            elif through is None or datetime.combine(d + timedelta(days=1), time(), IST) + ALERT_LAG > through:
                assert box is not None  # no_live covers a missing mailbox
                reason = (f"alerts for this day may still be arriving; mail read to {_ist(through)}"
                          if box["healthy"] and through else f"mailbox {box['label']} {box['problem']}")
            elif n := unread.get((a.institution, d), 0):
                reason = f"{n} alert email(s) from {a.institution} that day couldn't be read (statement queue)"
            else:
                state = "live"
            if state == "unknown" and inside:
                reason = f"{reason}; its statement {inside[0].period_start}–{inside[0].period_end} didn't reconcile"
            days.append({"date": d, "state": state, "reason": reason, "txn_count": counts.get((a.id, d), 0)})
        out.append({"account": account_ref(a.id, a.institution, a.name, a.kind, a.mask), "alerts": alerted,
                    "covered_through": f.covered_through, "last_seen_at": f.last_seen_at, "live_through": through,
                    "coverage_pct": f.coverage_pct, "days": days})
    return {"from": date_from, "to": date_to, "timezone": "Asia/Kolkata", "now": now,
            "mailboxes": list(boxes.values()), "accounts": out}


def mailbox_alerts(s: Session, member_id: int) -> list[dict[str, Any]]:
    """A `mailbox_stale` alert per mailbox the collector skips or hasn't read in STALE. The id holds the last good
    read, so an outage pushes once."""
    return [{"id": f"mailbox:{m['id']}:{m['status']}:{m['last_ok_poll_at'].isoformat() if m['last_ok_poll_at'] else 'never'}",
             "kind": "mailbox_stale", "severity": "bad", "title": f"Mailbox {m['label']} isn't being read",
             "detail": f"{m['email']} · {m['problem']}", "txn_ids": []}
            for m in mailboxes(s, member_id) if m["problem"]]
