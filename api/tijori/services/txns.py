"""Transactions: list and detail, the Inbox, and categorisation writes."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, String, Text, and_, case, cast, delete, func, literal_column, or_, select, update
from sqlalchemy.orm import Session

from tijori.classify.merchants import MERCHANT_QR_HANDLES
from tijori.db import MemberContext
from tijori.models import Category, Observation, RawMessage, Rule, RuleHit, Txn, TxnLink, TxnObservation
from tijori.money import ZERO, fmt
from tijori.services.common import audit, category_by_ref, cycle_bounds, txn_out, txn_query
from tijori.services.errors import Invalid, NotFound
from tijori.services.reports import is_expense


@dataclass(frozen=True, slots=True)
class TxnFilter:
    month: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    accounts: tuple[int, ...] = ()
    categories: tuple[str, ...] = ()  # ids, and/or "none" for uncategorized; any of them matches
    kind: str | None = None
    direction: str | None = None
    q: str | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    month_start_day: int = 1  # `month` is a cycle starting on this day
    sort: str = "date_desc"


SORTS = ("date_desc", "date_asc", "amount_desc", "amount_asc")


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _conditions(member_id: int, f: TxnFilter) -> ColumnElement[bool]:
    conds: list[ColumnElement[bool]] = [Txn.member_id == member_id]
    if f.month:
        start, end = cycle_bounds(f.month, f.month_start_day)
        conds += [Txn.occurred_at >= start, Txn.occurred_at < end]
    if f.date_from:
        conds.append(Txn.occurred_at >= f.date_from)
    if f.date_to:
        conds.append(Txn.occurred_at <= f.date_to)
    if f.accounts:
        conds.append(Txn.account_id.in_(f.accounts))
    if f.categories:
        ids = [int(c) for c in f.categories if c != "none"]
        either = [Txn.category_id.in_(ids)] if ids else []
        if "none" in f.categories:
            either.append(Txn.category_id.is_(None))
        conds.append(or_(*either))
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


_ORDER = {
    "date_desc": (Txn.occurred_at.desc(), Txn.id.desc()),
    "date_asc": (Txn.occurred_at.asc(), Txn.id.asc()),
    "amount_desc": (Txn.amount.desc(), Txn.occurred_at.desc(), Txn.id.desc()),
    "amount_asc": (Txn.amount.asc(), Txn.occurred_at.desc(), Txn.id.desc()),
}


def _measure() -> ColumnElement[str]:
    """Which total a txn feeds, on /api/summary's rules: spend, income, invest, or excluded."""
    return case(
        (is_expense(), literal_column("'spend'")),
        (and_(Txn.direction == "credit", Txn.bucket == "income"), literal_column("'income'")),
        (and_(Txn.direction == "debit", Txn.bucket == "invest"), literal_column("'invest'")),
        else_=literal_column("'excluded'"),
    )


def list_txns(s: Session, member_id: int, f: TxnFilter, page: int, page_size: int) -> dict[str, Any]:
    """Two indexed queries per call: the grouped totals of the whole filtered set, and the page."""
    where = _conditions(member_id, f)
    measure = _measure().label("measure")
    card = (Txn.bucket == "card").label("card")
    groups = s.execute(select(measure, card, func.count().label("n"), func.sum(Txn.amount).label("amount"))
                       .where(where).group_by(measure, card)).all()
    totals = {k: {"amount": ZERO, "count": 0} for k in ("spend", "income", "invest", "excluded", "card")}
    for g in groups:
        for k in (g.measure, "card") if g.card and g.measure == "spend" else (g.measure,):
            totals[k]["amount"] += g.amount
            totals[k]["count"] += g.n
    total = sum(g.n for g in groups)
    rows = s.execute(txn_query().where(where).order_by(*_ORDER[f.sort])
                     .limit(page_size).offset((page - 1) * page_size)).all()
    return {"items": [txn_out(r) for r in rows], "page": page, "page_size": page_size, "total": total,
            "totals": {k: {"amount": fmt(v["amount"]), "count": v["count"]} for k, v in totals.items()}}


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


PAYEE_RECENT = 12


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
        recent = s.execute(
            select(Txn.id, Txn.occurred_at, Txn.amount, Category.name)
            .outerjoin(Category, Category.id == Txn.category_id)
            .where(Txn.member_id == member_id, Txn.payee_key == t.payee_key, Txn.direction == t.direction)
            .order_by(Txn.occurred_at.desc(), Txn.id.desc()).limit(PAYEE_RECENT)
        ).all()
        payee = {"payee_key": t.payee_key, "count": n, "total": fmt(total),
                 "history": hist.get((t.payee_key, t.direction), []),
                 "recent": [{"id": r.id, "occurred_at": r.occurred_at, "amount": fmt(r.amount), "category": r.name}
                            for r in recent]}
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


MAX_NOTE = 2000
MAX_TAGS = 20


def update_txn(s: Session, ctx: MemberContext, actor: str, txn_id: int, *, notes: str | None, tags: list[str] | None,
               fields: set[str]) -> dict[str, Any]:
    """Notes and tags only; the category has its own endpoint because it teaches payee memory."""
    t = s.scalars(select(Txn).where(Txn.member_id == ctx.member_id, Txn.id == txn_id)).first()
    if t is None:
        raise NotFound("transaction not found")
    if "notes" in fields:
        t.notes = (notes or "").strip() or None
    if "tags" in fields:
        clean = []
        for tag in tags or []:
            tag = tag.strip()
            if tag and tag.lower() not in {c.lower() for c in clean}:
                clean.append(tag)
        t.tags = clean[:MAX_TAGS]
    s.flush()
    audit(s, ctx, actor, "txn.update", f"txn:{txn_id}", {"fields": sorted(fields)})
    return {"id": t.id, "notes": t.notes, "tags": list(t.tags or [])}


