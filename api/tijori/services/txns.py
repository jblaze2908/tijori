"""Transactions: list and detail, the Inbox, and categorisation writes."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Text, and_, cast, func, literal_column, or_, select, update
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.models import Category, Observation, RawMessage, Rule, RuleHit, Txn, TxnLink, TxnObservation
from tijori.money import fmt
from tijori.services.common import audit, category_by_ref, month_bounds, txn_out, txn_query
from tijori.services.errors import Invalid, NotFound


@dataclass(frozen=True, slots=True)
class TxnFilter:
    month: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    account: int | None = None
    category: int | str | None = None  # id, or "none" for uncategorized
    kind: str | None = None
    direction: str | None = None
    q: str | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _conditions(member_id: int, f: TxnFilter) -> ColumnElement[bool]:
    conds: list[ColumnElement[bool]] = [Txn.member_id == member_id]
    if f.month:
        start, end = month_bounds(f.month)
        conds += [Txn.occurred_at >= start, Txn.occurred_at < end]
    if f.date_from:
        conds.append(Txn.occurred_at >= f.date_from)
    if f.date_to:
        conds.append(Txn.occurred_at <= f.date_to)
    if f.account is not None:
        conds.append(Txn.account_id == f.account)
    if f.category == "none":
        conds.append(Txn.category_id.is_(None))
    elif f.category is not None:
        conds.append(Txn.category_id == int(f.category))
    if f.kind:
        conds.append(Txn.kind == f.kind)
    if f.direction:
        conds.append(Txn.direction == f.direction)
    if f.q:
        pattern = _like(f.q)
        conds.append(or_(Txn.narration.ilike(pattern, escape="\\"), Txn.merchant_norm.ilike(pattern, escape="\\")))
    if f.min_amount is not None:
        conds.append(Txn.amount >= f.min_amount)
    if f.max_amount is not None:
        conds.append(Txn.amount <= f.max_amount)
    return and_(*conds)


def list_txns(s: Session, member_id: int, f: TxnFilter, page: int, page_size: int) -> dict[str, Any]:
    """Two indexed queries per call: the total and the page."""
    where = _conditions(member_id, f)
    total = s.scalar(select(func.count()).select_from(Txn).where(where)) or 0
    rows = s.execute(txn_query().where(where).order_by(Txn.occurred_at.desc(), Txn.id.desc())
                     .limit(page_size).offset((page - 1) * page_size)).all()
    return {"items": [txn_out(r) for r in rows], "page": page, "page_size": page_size, "total": total}


def payee_history(s: Session, member_id: int, pairs: set[tuple[str, str]]) -> dict[tuple[str, str], list[dict]]:
    """How each (payee_key, direction) was categorised before; one grouped query for all pairs."""
    out: dict[tuple[str, str], list[dict]] = {}
    if not pairs:
        return out
    n = func.count().label("n")
    rows = s.execute(
        select(Txn.payee_key, Txn.direction, Category.id, Category.name, n)
        .join(Category, Category.id == Txn.category_id)
        .where(Txn.member_id == member_id, Txn.payee_key.in_({k for k, _ in pairs}))
        .group_by(Txn.payee_key, Txn.direction, Category.id, Category.name)
        .order_by(n.desc(), Category.name)
    ).all()
    for r in rows:
        out.setdefault((r.payee_key, r.direction), []).append({"category_id": r.id, "category": r.name, "count": r.n})
    return out


def get_txn(s: Session, member_id: int, txn_id: int) -> dict[str, Any]:
    row = s.execute(txn_query().where(Txn.member_id == member_id, Txn.id == txn_id)).first()
    if row is None:
        raise NotFound("transaction not found")
    t: Txn = row.Txn
    obs = s.execute(
        select(Observation, RawMessage.received_at, RawMessage.subject)
        .join(TxnObservation, TxnObservation.observation_id == Observation.id)
        .outerjoin(RawMessage, RawMessage.id == Observation.raw_message_id)
        .where(TxnObservation.txn_id == txn_id).order_by(Observation.id)
    ).all()
    links = s.execute(
        select(TxnLink.kind, TxnLink.a_txn_id, TxnLink.b_txn_id)
        .where(TxnLink.member_id == member_id, or_(TxnLink.a_txn_id == txn_id, TxnLink.b_txn_id == txn_id))
    ).all()
    payee = None
    if t.payee_key:
        n, total = s.execute(
            select(func.count(), func.coalesce(func.sum(Txn.amount), 0))
            .where(Txn.member_id == member_id, Txn.payee_key == t.payee_key, Txn.direction == t.direction)
        ).one()
        hist = payee_history(s, member_id, {(t.payee_key, t.direction)})
        payee = {"payee_key": t.payee_key, "count": n, "total": fmt(total),
                 "history": hist.get((t.payee_key, t.direction), [])}
    return {
        "transaction": txn_out(row),
        "observations": [
            {"id": o.id, "source": "statement", "parser": o.parser, "parser_version": o.parser_version,
             "occurred_at": o.occurred_at, "amount": fmt(o.amount), "direction": o.direction,
             "balance_after": fmt(o.balance_after) if o.balance_after is not None else None, "ref_no": o.ref_no,
             "raw_message_id": o.raw_message_id, "received_at": received_at, "filename": filename}
            for o, received_at, filename in obs
        ],
        "links": [{"kind": k, "txn_id": b if a == txn_id else a} for k, a, b in links],
        "payee": payee,
    }


def inbox(s: Session, member_id: int, page: int, page_size: int) -> dict[str, Any]:
    """Uncategorized txns, newest first, each with its payee's history. Three queries."""
    where = and_(Txn.member_id == member_id, Txn.category_id.is_(None))
    total = s.scalar(select(func.count()).select_from(Txn).where(where)) or 0
    rows = s.execute(txn_query().where(where).order_by(Txn.occurred_at.desc(), Txn.id.desc())
                     .limit(page_size).offset((page - 1) * page_size)).all()
    hist = payee_history(s, member_id, {(r.Txn.payee_key, r.Txn.direction) for r in rows if r.Txn.payee_key})
    items = [{"txn": txn_out(r), "reason": r.Txn.review_reason,
              "payee_history": hist.get((r.Txn.payee_key, r.Txn.direction), [])} for r in rows]
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def _group_key() -> ColumnElement[str]:
    # A txn with no payee identity is its own group. A literal, not a bind: the expression
    # appears in SELECT and GROUP BY, and Postgres must see them as identical.
    return func.coalesce(Txn.payee_key, literal_column("'txn:'").op("||")(cast(Txn.id, Text)))


