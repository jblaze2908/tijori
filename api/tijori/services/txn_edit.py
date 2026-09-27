"""Split a txn into parts, and link txns by hand. Every write is member-scoped and audit-logged.

A split keeps the original (for its statement sighting) but moves it out of every total (`excluded`);
the parts carry the amounts and categories. Links record what two txns are to each other; a transfer or
pass-through takes both legs out of spend and income, a duplicate takes the second one out, and a refund
takes its purchase's merchant and category so it nets that spend.
"""

import hashlib
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, union_all, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from tijori.classify.engine import TxnInput
from tijori.classify.taxonomy import EXPENSE_BUCKETS, bucket_kind
from tijori.db import MemberContext
from tijori.models import Category, Txn, TxnLink
from tijori.money import fmt
from tijori.services.common import audit
from tijori.services.errors import Invalid, NotFound

MAX_PARTS = 10
LINK_KINDS = ("transfer", "refund", "dup", "pass_through")
LINK_WINDOW_DAYS = 10
UPI_REF = "^[0-9]{12}$"  # an RRN names one UPI payment, so a credit carrying a debit's is that payment coming back
UNLINKED = "link:refund_removed"  # the member took this refund off its purchase: never auto-link it again


def _txn(s: Session, ctx: MemberContext, txn_id: int) -> Txn:
    t = s.scalars(select(Txn).where(Txn.member_id == ctx.member_id, Txn.id == txn_id)).first()
    if t is None:
        raise NotFound("transaction not found")
    return t


