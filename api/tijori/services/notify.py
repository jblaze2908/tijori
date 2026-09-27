"""Push notifications over self-hosted ntfy, and the Monday digest. Figures only, no commentary.

Each alert id is pushed once: sent ids are kept as `alert` rows (kind "push"). The first run for a
member records what is already open without sending it, so turning notifications on never floods.
Per worker poll: one alerts computation per member with notifications on, plus one HTTPS POST per new
alert; the digest adds a few grouped queries once a week.
"""

import json
import logging
import urllib.request
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from tijori.db import MemberContext, member_session
from tijori.models import Alert, Category, Member, Txn
from tijori.services import budgets, recurring
from tijori.services.alerts import month_alerts
from tijori.services.common import month_start_day, today_ist
from tijori.services.reports import expense_amount, is_expense
from tijori.settings import Settings

log = logging.getLogger("tijori.notify")
TIMEOUT_S = 10


PRIORITY = {"low": 2, "default": 3, "high": 4}


def send(settings: Settings, topic: str, title: str, body: str, priority: str = "default", tags: str = "") -> bool:
    """One HTTPS POST. JSON publish, since ntfy header values can't carry "₹" or "·"."""
    if not settings.ntfy_url:
        return False
    msg = {"topic": topic, "title": title, "message": body, "priority": PRIORITY[priority]}
    if tags:
        msg["tags"] = [tags]
    headers = {"Content-Type": "application/json"}
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token.get_secret_value()}"
    req = urllib.request.Request(settings.ntfy_url.rstrip("/") + "/", data=json.dumps(msg).encode(), headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S):  # noqa: S310 (operator-configured ntfy URL)
            return True
    except OSError:
        log.warning("ntfy post failed")
        return False


def _cycle(today: date, msd: int) -> str:
    y, m = today.year, today.month
    if today.day < msd:
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{y}-{m:02d}"


def _inr(v: Any) -> str:
    return f"₹{Decimal(v):,.0f}"


def digest(s: Any, member_id: int, today: date) -> tuple[str, str]:
    """Last Monday–Sunday: spend and its top categories, the week before, what's due in the next 7 days,
    budgets over, and the Inbox count."""
    end = today - timedelta(days=today.weekday())  # this Monday
    start, prev = end - timedelta(days=7), end - timedelta(days=14)
    def spent(a: date, b: date) -> tuple[Decimal, int]:
        amt, n = s.execute(select(func.coalesce(func.sum(expense_amount()), 0), func.count()).where(
            Txn.member_id == member_id, is_expense(), Txn.occurred_at >= a, Txn.occurred_at < b)).one()
        return Decimal(amt), n
    week, n = spent(start, end)
    last, _ = spent(prev, start)
    top = s.execute(select(Category.name, func.sum(expense_amount())).join(Category, Category.id == Txn.category_id).where(
        Txn.member_id == member_id, is_expense(), Txn.occurred_at >= start, Txn.occurred_at < end)
        .group_by(Category.name).order_by(func.sum(expense_amount()).desc()).limit(3)).all()
    due = [x for x in recurring.detect(s, member_id, today) if x.active and today <= x.next_due <= today + timedelta(days=7)]
    msd = month_start_day(s, member_id)
    over = [i for i in budgets.budgets(s, member_id, _cycle(today, msd), msd, today)["items"] if i["state"] == "over"]
    inbox = s.scalar(select(func.count()).where(Txn.member_id == member_id, Txn.category_id.is_(None)))
    lines = [f"Spent {_inr(week)} ({n} transactions) · week before {_inr(last)}"]
    if top:
        lines.append("Top: " + " · ".join(f"{c} {_inr(a)}" for c, a in top))
    if due:
        lines.append("Due next 7 days: " + " · ".join(f"{x.merchant} {_inr(x.amount_expected)} on {x.next_due:%d %b}" for x in due))
    if over:
        lines.append("Over budget: " + " · ".join(f"{i['category']} {_inr(i['spent'])} of {_inr(i['limit'])}" for i in over))
    if inbox:
        lines.append(f"Inbox: {inbox} to file")
    return f"Tijori · week of {start:%d %b}", "\n".join(lines)


def run(engine: Engine, members: list[MemberContext], settings: Settings, now: datetime | None = None) -> int:
    if not settings.ntfy_url:
        return 0
    now = now or datetime.now(UTC)
    today = today_ist()
    sent = 0
    for ctx in members:
        with member_session(engine, ctx) as s:
            cfg = s.scalar(select(Member.settings).where(Member.id == ctx.member_id)) or {}
            topic = cfg.get("notify_topic")
            if not (cfg.get("notify_enabled") and topic):
                continue
            seen = set(s.scalars(select(Alert.payload_json["id"].astext).where(Alert.member_id == ctx.member_id,
                                                                               Alert.kind == "push")))
            msd = month_start_day(s, ctx.member_id)
            items = month_alerts(s, ctx.member_id, _cycle(today, msd), msd)["items"]
            first = not seen
            if first:  # so a later run with the first real alert isn't taken for the first run
                s.add(Alert(member_id=ctx.member_id, kind="push", payload_json={"id": "baseline"}))
            outbox = []
            for a in items:
                if a["id"] in seen:
                    continue
                s.add(Alert(member_id=ctx.member_id, kind="push", payload_json={"id": a["id"], "title": a["title"]}))
                if not first:
                    outbox.append(a)
            ist = now + timedelta(hours=5, minutes=30)
            week_id = f"digest:{ist.date().isocalendar()[0]}-W{ist.date().isocalendar()[1]:02d}"
            want_digest = ist.weekday() == 0 and ist.hour >= 9 and week_id not in seen
            title_body = digest(s, ctx.member_id, today) if want_digest else None
            if want_digest:
                s.add(Alert(member_id=ctx.member_id, kind="push", payload_json={"id": week_id}))
            s.commit()
        for a in outbox:  # after the commit: a failed post is not retried, so nothing is sent twice
            sent += send(settings, topic, a["title"], a["detail"], "high" if a["severity"] == "bad" else "default",
                         "warning" if a["severity"] == "bad" else "")
        if title_body:
            sent += send(settings, topic, title_body[0], title_body[1], "low", "bar_chart")
    return sent
