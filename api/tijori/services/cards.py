"""Card bills: a purchase is spend on the card it was made with; a bill payment moves money between your
own accounts. A bank-side bill payment (CRED, BillDesk) matched to the card's own "payment received" line
becomes a transfer. Unmatched, it stays in the `card` bucket and stands in for card spend, so months with
no parsed card statement are not undercounted.
"""

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from tijori.models import Account, Category, Statement, Txn, TxnLink
from tijori.money import fmt
from tijori.services.common import account_label, today_ist

MATCH_DAYS = 4  # bank debit and card credit post within this many days of each other
# A payment settles the card statement that closed before it; if that closed longer ago than this,
# the statement it pays was never parsed and the payment stands in for its purchases.
SETTLES_WITHIN_DAYS = 45
PAYMENT_TEXT = re.compile(r"PAYMENT|THANK ?YOU|\bCRED\b|BBPS|AUTO ?PAY|AUTODEBIT|\bNEFT\b|\bIMPS\b", re.I)


def link_card_payments(s: Session, member_id: int) -> int:
    """Idempotent; runs after every statement ingest. Each unmatched bank bill payment takes the closest
    unmatched card credit of the same amount within ±4 days, when the statement it pays has been parsed.
    Per call: three member-scoped reads and one write per match; history is a few hundred bill payments."""
    linked_a = select(TxnLink.a_txn_id).where(TxnLink.member_id == member_id, TxnLink.kind == "card_payment")
    linked_b = select(TxnLink.b_txn_id).where(TxnLink.member_id == member_id, TxnLink.kind == "card_payment")
    payments = s.execute(
        select(Txn.id, Txn.occurred_at, Txn.amount).join(Account, Account.id == Txn.account_id)
        .where(Txn.member_id == member_id, Account.kind != "card", Txn.direction == "debit", Txn.bucket == "card",
               Txn.id.not_in(linked_a))
        .order_by(Txn.occurred_at, Txn.id)
    ).all()
    credits = [c for c in s.execute(
        select(Txn.id, Txn.occurred_at, Txn.amount, Txn.account_id, Txn.narration, Category.name.label("category"))
        .join(Account, Account.id == Txn.account_id).outerjoin(Category, Category.id == Txn.category_id)
        .where(Txn.member_id == member_id, Account.kind == "card", Txn.direction == "credit",
               Txn.id.not_in(linked_b))
    ).all() if c.category == "Card bill payment" or PAYMENT_TEXT.search(c.narration or "")]
    closes: dict[int, list[date]] = {}
    for acct, end in s.execute(select(Statement.account_id, Statement.period_end).join(
            Account, Account.id == Statement.account_id).where(Statement.member_id == member_id,
                                                               Account.kind == "card")):
        closes.setdefault(acct, []).append(end)
    card_bill = s.scalar(select(Category.id).where(Category.name == "Card bill payment").limit(1))
    used: set[int] = set()
    n = 0
    for p in payments:
        cands = [c for c in credits if c.id not in used and c.amount == p.amount
                 and abs((c.occurred_at - p.occurred_at).days) <= MATCH_DAYS]
        if not cands:
            continue
        c = min(cands, key=lambda c: (abs((c.occurred_at - p.occurred_at).days), c.id))
        if not settles_parsed(closes.get(c.account_id, []), p.occurred_at):
            continue
        used.add(c.id)
        s.execute(pg_insert(TxnLink).values(member_id=member_id, a_txn_id=p.id, b_txn_id=c.id, kind="card_payment")
                  .on_conflict_do_nothing())
        # Both legs leave spend: the purchases are counted on the card instead.
        s.execute(update(Txn).where(Txn.member_id == member_id, Txn.id == p.id).values(bucket="excluded"))
        n += 1
    # Card-side payment lines are never spend or income, matched or not.
    file_ids = [c.id for c in credits if c.category != "Card bill payment" or c.id in used]
    if file_ids:
        s.execute(update(Txn).where(Txn.member_id == member_id, Txn.id.in_(file_ids))
                  .values(bucket="excluded", kind="transfer", category_id=card_bill, classified_by="rule",
                          rule_id="kind:card_payment", review_reason=None))
    return n


def settles_parsed(closes: list[date], paid_on: date) -> bool:
    """The latest statement closed before the payment is on file."""
    before = [d for d in closes if d < paid_on]
    return bool(before) and (paid_on - max(before)).days <= SETTLES_WITHIN_DAYS