def _category_bucket(s: Session, category_id: int | None, direction: str) -> tuple[str | None, str | None]:
    if category_id is None:
        return None, None
    c = s.get(Category, category_id)
    return bucket_kind(c.bucket, c.kind, c.credit_bucket, direction) if c else (None, None)


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
        bucket, kind = bucket_kind(c.bucket, c.kind, c.credit_bucket, t.direction)
        s.add(Txn(member_id=ctx.member_id, account_id=t.account_id, occurred_at=t.occurred_at, posted_at=t.posted_at,
                  amount=Decimal(p["amount"]), currency=t.currency, direction=t.direction, kind=kind,
                  merchant_norm=t.merchant_norm, counterparty=t.counterparty, ref_no=t.ref_no, narration=t.narration,
                  vpa=t.vpa, payee_key=t.payee_key, category_id=c.id, bucket=bucket, classified_by="user",
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
    t.bucket = _category_bucket(s, t.category_id, t.direction)[0]
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
    """What a link does to totals; removing it puts each leg back on its category's bucket. Refunds: see _net."""
    legs = (a, b) if kind in ("transfer", "pass_through") else (b,) if kind == "dup" else ()
    for t in legs:
        t.bucket = "excluded" if on else _category_bucket(s, t.category_id, t.direction)[0]

def _refund_legs(a: Txn, b: Txn) -> tuple[Txn, Txn]:
    """(purchase, refund), whichever order the link was made in."""
    if {a.direction, b.direction} != {"debit", "credit"}:
        raise Invalid("a refund links money coming back to the payment it returns")
    return (a, b) if a.direction == "debit" else (b, a)

def _net(s: Session, ctx: MemberContext, purchase: Txn, refund: Txn, by: str) -> None:
    """The refund takes its purchase's merchant and category and, as a refund credit in a spend bucket, subtracts
    from that spend (reports.is_expense). A purchase outside spend, or still in the Inbox, leaves it in Refunds."""
    refund.kind, refund.merchant_norm = "refund", purchase.merchant_norm or refund.merchant_norm
    if purchase.category_id is not None and purchase.bucket in EXPENSE_BUCKETS:
        refund.category_id, refund.bucket = purchase.category_id, purchase.bucket
    else:
        refund.category_id = s.scalar(select(Category.id).where(
            Category.household_id == ctx.household_id, Category.member_id.is_(None), Category.name == "Refunds"))
        refund.bucket = "income"
    refund.classified_by, refund.rule_id, refund.review_reason = by, "link:refund", None

def _restore(s: Session, ctx: MemberContext, refund: Txn) -> None:
    """Back to where the classifier files it, marked UNLINKED. One classifier load: unlinking is a rare hand edit."""
    from tijori.services.ingest import category_ids, load_classifier  # ingest links refunds: a top-level import cycles
    d = load_classifier(s, ctx).classify(TxnInput(refund.occurred_at, refund.amount, refund.direction,  # type: ignore[arg-type]
                                                  refund.narration or "", refund.account_id, refund.ref_no))
    refund.kind, refund.merchant_norm, refund.bucket, refund.review_reason = d.kind, d.merchant[:120], d.bucket, d.inbox_reason
    refund.category_id = category_ids(s, ctx.household_id).get(d.category) if d.category else None
    refund.classified_by, refund.rule_id = ("user" if d.filed else None), UNLINKED

def follow_purchases(s: Session, ctx: MemberContext, txn_ids: list[int]) -> None:
    """After these txns were recategorised, each refund linked to one of them as its purchase follows it.
    Called from the hand-filing paths; one read, plus a load per linked refund."""
    if not txn_ids:
        return
    pairs = s.execute(select(TxnLink.a_txn_id, TxnLink.b_txn_id).where(
        TxnLink.member_id == ctx.member_id, TxnLink.kind == "refund",
        or_(TxnLink.a_txn_id.in_(txn_ids), TxnLink.b_txn_id.in_(txn_ids)))).all()
    for a_id, b_id in pairs:
        purchase, refund = _refund_legs(_txn(s, ctx, a_id), _txn(s, ctx, b_id))
        if purchase.id in txn_ids:  # a refund recategorised by hand keeps the member's choice
            _net(s, ctx, purchase, refund, refund.classified_by or "rule")

def link(s: Session, ctx: MemberContext, actor: str, txn_id: int, other_id: int, kind: str) -> dict[str, Any]:
    if kind not in LINK_KINDS:
        raise Invalid("unknown link kind")
    if txn_id == other_id:
        raise Invalid("a transaction can't be linked to itself")
    a, b = _txn(s, ctx, txn_id), _txn(s, ctx, other_id)
    legs = _refund_legs(a, b) if kind == "refund" else None
    if legs:
        if s.scalar(select(TxnLink.id).where(
                TxnLink.member_id == ctx.member_id, TxnLink.kind == "refund",
                or_(TxnLink.a_txn_id == legs[1].id, TxnLink.b_txn_id == legs[1].id),
                TxnLink.a_txn_id.not_in((a.id, b.id)) | TxnLink.b_txn_id.not_in((a.id, b.id))).limit(1)):
            raise Invalid("this refund is already linked to another payment")
    s.execute(pg_insert(TxnLink).values(member_id=ctx.member_id, a_txn_id=a.id, b_txn_id=b.id, kind=kind)
              .on_conflict_do_nothing())
    if legs:
        _net(s, ctx, *legs, "user")
    else:
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
    if kind == "refund":
        _restore(s, ctx, _refund_legs(a, b)[1])
    elif kind != "card_payment":
        _effect(s, ctx, a, b, kind, False)
    else:  # a matched card bill goes back to standing in for card spend
        for t in (a, b):
            t.bucket = _category_bucket(s, t.category_id, t.direction)[0]
    audit(s, ctx, actor, "txn.unlink", f"txn:{a.id}", {"other": b.id, "kind": kind})
    return {"txn_id": a.id, "other_id": b.id, "kind": kind}

def link_refunds_by_ref(s: Session, ctx: MemberContext) -> int:
    """A credit carrying a debit's UPI ref on the same account is that payment coming back (a refund, or a
    reversal whose debit arrived in an earlier batch): link it as the debit's refund, which nets it (_net).
    Only the latest earlier debit of at least its amount qualifies. Hand-filed and UNLINKED credits are left alone.
    Idempotent; runs wherever cards.link_card_payments runs. Per call: one member-scoped self-join on ref_no, plus
    two loads and a write per new link."""
    c, d = aliased(Txn), aliased(Txn)
    linked = union_all(select(TxnLink.a_txn_id).where(TxnLink.member_id == ctx.member_id),
                       select(TxnLink.b_txn_id).where(TxnLink.member_id == ctx.member_id))
    pairs = s.execute(
        select(d.id.label("purchase"), c.id.label("refund")).distinct(c.id)
        .join(d, and_(d.member_id == c.member_id, d.account_id == c.account_id, d.ref_no == c.ref_no))
        .where(c.member_id == ctx.member_id, c.direction == "credit", c.ref_no.op("~")(UPI_REF),
               c.split_of.is_(None), c.status != "flagged", c.classified_by.is_distinct_from("user"),
               c.rule_id.is_distinct_from(UNLINKED), c.id.not_in(linked),
               or_(c.bucket.is_distinct_from("excluded"), c.kind == "refund"),  # a Reversals credit is excluded
               d.direction == "debit", d.split_of.is_(None), d.bucket.is_distinct_from("excluded"),
               d.amount >= c.amount, d.occurred_at <= c.occurred_at)
        .order_by(c.id, d.occurred_at.desc(), d.id.desc())
    ).all()
    for p in pairs:
        s.execute(pg_insert(TxnLink).values(member_id=ctx.member_id, a_txn_id=p.purchase, b_txn_id=p.refund,
                                            kind="refund").on_conflict_do_nothing())
        _net(s, ctx, _txn(s, ctx, p.purchase), _txn(s, ctx, p.refund), "rule")
    return len(pairs)

def link_candidates(s: Session, member_id: int, txn_id: int) -> list[dict[str, Any]]:
    """The likely other leg: the same amount the other way within 10 days (a transfer, refund or bill
    payment), the same amount the same way on the same day (a duplicate), or, first and at any distance,
    any amount the other way sharing this txn's UPI ref (a refund, partial ones included). One query."""
    t = s.scalars(select(Txn).where(Txn.member_id == member_id, Txn.id == txn_id)).first()
    if t is None:
        raise NotFound("transaction not found")
    same_ref = and_(Txn.ref_no == t.ref_no, Txn.direction != t.direction, Txn.ref_no.op("~")(UPI_REF))
    rows = s.execute(
        select(Txn.id, Txn.occurred_at, Txn.amount, Txn.direction, Txn.merchant_norm, Txn.account_id,
               same_ref.label("same_ref"))
        .where(Txn.member_id == member_id, Txn.id != t.id, Txn.split_of.is_(None),
               or_(and_(Txn.amount == t.amount,
                        Txn.occurred_at.between(t.occurred_at - timedelta(days=LINK_WINDOW_DAYS),
                                                t.occurred_at + timedelta(days=LINK_WINDOW_DAYS)),
                        or_(Txn.direction != t.direction, Txn.occurred_at == t.occurred_at)),
                   same_ref))
        .order_by(same_ref.desc(), func.abs(Txn.occurred_at - t.occurred_at), Txn.id).limit(8)
    ).all()
    return [{"id": r.id, "occurred_at": r.occurred_at, "amount": fmt(r.amount), "direction": r.direction,
             "merchant": r.merchant_norm, "account_id": r.account_id,
             "suggest": "refund" if r.same_ref else "dup" if r.direction == t.direction else "transfer"} for r in rows]
