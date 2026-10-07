"""Merchant orders (docs/api.md, Orders): Blinkit and Zomato receipts as the line items behind a txn.

record() upserts a batch by (source, order_no), then re-matches every open order of the member against one query
of candidate debits. A debit matches when it is the brand's payee, the same amount to the paisa, on the order day or
the next (measured 2026-10-07: 107 of 131 Blinkit and 15 of 20 Zomato orders matched this way, none ambiguously).
Orders placed in the same minute may share one debit. Nothing is guessed past that: an order no debit pays is put on
an account only when its receipt names the card (`payment` ends in an account's last 4 digits) or when someone
assigns it (assign()). That txn is built from the receipt and kept out of totals (bucket excluded): it was paid from
money Tijori never saw arrive, such as a meal card, so counting the spend would be one-sided. A debit that turns up
later takes the order back.
"""

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from tijori.classify.brands import BRANDS
from tijori.db import MemberContext
from tijori.models import Account, MerchantOrder, MerchantOrderItem, Txn
from tijori.money import fmt
from tijori.services.common import IST, audit, category_by_ref, sha256_hex
from tijori.services.errors import Invalid, NotFound

SOURCES = ("blinkit", "zomato")  # each is also its brand key in classify/brands.py
OPEN = ("unmatched", "ambiguous", "assigned")
MATCH_LAG_DAYS = 1
CARD_RULE, ASSIGNED_RULE = "order:card", "order:assigned"  # receipt txn's rule_id: the card on the receipt, or a person
LAST4 = re.compile(r"(?<!\d)\d{4}(?!\d)")  # "Paid via Card (XXXX XXXX 0001)"
_BRANDS = {b.key: b for b in BRANDS if b.key in SOURCES}


def _day(at: datetime) -> date:
    return at.astimezone(IST).date()


def _dec(v: str | None) -> Decimal | None:
    return Decimal(v) if v is not None else None


def _row(o: dict[str, Any]) -> dict[str, Any]:
    return {"placed_at": o["placed_at"], "status": o["status"], "payment": o.get("payment"), "store": o.get("store"),
            "delivery_address": o.get("delivery_address"), "address_label": o.get("address_label"),
            "category": o.get("category"), "item_total": _dec(o.get("item_total")), "charges": {k: fmt(Decimal(v)) for k, v in (o.get("charges") or {}).items()},
            "bill_total": Decimal(o["bill_total"])}


