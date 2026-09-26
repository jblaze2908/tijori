"""Statement ingest: raw store → parse → resolve → classify → reconcile (PLAN §7.1–7.5), run
synchronously for uploads inside the caller's member-scoped session."""

import hashlib
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tijori.classify.engine import Classifier, Rule, TxnInput
from tijori.classify.kinds import MemberProfile
from tijori.classify.memory import PayeeMemory, memory_key
from tijori.classify.narration import parse_narration
from tijori.db import MemberContext
from tijori.models import (
    Account,
    Category,
    Member,
    Observation,
    RawAttachment,
    RawMessage,
    RuleHit,
    Statement,
    Txn,
    TxnObservation,
)
from tijori.models import Rule as RuleRow
from tijori.money import fmt
from tijori.parsers import Observation as Line
from tijori.parsers import Statement as ParsedStatement
from tijori.parsers import reconcile
from tijori.services import cards
from tijori.services.common import account_ref, audit


def line_dedupe_key(account_id: int, line: Line, occurrence: int) -> str:
    """Identity of one statement line. Shared by uploads and the legacy import, so re-sending a
    statement never duplicates a txn."""
    raw = (f"line|{account_id}|{line.occurred_at.isoformat()}|{line.amount}|{line.direction}|"
           f"{line.balance_after}|{line.ref_no or ''}|{occurrence}")
    return hashlib.sha256(raw.encode()).hexdigest()


def line_keys(account_id: int, lines: tuple[Line, ...] | list[Line]) -> list[str]:
    seen: Counter[tuple[Any, ...]] = Counter()
    keys = []
    for o in lines:
        base = (o.occurred_at, o.amount, o.direction, o.balance_after, o.ref_no)
        seen[base] += 1
        keys.append(line_dedupe_key(account_id, o, seen[base]))
    return keys


def ensure_account(s: Session, member_id: int, institution: str, mask: str | None, kind: str = "bank") -> Account:
    """Match on (institution, kind, mask); adopt a mask-less account of the same bank and kind (e.g. from
    the legacy import or setup) before creating a new one."""
    q = select(Account).where(Account.member_id == member_id, Account.institution == institution,
                              Account.kind == kind)
    acct = s.scalars(q.where(Account.mask.is_not_distinct_from(mask)).order_by(Account.id).limit(1)).first()
    if acct is None and mask is not None:
        acct = s.scalars(q.where(Account.mask.is_(None)).order_by(Account.id).limit(1)).first()
        if acct is not None:
            acct.mask = mask
    if acct is None:
        acct = Account(member_id=member_id, kind=kind, institution=institution,
                       name=f"{institution} {'card' if kind == 'card' else 'savings'}", mask=mask)
        s.add(acct)
    s.flush()
    return acct


def category_ids(s: Session, household_id: int) -> dict[str, int]:
    rows = s.execute(select(Category.name, Category.id)
                     .where(Category.household_id == household_id, Category.member_id.is_(None))).all()
    return {name: cid for name, cid in rows}


def load_classifier(s: Session, ctx: MemberContext) -> Classifier:
    """Three queries per batch (profile, rules, grouped payee memory), never per txn."""
    cfg = s.scalar(select(Member.classify_config).where(Member.id == ctx.member_id)) or {}
    member_rules, household_rules = [], []
    rows = s.execute(
        select(RuleRow.id, RuleRow.scope, RuleRow.match_json, RuleRow.kind, RuleRow.priority, Category.name)
        .join(Category, Category.id == RuleRow.category_id)
        .where(RuleRow.enabled, RuleRow.household_id == ctx.household_id,
               (RuleRow.member_id.is_(None)) | (RuleRow.member_id == ctx.member_id))
    ).all()
    for r in rows:
        try:
            rule = Rule.from_match_json(r.id, r.name, r.match_json, scope=r.scope, kind=r.kind, priority=r.priority)
        except ValueError:
            continue  # a rule on a custom category or with a bad pattern never blocks ingest
        (member_rules if r.scope == "member" else household_rules).append(rule)
    counts = s.execute(
        select(Txn.direction, Txn.payee_key, Category.name, func.count())
        .join(Category, Category.id == Txn.category_id)
        .where(Txn.member_id == ctx.member_id, Txn.payee_key.is_not(None),
               Txn.classified_by.in_(("user", "system")))
        .group_by(Txn.direction, Txn.payee_key, Category.name)
    ).all()
    memory = PayeeMemory.from_counts((memory_key(d, k), name, n) for d, k, name, n in counts)
    return Classifier(MemberProfile.from_json(cfg), member_rules, household_rules, memory)