def inbox_by_payee(s: Session, member_id: int, page: int, page_size: int) -> dict[str, Any]:
    """Inbox grouped by (payee, direction), most recent group first. Four queries per page."""
    gkey = _group_key().label("gkey")
    where = and_(Txn.member_id == member_id, Txn.category_id.is_(None))
    groups_q = (
        select(gkey, Txn.direction, func.count().label("n"), func.sum(Txn.amount).label("total"),
               func.max(Txn.occurred_at).label("last"),
               func.array_agg(Txn.id).label("ids"))
        .where(where).group_by(gkey, Txn.direction)
    )
    total = s.scalar(select(func.count()).select_from(groups_q.subquery())) or 0
    groups = s.execute(groups_q.order_by(func.max(Txn.occurred_at).desc(), gkey)
                       .limit(page_size).offset((page - 1) * page_size)).all()
    ids = [i for g in groups for i in g.ids]
    rows = {r.Txn.id: r for r in s.execute(txn_query().where(Txn.member_id == member_id, Txn.id.in_(ids)))} \
        if ids else {}
    hist = payee_history(s, member_id, {(g.gkey, g.direction) for g in groups})
    items = []
    for g in groups:
        members = sorted((rows[i] for i in g.ids if i in rows),
                         key=lambda r: (r.Txn.occurred_at, r.Txn.id), reverse=True)
        h = hist.get((g.gkey, g.direction), [])
        items.append({
            "payee_key": g.gkey, "direction": g.direction,
            "display": members[0].Txn.merchant_norm if members else None,
            "reason": members[0].Txn.review_reason if members else None,
            "count": g.n, "total": fmt(g.total), "last_at": g.last,
            "suggestion": h[0]["category"] if h else None, "history": h,
            "txns": [txn_out(r) for r in members],
        })
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def _resolve_category(s: Session, ctx: MemberContext, category_id: int | None, name: str | None) -> Category:
    cat = category_by_ref(s, ctx, category_id, name)
    if cat is None:
        raise Invalid("unknown category")
    return cat


