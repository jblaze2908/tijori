"""Loans: money lent to or borrowed from one person, and its repayments.

A loan's txns carry loan_id and are filed under the Loans category (bucket excluded), so they never count
as spend or income. What's owed is opening + money out − money in for a loan you lent, the reverse for one
you borrowed. Writing a loan off adds one txn for what's left, under the person's category, so the loss
(or the forgiven debt) lands in spend (or income) once. Per call: the list is three grouped queries; one
loan is three queries (loan, its txns, suggestions).
"""

import hashlib
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from tijori.classify.taxonomy import EXPENSE_BUCKETS, bucket_kind
from tijori.db import MemberContext
from tijori.models import Category, Loan, Member, Txn
from tijori.money import ZERO, fmt
from tijori.services.common import audit, category_by_ref, today_ist, txn_out, txn_query
from tijori.services.errors import Invalid, NotFound

LOANS = "Loans"
MAX_TXNS = 200
SUGGEST_LIMIT = 10


def _outstanding(direction: str, opening: Decimal, out: Decimal, back: Decimal) -> Decimal:
    return opening + out - back if direction == "lent" else opening + back - out


def _sums(s: Session, member_id: int) -> dict[int, dict[str, Any]]:
    rows = s.execute(select(Txn.loan_id, Txn.direction, func.sum(Txn.amount), func.count(), func.max(Txn.occurred_at))
                     .where(Txn.member_id == member_id, Txn.loan_id.is_not(None))
                     .group_by(Txn.loan_id, Txn.direction)).all()
    out: dict[int, dict[str, Any]] = {}
    for loan_id, direction, amount, n, last in rows:
        d = out.setdefault(loan_id, {"debit": (ZERO, 0), "credit": (ZERO, 0), "last": None})
        d[direction] = (amount, n)
        d["last"] = max(filter(None, (d["last"], last)))
    return out


def _item(loan: Loan, sums: dict[str, Any] | None) -> dict[str, Any]:
    sums = sums or {"debit": (ZERO, 0), "credit": (ZERO, 0), "last": None}
    (out, n_out), (back, n_back) = sums["debit"], sums["credit"]
    lent = loan.direction == "lent"
    given = loan.opening_amount + (out if lent else back)
    returned, n_returned = (back, n_back) if lent else (out, n_out)
    owed = _outstanding(loan.direction, loan.opening_amount, out, back) if loan.status == "open" else ZERO
    return {"id": loan.id, "direction": loan.direction, "counterparty": loan.counterparty, "payee_key": loan.payee_key,
            "started_on": loan.started_on, "opening_amount": fmt(loan.opening_amount), "note": loan.note,
            "status": loan.status, "closed_on": loan.closed_on, "given": fmt(given), "returned": fmt(returned),
            "outstanding": fmt(owed), "repayments": n_returned, "last_at": sums["last"]}


def _fy_start(today: date) -> date:
    return date(today.year if today.month >= 4 else today.year - 1, 4, 1)


def list_loans(s: Session, member_id: int, today: date | None = None) -> dict[str, Any]:
    today = today or today_ist()
    loans = s.scalars(select(Loan).where(Loan.member_id == member_id)).all()
    sums = _sums(s, member_id)
    items = [_item(ln, sums.get(ln.id)) for ln in loans]
    open_ = [i for i in items if i["status"] == "open"]
    closed = sorted((i for i in items if i["status"] != "open"), key=lambda i: i["closed_on"] or date.min, reverse=True)
    open_.sort(key=lambda i: Decimal(i["outstanding"]), reverse=True)
    fy = _fy_start(today)
    repaid_fy = s.execute(select(func.coalesce(func.sum(Txn.amount), 0), func.count())
                          .join(Loan, Loan.id == Txn.loan_id)
                          .where(Txn.member_id == member_id, Loan.direction == "lent", Txn.direction == "credit",
                                 Txn.occurred_at >= fy)).one()
    loans_cat = _category(s, member_id)
    unassigned = [txn_out(r) for r in s.execute(
        txn_query().where(Txn.member_id == member_id, Txn.category_id == loans_cat.id, Txn.loan_id.is_(None))
        .order_by(Txn.occurred_at.desc()).limit(20))] if loans_cat else []
    owed = sum((Decimal(i["outstanding"]) for i in open_ if i["direction"] == "lent"), ZERO)
    owe = sum((Decimal(i["outstanding"]) for i in open_ if i["direction"] == "borrowed"), ZERO)
    return {"items": open_ + closed, "unassigned": unassigned,
            "totals": {"owed_to_you": fmt(owed), "you_owe": fmt(owe),
                       "open_lent": sum(1 for i in open_ if i["direction"] == "lent"),
                       "open_borrowed": sum(1 for i in open_ if i["direction"] == "borrowed"),
                       "repaid_fy": fmt(repaid_fy[0]), "repaid_fy_count": repaid_fy[1], "fy_start": fy}}