def unfile(s: Session, ctx: MemberContext, actor: str, txn_ids: list[int], rule_id: str | None) -> dict[str, Any]:
    """Undo an Inbox filing: the txns go back to the Inbox, and the rule it created (if any) is deleted.
    Only txns filed by hand or by that rule are touched."""
    rule_pk = int(rule_id[5:]) if rule_id and rule_id.startswith("rule:") and rule_id[5:].isdigit() else None
    # Filing clears review_reason, so it is re-derived the way the classifier would have set it.
    reason = case((Txn.vpa.op("~*")(MERCHANT_QR_HANDLES.pattern), literal_column("'merchant_over_cap'")),
                  (Txn.payee_key.like("vpa:%"), literal_column("'person'")),
                  else_=literal_column("'new_payee'"))
    back_to_inbox = dict(category_id=None, bucket=None, classified_by=None, rule_id="inbox:undo",
                         review_reason=reason, updated_at=func.now())
    owned_by = [Txn.classified_by == "user"]
    if rule_pk is not None:
        owned_by.append(and_(Txn.classified_by == "rule", Txn.rule_id == rule_id))
    back = s.execute(
        update(Txn).where(Txn.member_id == ctx.member_id, Txn.id.in_(txn_ids), or_(*owned_by))
        .values(**back_to_inbox).returning(Txn.id)
    ).scalars().all()
    rule_removed = False
    if rule_pk is not None:
        restored = s.execute(
            update(Txn).where(Txn.member_id == ctx.member_id, Txn.classified_by == "rule", Txn.rule_id == rule_id)
            .values(**back_to_inbox).returning(Txn.id)
        ).scalars().all()
        back = sorted({*back, *restored})
        rule_removed = s.execute(delete(Rule).where(Rule.id == rule_pk, Rule.member_id == ctx.member_id)
                                 .returning(Rule.id)).first() is not None
    audit(s, ctx, actor, "inbox.undo", f"txns:{len(back)}", {"rule_id": rule_id, "rule_removed": rule_removed})
    return {"restored": len(back), "rule_removed": rule_removed}


def filing_stats(s: Session, member_id: int, month: str, month_start_day: int = 1) -> dict[str, Any]:
    """How the month's txns were filed, by decider. One grouped query, plus the member's rule count."""
    start, end = cycle_bounds(month, month_start_day)
    by = case(
        (Txn.category_id.is_(None), literal_column("'waiting'")),
        (and_(Txn.classified_by == "rule", Txn.rule_id.like("rule:%")), literal_column("'rules'")),
        (Txn.classified_by == "rule", literal_column("'structural'")),
        (Txn.classified_by.in_(("heuristic", "system")), literal_column("'structural'")),
        else_=cast(Txn.classified_by, String(24)),
    ).label("by")
    rows = s.execute(select(by, func.count().label("n")).where(
        Txn.member_id == member_id, Txn.occurred_at >= start, Txn.occurred_at < end).group_by(by)).all()
    counts = {k: 0 for k in ("rules", "payee_memory", "dictionary", "structural", "user", "waiting")}
    for r in rows:
        counts[r.by] = counts.get(r.by, 0) + r.n
    total = sum(counts.values())
    rules = s.scalar(select(func.count()).select_from(Rule).where(Rule.member_id == member_id, Rule.enabled)) or 0
    return {"month": month, "total": total, "automatic": total - counts["waiting"] - counts["user"],
            "by": counts, "rules": rules}


def list_rules(s: Session, ctx: MemberContext) -> dict[str, Any]:
    """The member's own rules and the household's, with how many txns each has filed."""
    hits = (select(RuleHit.rule_id, func.count().label("n"), func.max(RuleHit.at).label("last"))
            .where(RuleHit.member_id == ctx.member_id).group_by(RuleHit.rule_id).subquery())
    rows = s.execute(
        select(Rule, Category.name, func.coalesce(hits.c.n, 0).label("n"), hits.c.last)
        .join(Category, Category.id == Rule.category_id)
        .outerjoin(hits, hits.c.rule_id == Rule.id)
        .where(Rule.household_id == ctx.household_id,
               or_(Rule.member_id.is_(None), Rule.member_id == ctx.member_id))
        .order_by(Rule.created_at.desc(), Rule.id.desc())
    ).all()
    return {"items": [
        {"id": f"rule:{r.id}", "scope": r.scope, "match": r.match_json, "category": name, "enabled": r.enabled,
         "created_by": r.created_by, "created_at": r.created_at, "hits": n, "last_hit_at": last,
         "editable": r.member_id == ctx.member_id}
        for r, name, n, last in rows]}


def set_rule_enabled(s: Session, ctx: MemberContext, actor: str, rule_pk: int, enabled: bool) -> dict[str, Any]:
    rule = s.scalars(select(Rule).where(Rule.id == rule_pk, Rule.member_id == ctx.member_id)).first()
    if rule is None:
        raise NotFound("rule not found")
    rule.enabled = enabled
    audit(s, ctx, actor, "rule.enable" if enabled else "rule.disable", f"rule:{rule_pk}", {})
    return {"id": f"rule:{rule_pk}", "enabled": enabled}