def _create_payee_rule(s: Session, ctx: MemberContext, cat: Category, vpa: str | None, merchant: str | None,
                       direction: str | None) -> Rule:
    if vpa:
        match: dict[str, Any] = {"vpa": vpa}
    elif merchant:
        match = {"merchant": merchant}
    else:
        raise Invalid("this payee has no handle or merchant name to build a rule from")
    if direction:
        match["direction"] = direction
    rule = Rule(household_id=ctx.household_id, scope="member", member_id=ctx.member_id, match_json=match,
                category_id=cat.id, created_by="user")
    s.add(rule)
    s.flush()
    return rule


def _filed_values(cat: Category, by: str, rule_id: str) -> dict[str, Any]:
    return dict(category_id=cat.id, bucket=cat.bucket, kind=cat.kind, classified_by=by, rule_id=rule_id,
                review_reason=None, updated_at=func.now())


def set_category(s: Session, ctx: MemberContext, actor: str, txn_id: int, *, category_id: int | None,
                 category: str | None, scope: str) -> dict[str, Any]:
    """scope=this: this txn only. scope=payee: also a member rule, applied at once to the payee's
    other txns in the same direction that the member has not categorised by hand."""
    cat = _resolve_category(s, ctx, category_id, category)
    t = s.scalars(select(Txn).where(Txn.member_id == ctx.member_id, Txn.id == txn_id)).first()
    if t is None:
        raise NotFound("transaction not found")
    s.execute(update(Txn).where(Txn.id == txn_id).values(**_filed_values(cat, "user", "user")))
    updated, rule_ref = 1, None
    if scope == "payee":
        rule = _create_payee_rule(s, ctx, cat, t.vpa, t.merchant_norm, t.direction)
        rule_ref = f"rule:{rule.id}"
        others = s.scalars(
            update(Txn).where(Txn.member_id == ctx.member_id, Txn.payee_key == t.payee_key,
                              Txn.direction == t.direction, Txn.id != txn_id,
                              Txn.classified_by.is_distinct_from("user"))
            .values(**_filed_values(cat, "rule", rule_ref)).returning(Txn.id)
        ).all()
        if others:
            s.add_all(RuleHit(rule_id=rule.id, txn_id=i, member_id=ctx.member_id) for i in others)
        updated += len(others)
    audit(s, ctx, actor, "txn.categorize", f"txn:{txn_id}",
          {"category_id": cat.id, "scope": scope, "rule_id": rule_ref, "updated": updated})
    return {"updated": updated, "rule_id": rule_ref}


def file_inbox(s: Session, ctx: MemberContext, actor: str, payee_key: str, *, category_id: int | None,
               category: str | None, remember: bool, direction: str | None, txn_ids: list[int] | None
               ) -> dict[str, Any]:
    cat = _resolve_category(s, ctx, category_id, category)
    conds: list[ColumnElement[bool]] = [Txn.member_id == ctx.member_id, Txn.category_id.is_(None)]
    if payee_key.startswith("txn:") and payee_key[4:].isdigit():
        conds += [Txn.id == int(payee_key[4:]), Txn.payee_key.is_(None)]
    else:
        conds.append(Txn.payee_key == payee_key)
    if direction:
        conds.append(Txn.direction == direction)
    if txn_ids:
        conds.append(Txn.id.in_(txn_ids))
    filed = s.execute(update(Txn).where(*conds).values(**_filed_values(cat, "user", "user"))
                      .returning(Txn.id, Txn.vpa, Txn.merchant_norm, Txn.direction)).all()
    if not filed:
        raise NotFound("no Inbox items for this payee")
    rule_ref = None
    if remember:
        directions = {f.direction for f in filed}
        rule = _create_payee_rule(s, ctx, cat, filed[0].vpa, filed[0].merchant_norm,
                                  directions.pop() if len(directions) == 1 else None)
        rule_ref = f"rule:{rule.id}"
    audit(s, ctx, actor, "inbox.file", f"payee:{payee_key}",
          {"category_id": cat.id, "filed": len(filed), "rule_id": rule_ref})
    return {"filed": len(filed), "rule_id": rule_ref}