def settles(s: Session, member_id: int, txn_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Both legs of each matched bill payment among `txn_ids`: the bank leg gets the card it paid, the card
    leg gets the account it was paid from. One query."""
    if not txn_ids:
        return {}
    other = aliased(Txn)
    rows = s.execute(
        select(Txn.id, other.id.label("other_id"), other.occurred_at, Account.institution, Account.name,
               Account.mask, (TxnLink.a_txn_id == Txn.id).label("is_bank"))
        .select_from(TxnLink)
        .join(Txn, or_(Txn.id == TxnLink.a_txn_id, Txn.id == TxnLink.b_txn_id))
        .join(other, and_(or_(other.id == TxnLink.a_txn_id, other.id == TxnLink.b_txn_id), other.id != Txn.id))
        .join(Account, Account.id == other.account_id)
        .where(TxnLink.member_id == member_id, TxnLink.kind == "card_payment", Txn.id.in_(txn_ids))
    ).all()
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        label = account_label(r.institution, r.name, r.mask)
        out[r.id] = {"txn_id": r.other_id, "date": r.occurred_at,
                     "card": label if r.is_bank else None, "from_account": None if r.is_bank else label}
    return out


def card_status(s: Session, member_id: int, today: date | None = None) -> dict[str, Any]:
    """Per card: the newest parsed statement, the payments matched to it, and purchases since it closed.
    Per call: three small queries per card (a member has a handful)."""
    today = today or today_ist()
    out: list[dict[str, Any]] = []
    for a in s.scalars(select(Account).where(Account.member_id == member_id, Account.kind == "card")
                       .order_by(Account.id)).all():
        st = s.scalars(select(Statement).where(Statement.member_id == member_id, Statement.account_id == a.id)
                       .order_by(Statement.period_end.desc()).limit(1)).first()
        since = st.period_end + timedelta(days=1) if st else today.replace(day=1)
        cycle_amount, cycle_count, seen = s.execute(
            select(func.coalesce(func.sum(Txn.amount), 0), func.count(), func.max(Txn.occurred_at))
            .where(Txn.member_id == member_id, Txn.account_id == a.id, Txn.direction == "debit",
                   Txn.bucket.is_distinct_from("excluded"), Txn.occurred_at >= since)).one()
        out.append({"account": {"id": a.id, "label": account_label(a.institution, a.name, a.mask)},
                    "statement": _statement_out(s, member_id, a.id, st) if st else None,
                    "cycle": {"since": since, "amount": fmt(Decimal(cycle_amount)), "count": cycle_count,
                              "seen_through": seen}})
    return {"items": out, "stand_in": stand_in(s, member_id)}


def _statement_out(s: Session, member_id: int, account_id: int, st: Statement) -> dict[str, Any]:
    purchases = s.scalar(select(func.count()).where(
        Txn.member_id == member_id, Txn.account_id == account_id, Txn.direction == "debit",
        Txn.occurred_at >= st.period_start, Txn.occurred_at <= st.period_end))
    card_leg = select(Txn.id).where(Txn.member_id == member_id, Txn.account_id == account_id,
                                    Txn.direction == "credit", Txn.occurred_at > st.period_end)
    paid = s.execute(
        select(Txn.occurred_at, Txn.amount, Txn.account_id).join(TxnLink, TxnLink.a_txn_id == Txn.id)
        .where(TxnLink.member_id == member_id, TxnLink.kind == "card_payment", TxnLink.b_txn_id.in_(card_leg))
        .order_by(Txn.occurred_at)).all()
    total = st.total_due if st.total_due is not None else st.closing
    paid_total = sum((r.amount for r in paid), Decimal(0))
    return {"period_start": st.period_start, "period_end": st.period_end, "total": fmt(total),
            "due_date": st.due_date, "purchases": purchases, "paid": fmt(paid_total),
            "paid_at": paid[-1].occurred_at if paid else None,
            "paid_from": _label(s, paid[-1].account_id) if paid else None,
            "state": "paid" if paid_total >= total else ("part_paid" if paid_total else "unpaid")}


def _label(s: Session, account_id: int) -> str | None:
    a = s.get(Account, account_id)
    return account_label(a.institution, a.name, a.mask) if a else None


def stand_in(s: Session, member_id: int) -> dict[str, Any]:
    """Bank bill payments still counted as spend because the card statement they pay isn't parsed."""
    amount, count, first, last = s.execute(
        select(func.coalesce(func.sum(Txn.amount), 0), func.count(), func.min(Txn.occurred_at),
               func.max(Txn.occurred_at))
        .join(Account, Account.id == Txn.account_id)
        .where(Txn.member_id == member_id, Account.kind != "card", Txn.direction == "debit", Txn.bucket == "card")
    ).one()
    return {"amount": fmt(Decimal(amount)), "count": count, "first": first, "last": last}