def balances(s: Session, member_id: int) -> tuple[Decimal, Decimal]:
    """(owed to you, you owe) over open loans, for net worth."""
    t = list_loans(s, member_id)["totals"]
    return Decimal(t["owed_to_you"]), Decimal(t["you_owe"])


def _loan(s: Session, member_id: int, loan_id: int) -> Loan:
    loan = s.scalars(select(Loan).where(Loan.member_id == member_id, Loan.id == loan_id)).first()
    if loan is None:
        raise NotFound("loan not found")
    return loan


def _category(s: Session, member_id: int) -> Category | None:
    return s.scalars(select(Category).join(Member, Member.household_id == Category.household_id)
                     .where(Member.id == member_id, Category.name == LOANS,
                            or_(Category.member_id.is_(None), Category.member_id == member_id))
                     .order_by(Category.member_id.is_(None)).limit(1)).first()


def _payee_keys(s: Session, loan: Loan) -> set[str]:
    keys = set(s.scalars(select(Txn.payee_key).where(Txn.member_id == loan.member_id, Txn.loan_id == loan.id,
                                                     Txn.payee_key.is_not(None)).distinct()))
    return keys | ({loan.payee_key} if loan.payee_key else set())


def _unfiled_from(s: Session, member_id: int, keys: set[str], since: date, exclude: set[int] = frozenset()) -> list[Any]:
    """Payments from these handles that aren't in a loan yet: repayment suggestions."""
    if not keys:
        return []
    part = aliased(Txn)
    return [txn_out(r) for r in s.execute(
        txn_query().where(Txn.member_id == member_id, Txn.payee_key.in_(keys), Txn.loan_id.is_(None),
                          Txn.occurred_at >= since - timedelta(days=7), Txn.status != "flagged",
                          Txn.id.not_in(exclude or {0}), Txn.split_of.is_(None),
                          ~exists().where(part.split_of == Txn.id))
        .order_by(Txn.occurred_at.desc()).limit(SUGGEST_LIMIT))]


def get_loan(s: Session, member_id: int, loan_id: int) -> dict[str, Any]:
    loan = _loan(s, member_id, loan_id)
    rows = s.execute(txn_query().where(Txn.member_id == member_id, Txn.loan_id == loan.id)
                     .order_by(Txn.occurred_at, Txn.id)).all()
    bal, timeline = loan.opening_amount, []
    for r in rows:
        t = r.Txn
        bal = _outstanding(loan.direction, bal, t.amount if t.direction == "debit" else ZERO,
                           t.amount if t.direction == "credit" else ZERO)
        timeline.append({**txn_out(r), "balance_after": fmt(bal)})
    sums = _sums(s, member_id).get(loan.id)
    return {"loan": _item(loan, sums), "txns": list(reversed(timeline)),
            "suggestions": _unfiled_from(s, member_id, _payee_keys(s, loan), loan.started_on)}


def for_txn(s: Session, member_id: int, txn_id: int) -> dict[str, Any]:
    """What the Loans picker offers for one txn: open loans (same handle first) and the handle's other payments."""
    t = s.scalars(select(Txn).where(Txn.member_id == member_id, Txn.id == txn_id)).first()
    if t is None:
        raise NotFound("transaction not found")
    sums = _sums(s, member_id)
    loans = s.scalars(select(Loan).where(Loan.member_id == member_id, Loan.status == "open")).all()
    keyed = {ln.id: _payee_keys(s, ln) for ln in loans} if t.payee_key else {}
    items = [{**_item(ln, sums.get(ln.id)), "same_payee": t.payee_key in keyed.get(ln.id, set())} for ln in loans]
    items.sort(key=lambda i: (not i["same_payee"], -Decimal(i["outstanding"])))
    others = _unfiled_from(s, member_id, {t.payee_key} if t.payee_key else set(), date.min + timedelta(days=7),
                           exclude={t.id})
    return {"txn_id": t.id, "loan_id": t.loan_id, "new_direction": "lent" if t.direction == "debit" else "borrowed",
            "counterparty": t.counterparty or t.merchant_norm, "loans": items, "other_txns": others}