def record_raw(s: Session, ctx: MemberContext, *, filename: str | None, sha256: str, blob_ref: str,
               parse_status: str) -> tuple[RawMessage, RawAttachment]:
    msg = RawMessage(member_id=ctx.member_id, received_at=datetime.now(UTC), sender="upload", subject=filename,
                     sha256=sha256, blob_ref=blob_ref, parse_status=parse_status)
    s.add(msg)
    s.flush()
    att = RawAttachment(member_id=ctx.member_id, raw_message_id=msg.id, filename=filename, sha256=sha256,
                        blob_ref=blob_ref)
    s.add(att)
    s.flush()
    return msg, att


def find_duplicate(s: Session, member_id: int, sha256: str) -> dict[str, Any] | None:
    row = s.execute(
        select(RawMessage.id, RawMessage.parse_status, Statement.id.label("statement_id"))
        .outerjoin(RawAttachment, RawAttachment.raw_message_id == RawMessage.id)
        .outerjoin(Statement, Statement.raw_attachment_id == RawAttachment.id)
        .where(RawMessage.member_id == member_id, RawMessage.sha256 == sha256)
    ).first()
    if row is None:
        return None
    return {"raw_message_id": row.id, "parse_status": row.parse_status, "statement_id": row.statement_id}


def reconciliation_out(st: ParsedStatement) -> dict[str, Any]:
    rec = reconcile(st)
    return {
        "ok": rec.ok, "line_count": rec.line_count, "debit_count": rec.debit_count,
        "credit_count": rec.credit_count, "total_debits": fmt(rec.total_debits),
        "total_credits": fmt(rec.total_credits), "closing_diff": fmt(rec.closing_diff),
        "chain_breaks": [{"index": b.index, "occurred_at": b.occurred_at, "expected_balance": fmt(b.expected_balance),
                          "printed_balance": fmt(b.printed_balance)} for b in rec.chain_breaks],
        "footer_mismatches": list(rec.footer_mismatches),
    }