def _items(o: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [(i["name"], i.get("unit"), i["qty"], _dec(i.get("line_price")), _dec(i.get("unit_price")), i.get("note"),
             i.get("category")) for i in o.get("items") or []]


def record(s: Session, ctx: MemberContext, actor: str, orders: list[dict[str, Any]]) -> dict[str, Any]:
    """Idempotent: resending an order changes nothing. A changed amount, date or status unlinks it and matches again."""
    keys = [(o["source"], o["order_no"]) for o in orders]
    if len(set(keys)) != len(keys):
        raise Invalid("an order appears twice in this batch")
    have = {(r.source, r.order_no): r for r in s.scalars(select(MerchantOrder).where(
        MerchantOrder.member_id == ctx.member_id, MerchantOrder.order_no.in_([k[1] for k in keys])))}
    old_items = defaultdict(list)
    if have:
        for i in s.scalars(select(MerchantOrderItem).where(MerchantOrderItem.member_id == ctx.member_id,
                                                           MerchantOrderItem.order_id.in_([r.id for r in have.values()]))
                           .order_by(MerchantOrderItem.order_id, MerchantOrderItem.position)):
            old_items[i.order_id].append((i.name, i.unit, i.qty, i.line_price, i.unit_price, i.note, i.category))
    created = updated = 0
    for o in orders:
        row, items, cur = _row(o), _items(o), have.get((o["source"], o["order_no"]))
        if cur is None:
            cur = MerchantOrder(member_id=ctx.member_id, source=o["source"], order_no=o["order_no"], **row)
            s.add(cur)
            s.flush()
            created += 1
        else:
            changed = {k: v for k, v in row.items() if getattr(cur, k) != v}
            if not changed and old_items[cur.id] == items:
                continue
            if {"placed_at", "status", "bill_total"} & changed.keys():
                _unlink(s, ctx, cur)
            for k, v in changed.items():
                setattr(cur, k, v)
            s.execute(delete(MerchantOrderItem).where(MerchantOrderItem.member_id == ctx.member_id,
                                                      MerchantOrderItem.order_id == cur.id))
            updated += 1
        s.add_all(MerchantOrderItem(member_id=ctx.member_id, order_id=cur.id, position=n, name=name, unit=unit, qty=qty,
                                    line_price=lp, unit_price=up, note=note, category=cat)
                  for n, (name, unit, qty, lp, up, note, cat) in enumerate(items))
    s.flush()
    rematch(s, ctx)
    rows = s.execute(select(MerchantOrder.source, MerchantOrder.order_no, MerchantOrder.match_state, MerchantOrder.txn_id)
                     .where(MerchantOrder.member_id == ctx.member_id, MerchantOrder.order_no.in_([k[1] for k in keys]))).all()
    out = [{"source": r.source, "order_no": r.order_no, "match_state": r.match_state, "txn_id": r.txn_id}
           for r in rows if (r.source, r.order_no) in set(keys)]
    states: dict[str, int] = defaultdict(int)
    for r in out:
        states[r["match_state"]] += 1
    audit(s, ctx, actor, "orders.record", None, {"received": len(orders), "created": created, "updated": updated})
    return {"received": len(orders), "created": created, "updated": updated,
            "unchanged": len(orders) - created - updated, "states": dict(states), "orders": out}


def _unlink(s: Session, ctx: MemberContext, o: MerchantOrder) -> None:
    """Drop the order's link; a receipt txn exists only for its order, so it goes too."""
    if o.match_state == "assigned" and o.txn_id is not None:
        s.execute(delete(Txn).where(Txn.member_id == ctx.member_id, Txn.id == o.txn_id, Txn.sources.contains(["order"])))
    o.txn_id, o.match_state = None, "unmatched"


def rematch(s: Session, ctx: MemberContext) -> None:
    """Every open order of the member, in one candidate query. Per record() call, so per bot run, not per order."""
    orders = s.scalars(select(MerchantOrder).where(
        MerchantOrder.member_id == ctx.member_id,
        or_(MerchantOrder.match_state.in_(OPEN), MerchantOrder.status == "cancelled",
            MerchantOrder.txn_id.is_(None)))).all()
    for o in orders:
        if o.status == "cancelled" and o.match_state != "cancelled":
            _unlink(s, ctx, o)
            o.match_state = "cancelled"
    # A matched order whose debit was deleted (txn_id set null) is open again.
    live = [o for o in orders if o.status == "delivered" and (o.match_state in OPEN or o.txn_id is None)]
    if not live:
        return
    days = [_day(o.placed_at) for o in live]
    claimed = select(MerchantOrder.txn_id).where(MerchantOrder.member_id == ctx.member_id,
                                                 MerchantOrder.match_state == "matched", MerchantOrder.txn_id.is_not(None))
    debits = s.execute(select(Txn.id, Txn.payee_key, Txn.occurred_at, Txn.amount).where(
        Txn.member_id == ctx.member_id, Txn.direction == "debit", Txn.split_of.is_(None),
        Txn.payee_key.in_([f"brand:{o.source}" for o in live]), ~Txn.sources.contains(["order"]),
        Txn.occurred_at.between(min(days), max(days) + timedelta(days=MATCH_LAG_DAYS)), Txn.id.not_in(claimed))).all()

    def fits(source: str, day: date, amount: Decimal) -> list[int]:
        return [d.id for d in debits if d.payee_key == f"brand:{source}" and d.amount == amount
                and 0 <= (d.occurred_at - day).days <= MATCH_LAG_DAYS]

    cands = {o.id: fits(o.source, _day(o.placed_at), o.bill_total) for o in live}
    uses: dict[int, int] = defaultdict(int)
    for c in cands.values():
        for d in set(c):
            uses[d] += 1
    to: dict[int, int] = {}  # order id -> debit id
    for o in live:
        c = cands[o.id]
        if len(c) == 1 and uses[c[0]] == 1:
            to[o.id] = c[0]
    taken = set(to.values())
    together: dict[tuple[str, datetime], list[MerchantOrder]] = defaultdict(list)
    for o in live:
        if not cands[o.id]:
            together[(o.source, o.placed_at.replace(second=0, microsecond=0))].append(o)
    for (source, at), group in together.items():
        if len(group) > 1:
            c = [d for d in fits(source, _day(at), sum((o.bill_total for o in group), Decimal(0))) if d not in taken]
            if len(c) == 1:
                taken.add(c[0])
                to.update({o.id: c[0] for o in group})
    cards = _cards(s, ctx.member_id)
    for o in live:
        card = next((cards[d] for d in reversed(LAST4.findall(o.payment or "")) if d in cards), None)
        if o.id in to:
            if o.match_state == "assigned":
                _unlink(s, ctx, o)
            o.txn_id, o.match_state = to[o.id], "matched"
        elif o.match_state == "assigned" and o.txn_id is not None:
            continue  # someone placed it, or its card did; only a one-to-one debit overrides that
        elif cands[o.id]:
            o.match_state = "ambiguous"
        elif card is not None:
            o.txn_id, o.match_state = _receipt_txn(s, ctx, o, card, CARD_RULE), "assigned"
        else:
            o.match_state = "unmatched"
    s.flush()


def _cards(s: Session, member_id: int) -> dict[str, int]:
    """Last 4 digits → account, for digits only one of the member's accounts has."""
    rows = s.execute(select(Account.id, Account.mask).where(Account.member_id == member_id, Account.mask.is_not(None))).all()
    seen: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        seen[r.mask[-4:]].append(r.id)
    return {m: ids[0] for m, ids in seen.items() if len(ids) == 1}


def _receipt_txn(s: Session, ctx: MemberContext, o: MerchantOrder, account_id: int, rule_id: str) -> int:
    """Two queries per order (category, insert): a backfill places a few dozen once, later runs one or two."""
    brand = _BRANDS[o.source]
    cat = category_by_ref(s, ctx, None, brand.category)
    day = _day(o.placed_at)
    t = Txn(member_id=ctx.member_id, account_id=account_id, occurred_at=day, posted_at=day, amount=o.bill_total,
            direction="debit", kind=cat.kind if cat else "spend", merchant_norm=brand.name, counterparty=o.store or brand.name,
            narration=f"{brand.name} order {o.order_no}" + (f" · {o.store}" if o.store else ""),
            payee_key=f"brand:{o.source}", category_id=cat.id if cat else None, bucket="excluded",
            classified_by="system", rule_id=rule_id, status="posted", sources=["order"],
            dedupe_key=sha256_hex(f"order|{o.source}|{o.order_no}"))
    s.add(t)
    s.flush()
    return t.id


def assign(s: Session, ctx: MemberContext, actor: str, keys: list[tuple[str, str]], account_id: int | None) -> dict[str, Any]:
    """Say which account paid for orders no debit matched (a meal card), or None to take them off. Orders a debit
    paid can't be assigned. Taking off an order whose receipt names a card puts it straight back on that card."""
    if account_id is not None and s.scalar(select(Account.id).where(Account.member_id == ctx.member_id,
                                                                    Account.id == account_id)) is None:
        raise NotFound("account not found")
    found = {(o.source, o.order_no): o for o in s.scalars(select(MerchantOrder).where(
        MerchantOrder.member_id == ctx.member_id, MerchantOrder.order_no.in_([k[1] for k in keys])))}
    missing = [k[1] for k in keys if k not in found]
    if missing:
        raise NotFound(f"no such order: {', '.join(missing[:5])}")
    for k in keys:
        o = found[k]
        if o.status == "cancelled":
            raise Invalid(f"order {o.order_no} was cancelled")
        if o.match_state == "matched":
            raise Invalid(f"order {o.order_no} is paid by txn {o.txn_id}")
    for k in keys:
        o = found[k]
        if account_id is None:
            _unlink(s, ctx, o)
        elif o.match_state == "assigned" and o.txn_id is not None:
            s.get(Txn, o.txn_id).account_id = account_id
        else:
            o.txn_id, o.match_state = _receipt_txn(s, ctx, o, account_id, ASSIGNED_RULE), "assigned"
    s.flush()
    rematch(s, ctx)  # an order taken off goes back to what the debits and its receipt say
    audit(s, ctx, actor, "orders.assign", None, {"orders": len(keys), "account_id": account_id})
    return {"orders": [{"source": found[k].source, "order_no": found[k].order_no, "match_state": found[k].match_state,
                        "txn_id": found[k].txn_id} for k in keys]}


def _out(o: MerchantOrder, items: list[MerchantOrderItem]) -> dict[str, Any]:
    return {"source": o.source, "order_no": o.order_no, "placed_at": o.placed_at, "status": o.status,
            "payment": o.payment, "store": o.store, "delivery_address": o.delivery_address,
            "address_label": o.address_label, "category": o.category, "item_total": fmt(o.item_total) if o.item_total is not None else None,
            "charges": o.charges, "bill_total": fmt(o.bill_total), "match_state": o.match_state,
            "items": [{"name": i.name, "unit": i.unit, "qty": i.qty, "note": i.note, "category": i.category,
                       "line_price": fmt(i.line_price) if i.line_price is not None else None,
                       "unit_price": fmt(i.unit_price) if i.unit_price is not None else None} for i in items]}


def for_txn(s: Session, member_id: int, txn_id: int) -> list[dict[str, Any]]:
    """The orders a txn paid for; two when one payment covered orders placed together."""
    orders = s.scalars(select(MerchantOrder).where(MerchantOrder.member_id == member_id, MerchantOrder.txn_id == txn_id)
                       .order_by(MerchantOrder.placed_at)).all()
    if not orders:
        return []
    items = defaultdict(list)
    for i in s.scalars(select(MerchantOrderItem).where(MerchantOrderItem.member_id == member_id,
                                                       MerchantOrderItem.order_id.in_([o.id for o in orders]))
                       .order_by(MerchantOrderItem.position)):
        items[i.order_id].append(i)
    return [_out(o, items[o.id]) for o in orders]


def search_items(s: Session, member_id: int, *, q: str | None, source: str | None, store: str | None,
                 category: str | None, date_from: date | None, date_to: date | None, page: int,
                 page_size: int) -> dict[str, Any]:
    """Items, not txns: totals add line prices, so "what did I spend on coffee" isn't the whole bill. Zomato items
    carry no price, so `priced` says how many of the matches the total covers. An item without its own category
    takes its order's (a Zomato order is eating out as a whole). Three queries: page, totals, per category."""
    from tijori.services.txns import _like

    cat = func.coalesce(MerchantOrderItem.category, MerchantOrder.category)
    conds = [MerchantOrderItem.member_id == member_id, MerchantOrder.status != "cancelled"]
    if q:
        p = _like(q)
        conds.append(or_(MerchantOrderItem.name.ilike(p, escape="\\"), MerchantOrderItem.note.ilike(p, escape="\\"),
                         MerchantOrder.store.ilike(p, escape="\\"), MerchantOrder.delivery_address.ilike(p, escape="\\"),
                         cat.ilike(p, escape="\\")))
    if category == "none":
        conds.append(cat.is_(None))
    elif category:
        conds.append(func.lower(cat) == category.lower())
    if source:
        conds.append(MerchantOrder.source == source)
    if store:
        conds.append(MerchantOrder.store.ilike(_like(store), escape="\\"))
    if date_from:
        conds.append(MerchantOrder.placed_at >= datetime.combine(date_from, datetime.min.time(), IST))
    if date_to:
        conds.append(MerchantOrder.placed_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), IST))
    base = select(MerchantOrderItem, MerchantOrder).join(MerchantOrder, MerchantOrder.id == MerchantOrderItem.order_id).where(*conds)
    total, amount, priced, n_orders = s.execute(
        select(func.count(), func.coalesce(func.sum(MerchantOrderItem.line_price), 0), func.count(MerchantOrderItem.line_price),
               func.count(func.distinct(MerchantOrder.id)))
        .select_from(MerchantOrderItem).join(MerchantOrder, MerchantOrder.id == MerchantOrderItem.order_id).where(*conds)).one()
    rows = s.execute(base.order_by(MerchantOrder.placed_at.desc(), MerchantOrderItem.position)
                     .offset((page - 1) * page_size).limit(page_size)).all()
    groups = s.execute(select(cat.label("c"), func.count(), func.coalesce(func.sum(MerchantOrderItem.line_price), 0))
                       .select_from(MerchantOrderItem).join(MerchantOrder, MerchantOrder.id == MerchantOrderItem.order_id)
                       .where(*conds).group_by(cat).order_by(func.coalesce(func.sum(MerchantOrderItem.line_price), 0).desc())).all()
    return {"items": [{"name": i.name, "unit": i.unit, "qty": i.qty, "note": i.note, "category": i.category or o.category,
                       "line_price": fmt(i.line_price) if i.line_price is not None else None,
                       "source": o.source, "order_no": o.order_no, "placed_at": o.placed_at, "store": o.store,
                       "delivery_address": o.delivery_address, "match_state": o.match_state, "txn_id": o.txn_id}
                      for i, o in rows],
            "page": page, "page_size": page_size, "total": total,
            "totals": {"line_price": fmt(amount), "priced": priced, "orders": n_orders,
                       "by_category": [{"category": c, "items": n, "line_price": fmt(a)} for c, n, a in groups]}}