def _txns(s: Session, member_id: int, txn_ids: list[int]) -> list[Txn]:
    if not txn_ids or len(txn_ids) > MAX_TXNS:
        raise Invalid(f"give 1 to {MAX_TXNS} transactions")
    part = aliased(Txn)
    rows = s.scalars(select(Txn).where(Txn.member_id == member_id, Txn.id.in_(txn_ids),
                                       ~exists().where(part.split_of == Txn.id))).all()
    if len(rows) != len(set(txn_ids)):
        raise NotFound("some transactions weren't found, or are split into parts")
    return sorted(rows, key=lambda t: (t.occurred_at, t.id))


def attach(s: Session, ctx: MemberContext, actor: str, loan_id: int, txn_ids: list[int]) -> dict[str, Any]:
    loan = _loan(s, ctx.member_id, loan_id)
    rows = _txns(s, ctx.member_id, txn_ids)
    cat = _category(s, ctx.member_id)
    if cat is None:
        raise Invalid("the Loans category is missing")
    s.execute(update(Txn).where(Txn.member_id == ctx.member_id, Txn.id.in_([t.id for t in rows])).values(
        loan_id=loan.id, category_id=cat.id, bucket=cat.bucket, kind=cat.kind, classified_by="user",
        rule_id="user", review_reason=None, updated_at=func.now()))
    if loan.payee_key is None:
        loan.payee_key = next((t.payee_key for t in rows if t.payee_key), None)
    audit(s, ctx, actor, "loan.attach", f"loan:{loan.id}", {"txns": len(rows)})
    return {"loan_id": loan.id, "attached": len(rows)}


def detach(s: Session, ctx: MemberContext, actor: str, loan_id: int, txn_ids: list[int]) -> dict[str, Any]:
    """Back to the Inbox, uncategorized, so the payment can be filed again."""
    loan = _loan(s, ctx.member_id, loan_id)
    n = 0
    for direction, kind in (("debit", "spend"), ("credit", "income")):
        n += s.execute(update(Txn).where(Txn.member_id == ctx.member_id, Txn.loan_id == loan.id, Txn.id.in_(txn_ids),
                                         Txn.direction == direction).values(
            loan_id=None, category_id=None, bucket=None, kind=kind, classified_by=None, rule_id="loan:removed",
            review_reason="loan_removed", updated_at=func.now())).rowcount
    audit(s, ctx, actor, "loan.detach", f"loan:{loan.id}", {"txns": n})
    return {"loan_id": loan.id, "removed": n}


def create(s: Session, ctx: MemberContext, actor: str, *, direction: str | None, counterparty: str | None,
           started_on: date | None, opening_amount: Decimal, note: str | None, txn_ids: list[int]) -> dict[str, Any]:
    rows = _txns(s, ctx.member_id, txn_ids) if txn_ids else []
    first = rows[0] if rows else None
    direction = direction or (("lent" if first.direction == "debit" else "borrowed") if first else None)
    counterparty = (counterparty or (first and (first.counterparty or first.merchant_norm)) or "").strip()[:120]
    started_on = started_on or (first.occurred_at if first else None)
    if direction not in ("lent", "borrowed") or not counterparty or started_on is None:
        raise Invalid("a loan needs a direction, a person and a start date (or a transaction to take them from)")
    loan = Loan(member_id=ctx.member_id, direction=direction, counterparty=counterparty, started_on=started_on,
                opening_amount=opening_amount, note=(note or "").strip() or None,
                payee_key=next((t.payee_key for t in rows if t.payee_key), None))
    s.add(loan)
    s.flush()
    audit(s, ctx, actor, "loan.create", f"loan:{loan.id}", {"direction": direction, "txns": len(rows)})
    if rows:
        attach(s, ctx, actor, loan.id, [t.id for t in rows])
    return _item(loan, _sums(s, ctx.member_id).get(loan.id))