def ingest_statement(s: Session, ctx: MemberContext, actor: str, st: ParsedStatement, *, filename: str | None,
                     sha256: str, blob_ref: str) -> dict[str, Any]:
    if not st.lines:
        raise ValueError("statement has no lines")
    rec = reconcile(st)
    msg, att = record_raw(s, ctx, filename=filename, sha256=sha256, blob_ref=blob_ref, parse_status="parsed")
    account = ensure_account(s, ctx.member_id, st.institution, st.account_mask, st.account_kind)
    values = dict(member_id=ctx.member_id, account_id=account.id, period_start=st.period_start,
                  period_end=st.period_end, opening=st.summary.opening, closing=st.summary.closing,
                  raw_attachment_id=att.id, parser=st.parser, parser_version=st.parser_version,
                  reconciled_at=datetime.now(UTC) if rec.ok else None, diff=rec.closing_diff,
                  total_due=st.total_due, due_date=st.due_date)
    stmt = pg_insert(Statement).values(**values)
    statement_id = s.scalar(stmt.on_conflict_do_update(
        constraint="uq_statement_member_id_account_id_period_start_period_end",
        set_={k: stmt.excluded[k] for k in ("raw_attachment_id", "parser", "parser_version", "opening", "closing",
                                             "reconciled_at", "diff", "total_due", "due_date")},
    ).returning(Statement.id))

    # Batched INSERT … RETURNING in input order: one round-trip for all lines.
    obs_ids = list(s.scalars(insert(Observation).returning(Observation.id, sort_by_parameter_order=True), [
        dict(member_id=ctx.member_id, raw_message_id=msg.id, parser=st.parser, parser_version=st.parser_version,
             account_id=account.id, occurred_at=o.occurred_at, amount=o.amount, direction=o.direction,
             counterparty=parse_narration(o.narration).payee, ref_no=o.ref_no, balance_after=o.balance_after,
             confidence=o.confidence,
             payload_json={"narration": o.narration, **{k: v for k, v in o.payload.items() if v is not None},
                           "value_date": o.value_date.isoformat() if o.value_date else None})
        for o in st.lines
    ]))

    keys = line_keys(account.id, st.lines)
    existing = dict(s.execute(select(Txn.dedupe_key, Txn.id)
                              .where(Txn.member_id == ctx.member_id, Txn.dedupe_key.in_(keys))).all())
    decisions = load_classifier(s, ctx).classify_batch(
        [TxnInput(o.occurred_at, o.amount, o.direction, o.narration, account.id, o.ref_no)  # type: ignore[arg-type]
         for o in st.lines])
    cat_ids = category_ids(s, ctx.household_id)
    status = "reconciled" if rec.ok else "posted"

    new_idx = [i for i, k in enumerate(keys) if k not in existing]
    txn_ids: dict[int, int] = {}
    if new_idx:
        rows = [
            dict(member_id=ctx.member_id, account_id=account.id, occurred_at=o.occurred_at, posted_at=o.value_date,
                 amount=o.amount, direction=o.direction, kind=d.kind, merchant_norm=d.merchant[:120],
                 counterparty=parse_narration(o.narration).payee, ref_no=o.ref_no, narration=o.narration,
                 vpa=d.vpa, payee_key=d.payee_key[:80],
                 category_id=cat_ids.get(d.category) if d.category else None, bucket=d.bucket,
                 classified_by=d.classified_by, rule_id=d.rule_id, review_reason=d.inbox_reason, status=status,
                 sources=["statement"], dedupe_key=keys[i])
            for i in new_idx for o, d in [(st.lines[i], decisions[i])]
        ]
        ids = s.scalars(insert(Txn).returning(Txn.id, sort_by_parameter_order=True), rows).all()
        txn_ids = dict(zip(new_idx, ids))
    matched = [existing[k] for k in keys if k in existing]
    if matched:
        s.execute(
            update(Txn).where(Txn.member_id == ctx.member_id, Txn.id.in_(matched))
            .values(sources=case((Txn.sources.contains(["statement"]), Txn.sources),
                                 else_=func.array_append(Txn.sources, "statement")),
                    status="reconciled" if rec.ok else Txn.status, updated_at=func.now())
        )
    s.execute(pg_insert(TxnObservation).on_conflict_do_nothing(), [
        dict(txn_id=txn_ids.get(i) or existing[keys[i]], observation_id=obs_ids[i], member_id=ctx.member_id)
        for i in range(len(st.lines))
    ])
    hits = [dict(rule_id=int(d.rule_id[5:]), txn_id=txn_ids[i], member_id=ctx.member_id)
            for i, d in enumerate(decisions) if i in txn_ids and d.rule_id.startswith("rule:") and d.rule_id[5:].isdigit()]
    if hits:
        s.execute(pg_insert(RuleHit).on_conflict_do_nothing(), hits)
    created = [decisions[i] for i in txn_ids]
    linked = cards.link_card_payments(s, ctx.member_id)
    audit(s, ctx, actor, "statement.upload", f"statement:{statement_id}",
          {"parser": st.parser, "lines": len(st.lines), "created": len(created), "ok": rec.ok,
           "card_payments_linked": linked})
    return {
        "statement_id": statement_id, "raw_message_id": msg.id, "duplicate": False,
        "parser": st.parser, "parser_version": st.parser_version, "institution": st.institution,
        "account": account_ref(account.id, account.institution, account.name, account.kind, account.mask),
        "period_start": st.period_start, "period_end": st.period_end,
        "opening": fmt(st.summary.opening), "closing": fmt(st.summary.closing),
        "reconciliation": reconciliation_out(st),
        "txns": {"lines": len(st.lines), "created": len(created), "matched_existing": len(matched),
                 "filed": sum(1 for d in created if d.filed), "inbox": sum(1 for d in created if not d.filed)},
    }
