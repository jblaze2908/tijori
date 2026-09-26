"""Split a txn into parts, and link txns by hand. Every write is member-scoped and audit-logged.

A split keeps the original (for its statement sighting) but moves it out of every total (`excluded`);
the parts carry the amounts and categories. Links record what two txns are to each other; a transfer or
pass-through takes both legs out of spend and income, a duplicate takes the second one out.
"""

import hashlib
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.models import Category, Txn, TxnLink
from tijori.money import fmt
from tijori.services.common import audit
from tijori.services.errors import Invalid, NotFound

MAX_PARTS = 10
LINK_KINDS = ("transfer", "refund", "dup", "pass_through")
LINK_WINDOW_DAYS = 10


def _txn(s: Session, ctx: MemberContext, txn_id: int) -> Txn:
    t = s.scalars(select(Txn).where(Txn.member_id == ctx.member_id, Txn.id == txn_id)).first()
    if t is None:
        raise NotFound("transaction not found")
    return t


def _category_bucket(s: Session, category_id: int | None) -> tuple[str | None, str | None]:
    if category_id is None:
        return None, None
    c = s.get(Category, category_id)
    return (c.bucket, c.kind) if c else (None, None)


def split(s: Session, ctx: MemberContext, actor: str, txn_id: int, parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace any earlier split. Parts must add up to the txn to the paisa."""
    t = _txn(s, ctx, txn_id)
    if t.split_of is not None:
        raise Invalid("a part can't be split again; split the original")
    if not 2 <= len(parts) <= MAX_PARTS:
        raise Invalid(f"split into 2 to {MAX_PARTS} parts")
    total = sum((Decimal(p["amount"]) for p in parts), Decimal(0))
    if total != t.amount or any(Decimal(p["amount"]) <= 0 for p in parts):
        raise Invalid(f"parts must be positive and add up to {fmt(t.amount)}")
    s.execute(delete(Txn).where(Txn.member_id == ctx.member_id, Txn.split_of == t.id))
    for i, p in enumerate(parts):
        c = s.get(Category, p["category_id"])
        if c is None:
            raise Invalid("unknown category")
        s.add(Txn(member_id=ctx.member_id, account_id=t.account_id, occurred_at=t.occurred_at, posted_at=t.posted_at,
                  amount=Decimal(p["amount"]), currency=t.currency, direction=t.direction, kind=c.kind,
                  merchant_norm=t.merchant_norm, counterparty=t.counterparty, ref_no=t.ref_no, narration=t.narration,
                  vpa=t.vpa, payee_key=t.payee_key, category_id=c.id, bucket=c.bucket, classified_by="user",
                  rule_id=None, review_reason=None, status=t.status, notes=p.get("note") or None,
                  sources=list(t.sources or []), split_of=t.id,
                  dedupe_key=hashlib.sha256(f"split|{t.id}|{i}".encode()).hexdigest()))
    t.bucket = "excluded"
    t.review_reason = None
    audit(s, ctx, actor, "txn.split", f"txn:{t.id}", {"parts": len(parts)})
    s.flush()
    return {"id": t.id, "parts": len(parts)}


def unsplit(s: Session, ctx: MemberContext, actor: str, txn_id: int) -> dict[str, Any]:
    t = _txn(s, ctx, txn_id)
    n = s.execute(delete(Txn).where(Txn.member_id == ctx.member_id, Txn.split_of == t.id)).rowcount
    if not n:
        raise Invalid("this transaction isn't split")
    t.bucket = _category_bucket(s, t.category_id)[0]
    audit(s, ctx, actor, "txn.unsplit", f"txn:{t.id}", {"parts": n})
    return {"id": t.id, "parts": 0}


def parts_of(s: Session, member_id: int, txn_id: int) -> list[dict[str, Any]]:
    rows = s.execute(select(Txn.id, Txn.amount, Category.name, Txn.notes).outerjoin(Category, Category.id == Txn.category_id)
                     .where(Txn.member_id == member_id, Txn.split_of == txn_id).order_by(Txn.id)).all()
    return [{"id": r.id, "amount": fmt(r.amount), "category": r.name, "note": r.notes} for r in rows]


def split_counts(s: Session, member_id: int, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    return dict(s.execute(select(Txn.split_of, func.count()).where(Txn.member_id == member_id, Txn.split_of.in_(ids))
                          .group_by(Txn.split_of)).all())


def _effect(s: Session, ctx: MemberContext, a: Txn, b: Txn, kind: str, on: bool) -> None:
    """What a link does to totals; removing it puts each leg back on its category's bucket."""
    legs = (a, b) if kind in ("transfer", "pass_through") else (b,) if kind == "dup" else ()
    for t in legs:
        t.bucket = "excluded" if on else _category_bucket(s, t.category_id)[0]


def link(s: Session, ctx: MemberContext, actor: str, txn_id: int, other_id: int, kind: str) -> dict[str, Any]:
    if kind not in LINK_KINDS:
        raise Invalid("unknown link kind")
    if txn_id == other_id:
        raise Invalid("a transaction can't be linked to itself")
    a, b = _txn(s, ctx, txn_id), _txn(s, ctx, other_id)
    s.execute(pg_insert(TxnLink).values(member_id=ctx.member_id, a_txn_id=a.id, b_txn_id=b.id, kind=kind)
              .on_conflict_do_nothing())
    _effect(s, ctx, a, b, kind, True)
    audit(s, ctx, actor, "txn.link", f"txn:{a.id}", {"other": b.id, "kind": kind})
    return {"txn_id": a.id, "other_id": b.id, "kind": kind}


def unlink(s: Session, ctx: MemberContext, actor: str, txn_id: int, other_id: int, kind: str) -> dict[str, Any]:
    a, b = _txn(s, ctx, txn_id), _txn(s, ctx, other_id)
    n = s.execute(delete(TxnLink).where(
        TxnLink.member_id == ctx.member_id, TxnLink.kind == kind,
        or_((TxnLink.a_txn_id == a.id) & (TxnLink.b_txn_id == b.id), (TxnLink.a_txn_id == b.id) & (TxnLink.b_txn_id == a.id)))).rowcount
    if not n:
        raise NotFound("no such link")
    if kind != "card_payment":
        _effect(s, ctx, a, b, kind, False)
    else:  # a matched card bill goes back to standing in for card spend
        for t in (a, b):
            t.bucket = _category_bucket(s, t.category_id)[0]
    audit(s, ctx, actor, "txn.unlink", f"txn:{a.id}", {"other": b.id, "kind": kind})
    return {"txn_id": a.id, "other_id": b.id, "kind": kind}


def link_candidates(s: Session, member_id: int, txn_id: int) -> list[dict[str, Any]]:
    """The likely other leg: the same amount the other way within 10 days (a transfer, refund or bill
    payment), or the same amount the same way on the same day (a duplicate). One query."""
    t = s.scalars(select(Txn).where(Txn.member_id == member_id, Txn.id == txn_id)).first()
    if t is None:
        raise NotFound("transaction not found")
    rows = s.execute(
        select(Txn.id, Txn.occurred_at, Txn.amount, Txn.direction, Txn.merchant_norm, Txn.account_id)
        .where(Txn.member_id == member_id, Txn.id != t.id, Txn.amount == t.amount, Txn.split_of.is_(None),
               Txn.occurred_at.between(t.occurred_at - timedelta(days=LINK_WINDOW_DAYS),
                                       t.occurred_at + timedelta(days=LINK_WINDOW_DAYS)),
               or_(Txn.direction != t.direction, Txn.occurred_at == t.occurred_at))
        .order_by(func.abs(Txn.occurred_at - t.occurred_at), Txn.id).limit(8)
    ).all()
    return [{"id": r.id, "occurred_at": r.occurred_at, "amount": fmt(r.amount), "direction": r.direction,
             "merchant": r.merchant_norm, "account_id": r.account_id,
             "suggest": "dup" if r.direction == t.direction else "transfer"} for r in rows]