def update_loan(s: Session, ctx: MemberContext, actor: str, loan_id: int, fields: dict[str, Any]) -> dict[str, Any]:
    loan = _loan(s, ctx.member_id, loan_id)
    if "counterparty" in fields:
        name = (fields["counterparty"] or "").strip()[:120]
        if not name:
            raise Invalid("a loan needs a person")
        loan.counterparty = name
    if "note" in fields:
        loan.note = (fields["note"] or "").strip() or None
    if "opening_amount" in fields:
        loan.opening_amount = fields["opening_amount"]
    if "started_on" in fields:
        loan.started_on = fields["started_on"]
    audit(s, ctx, actor, "loan.update", f"loan:{loan.id}", {"fields": sorted(fields)})
    s.flush()
    return _item(loan, _sums(s, ctx.member_id).get(loan.id))


def _writeoff_key(loan_id: int) -> str:
    return hashlib.sha256(f"loan-writeoff|{loan_id}".encode()).hexdigest()


def close(s: Session, ctx: MemberContext, actor: str, loan_id: int, status: str,
          category_id: int | None = None) -> dict[str, Any]:
    """settled: closed as is. written_off: what's left becomes one txn under category_id (a spend category
    for money lent; money you owed and were let off becomes a credit, so a people category counts it as
    income). open: reopens, removing any write-off txn."""
    loan = _loan(s, ctx.member_id, loan_id)
    s.execute(delete(Txn).where(Txn.member_id == ctx.member_id, Txn.dedupe_key == _writeoff_key(loan.id)))
    if status == "written_off":
        cat = category_by_ref(s, ctx, category_id, None) if category_id else None
        if cat is None or cat.bucket not in EXPENSE_BUCKETS:
            raise Invalid("write a loan off to a spend category, e.g. Friends")
        left = Decimal(_item(loan, _sums(s, ctx.member_id).get(loan.id))["outstanding"])
        if left > 0:
            direction = "debit" if loan.direction == "lent" else "credit"
            bucket, kind = bucket_kind(cat.bucket, cat.kind, cat.credit_bucket, direction)
            s.add(Txn(member_id=ctx.member_id, occurred_at=today_ist(), amount=left, direction=direction, kind=kind,
                      merchant_norm=loan.counterparty[:120], counterparty=loan.counterparty,
                      narration=f"Loan written off: {loan.counterparty}", category_id=cat.id, bucket=bucket,
                      classified_by="user", rule_id=f"loan:{loan.id}", status="posted", sources=[],
                      dedupe_key=_writeoff_key(loan.id)))
    loan.status = status
    loan.closed_on = None if status == "open" else today_ist()
    audit(s, ctx, actor, f"loan.{status}", f"loan:{loan.id}", {"category_id": category_id})
    s.flush()
    return _item(loan, _sums(s, ctx.member_id).get(loan.id))


def auto_attach(s: Session, member_id: int) -> int:
    """Payee memory files a repeat payer under Loans; attach those txns to the payer's one open loan.
    One UPDATE per poll; a handle with two open loans is left for the member to pick."""
    cat = _category(s, member_id)
    if cat is None:
        return 0
    only = (select(func.count()).select_from(Loan)
            .where(Loan.member_id == member_id, Loan.status == "open", Loan.payee_key == Txn.payee_key)
            .scalar_subquery())
    target = (select(Loan.id).where(Loan.member_id == member_id, Loan.status == "open",
                                    Loan.payee_key == Txn.payee_key).limit(1).scalar_subquery())
    return s.execute(update(Txn).where(
        Txn.member_id == member_id, Txn.category_id == cat.id, Txn.loan_id.is_(None), Txn.payee_key.is_not(None),
        and_(only == 1)).values(loan_id=target)).rowcount


def month_lines(s: Session, member_id: int, start: date, end: date) -> dict[str, Any]:
    """Loan money in one month cycle, for Spending's lines outside the total. One grouped query."""
    rows = s.execute(select(Loan.direction, Txn.direction, func.sum(Txn.amount), func.count(),
                            func.array_agg(func.distinct(Loan.counterparty)))
                     .join(Loan, Loan.id == Txn.loan_id)
                     .where(Txn.member_id == member_id, Txn.occurred_at >= start, Txn.occurred_at < end)
                     .group_by(Loan.direction, Txn.direction)).all()
    names = {("lent", "debit"): "lent", ("lent", "credit"): "repaid_to_you",
             ("borrowed", "credit"): "borrowed", ("borrowed", "debit"): "repaid_by_you"}
    out = {v: {"amount": "0.00", "count": 0, "people": []} for v in names.values()}
    for loan_dir, txn_dir, amount, n, people in rows:
        out[names[(loan_dir, txn_dir)]] = {"amount": fmt(amount), "count": n, "people": sorted(people)[:3]}
    return out
