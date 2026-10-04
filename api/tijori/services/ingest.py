"""Statement ingest: raw store → parse → resolve → classify → reconcile (PLAN §7.1–7.5), run
synchronously for uploads inside the caller's member-scoped session."""

import hashlib
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tijori.classify.engine import Classifier, Rule, TxnInput
from tijori.classify.kinds import MemberProfile
from tijori.classify.memory import PayeeMemory, memory_key
from tijori.classify.narration import parse_narration
from tijori.classify.taxonomy import UNRECALLED, CategoryDef
from tijori.db import MemberContext
from tijori.models import (
    Account,
    Category,
    ComponentValue,
    Member,
    Observation,
    PayeeAlias,
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
from tijori.services import cards, txn_edit
from tijori.services.common import account_ref, audit
from tijori.services.errors import Invalid


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


def ensure_account(s: Session, member_id: int, institution: str, mask: str | None, kind: str = "bank",
                   name: str | None = None) -> Account:
    """Match on (institution, kind, mask); adopt a mask-less account of the same bank and kind (e.g. from
    the legacy import or setup) before creating a new one. A statement's product name ("HDFC Platinum")
    replaces the generic name an alert gave the account."""
    q = select(Account).where(Account.member_id == member_id, Account.institution == institution,
                              Account.kind == kind)
    acct = s.scalars(q.where(Account.mask.is_not_distinct_from(mask)).order_by(Account.id).limit(1)).first()
    if acct is None and mask is not None:
        acct = s.scalars(q.where(Account.mask.is_(None)).order_by(Account.id).limit(1)).first()
        if acct is not None:
            acct.mask = mask
    generic = f"{institution} {'card' if kind == 'card' else 'savings'}"
    if acct is None:
        acct = Account(member_id=member_id, kind=kind, institution=institution, name=name or generic, mask=mask)
        s.add(acct)
    elif name and acct.name in (None, generic) and name != acct.name:
        acct.name = name
    s.flush()
    return acct


def category_ids(s: Session, household_id: int) -> dict[str, int]:
    rows = s.execute(select(Category.name, Category.id)
                     .where(Category.household_id == household_id, Category.member_id.is_(None))).all()
    return {name: cid for name, cid in rows}


def load_classifier(s: Session, ctx: MemberContext) -> Classifier:
    """Five queries per batch (profile, categories, rules, grouped payee memory, aliases), never per txn."""
    cfg = s.scalar(select(Member.classify_config).where(Member.id == ctx.member_id)) or {}
    mine = (Category.member_id.is_(None)) | (Category.member_id == ctx.member_id)
    categories = {r.name: CategoryDef(r.name, r.kind, r.bucket, "", r.credit_bucket) for r in s.execute(
        select(Category.name, Category.kind, Category.bucket, Category.credit_bucket)
        .where(Category.household_id == ctx.household_id, mine)
        .order_by(Category.member_id.is_(None).desc()))}  # a member's own category wins over the household's
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
            continue  # a rule with a bad pattern never blocks ingest
        (member_rules if r.scope == "member" else household_rules).append(rule)
    counts = s.execute(
        select(Txn.direction, Txn.payee_key, Category.name, func.count())
        .join(Category, Category.id == Txn.category_id)
        .where(Txn.member_id == ctx.member_id, Txn.payee_key.is_not(None),
               Txn.classified_by.in_(("user", "system")), Category.name != UNRECALLED)
        .group_by(Txn.direction, Txn.payee_key, Category.name)
    ).all()
    memory = PayeeMemory.from_counts((memory_key(d, k), name, n) for d, k, name, n in counts)
    aliases = dict(s.execute(select(PayeeAlias.payee_key, PayeeAlias.name)
                             .where(PayeeAlias.member_id == ctx.member_id)).tuples().all())
    return Classifier(MemberProfile.from_json(cfg), member_rules, household_rules, memory, categories, aliases)


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


ALERT_DAYS = 3  # an alert carries the transaction date; the statement may post it up to this much later


def _soft_matches(s: Session, member_id: int, account_id: int, lines: tuple[Line, ...], keys: list[str],
                  existing: dict[str, int]) -> tuple[dict[int, int], set[int]]:
    """Statement lines with a new key whose txn already exists. The same line from another statement format
    (a netbanking download vs the emailed PDF) matches on date, amount and direction. A txn first seen in an
    alert matches within ±3 days, closest date first. One query over the account's txns in the window."""
    todo = [i for i, k in enumerate(keys) if k not in existing]
    if not todo:
        return {}, set()
    lo = min(lines[i].occurred_at for i in todo) - timedelta(days=ALERT_DAYS)
    hi = max(lines[i].occurred_at for i in todo) + timedelta(days=ALERT_DAYS)
    taken = set(existing.values())
    pool = [r for r in s.execute(
        select(Txn.id, Txn.occurred_at, Txn.amount, Txn.direction, Txn.sources)
        .where(Txn.member_id == member_id, Txn.account_id == account_id, Txn.occurred_at.between(lo, hi))
        .order_by(Txn.occurred_at, Txn.id)).all() if r.id not in taken]
    out: dict[int, int] = {}
    alert_only: set[int] = set()
    for i in todo:
        o = lines[i]
        same = next((r for r in pool if r.occurred_at == o.occurred_at and r.amount == o.amount
                     and r.direction == o.direction), None)
        if same is None:
            near = [r for r in pool if set(r.sources or []) == {"alert"} and r.amount == o.amount
                    and r.direction == o.direction and abs((r.occurred_at - o.occurred_at).days) <= ALERT_DAYS]
            same = min(near, key=lambda r: abs((r.occurred_at - o.occurred_at).days), default=None)
            if same is not None:
                alert_only.add(same.id)
        if same is not None:
            out[i] = same.id
            pool.remove(same)
    return out, alert_only


def ingest_statement(s: Session, ctx: MemberContext, actor: str, st: ParsedStatement, *, filename: str | None,
                     sha256: str, blob_ref: str, raw: tuple[RawMessage, RawAttachment] | None = None) -> dict[str, Any]:
    """`raw` is the collector's email and attachment; an upload records its own. A statement with no lines is
    kept only when its printed totals reconcile: it is then the proof that the period was empty."""
    rec = reconcile(st)
    if not st.lines and not rec.ok:
        raise Invalid("statement has no lines, and its totals don't show an empty period")
    msg, att = raw or record_raw(s, ctx, filename=filename, sha256=sha256, blob_ref=blob_ref, parse_status="parsed")
    account = ensure_account(s, ctx.member_id, st.institution, st.account_mask, st.account_kind, st.account_name)
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
    ])) if st.lines else []  # an empty parameter list would run one bare INSERT

    keys = line_keys(account.id, st.lines)
    existing = dict(s.execute(select(Txn.dedupe_key, Txn.id)
                              .where(Txn.member_id == ctx.member_id, Txn.dedupe_key.in_(keys))).all())
    soft, alert_only = _soft_matches(s, ctx.member_id, account.id, st.lines, keys, existing)
    for i, txn_id in soft.items():
        existing[keys[i]] = txn_id
        if txn_id in alert_only:  # the statement is the record: its date, narration and key replace the alert's
            o = st.lines[i]
            s.execute(update(Txn).where(Txn.member_id == ctx.member_id, Txn.id == txn_id).values(
                occurred_at=o.occurred_at, posted_at=o.value_date, narration=o.narration, ref_no=o.ref_no,
                dedupe_key=keys[i]))
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
    if st.lines:
        s.execute(pg_insert(TxnObservation).on_conflict_do_nothing(), [
            dict(txn_id=txn_ids.get(i) or existing[keys[i]], observation_id=obs_ids[i], member_id=ctx.member_id)
            for i in range(len(st.lines))
        ])
    hits = [dict(rule_id=int(d.rule_id[5:]), txn_id=txn_ids[i], member_id=ctx.member_id)
            for i, d in enumerate(decisions) if i in txn_ids and d.rule_id.startswith("rule:") and d.rule_id[5:].isdigit()]
    if hits:
        s.execute(pg_insert(RuleHit).on_conflict_do_nothing(), hits)
    created = [decisions[i] for i in txn_ids]
    # An alert the statement doesn't carry (a card hold, a declined or reversed payment) leaves the totals.
    # The period's first and last 3 days are left alone: those lines can post into the neighbouring statement.
    s.execute(update(Txn).where(
        Txn.member_id == ctx.member_id, Txn.account_id == account.id, Txn.status == "pending", Txn.sources == ["alert"],
        Txn.occurred_at.between(st.period_start + timedelta(days=ALERT_DAYS), st.period_end - timedelta(days=ALERT_DAYS)),
    ).values(status="flagged", bucket="excluded", review_reason="not_in_statement"))
    for key, amount in st.components:  # newest value wins in networth.live; an older statement never overrides
        cv = pg_insert(ComponentValue).values(member_id=ctx.member_id, key=key, amount=amount, as_of=st.period_end,
                                              source="statement")
        s.execute(cv.on_conflict_do_update(constraint="uq_component_value_member_id_key_as_of",
                                           set_={"amount": cv.excluded.amount}))
    linked = cards.link_card_payments(s, ctx.member_id)
    refunds = txn_edit.link_refunds_by_ref(s, ctx)
    audit(s, ctx, actor, "statement.upload", f"statement:{statement_id}",
          {"parser": st.parser, "lines": len(st.lines), "created": len(created), "ok": rec.ok,
           "soft_matched": len(soft), "card_payments_linked": linked, "refunds_linked": refunds})
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


def ingest_alert(s: Session, ctx: MemberContext, clf: Classifier, obs: Line, msg: RawMessage, parser: str,
                 version: str) -> int:
    """One alert sighting → the txn it reports. An existing txn on the account with the same amount and
    direction within ±3 days (a statement line, or the same alert re-sent) takes the sighting; otherwise a
    `pending` txn is created until its statement arrives. Returns the txn id. Two queries plus writes."""
    p = obs.payload
    account = ensure_account(s, ctx.member_id, p["institution"], p["mask"], p["account_kind"])
    obs_id = s.scalar(insert(Observation).returning(Observation.id).values(
        member_id=ctx.member_id, raw_message_id=msg.id, parser=parser, parser_version=version, account_id=account.id,
        occurred_at=obs.occurred_at, amount=obs.amount, direction=obs.direction, merchant_raw=obs.narration,
        counterparty=parse_narration(obs.narration).payee, ref_no=obs.ref_no, confidence=obs.confidence,
        payload_json={"narration": obs.narration, **{k: v for k, v in p.items() if v is not None}}))
    near = s.execute(
        select(Txn.id, Txn.occurred_at, Txn.sources)
        .where(Txn.member_id == ctx.member_id, Txn.account_id == account.id, Txn.amount == obs.amount,
               Txn.direction == obs.direction,
               Txn.occurred_at.between(obs.occurred_at - timedelta(days=ALERT_DAYS),
                                       obs.occurred_at + timedelta(days=ALERT_DAYS)),
               # a txn already backed by another alert is a different payment of the same amount
               ~Txn.id.in_(select(TxnObservation.txn_id).join(Observation, Observation.id == TxnObservation.observation_id)
                           .where(Observation.member_id == ctx.member_id, Observation.raw_message_id.is_not(None),
                                  Observation.parser == parser, Observation.raw_message_id != msg.id)))
    ).all()
    hit = min(near, key=lambda r: (abs((r.occurred_at - obs.occurred_at).days), r.id), default=None)
    if hit is not None:
        txn_id = hit.id
        s.execute(update(Txn).where(Txn.id == txn_id).values(
            sources=case((Txn.sources.contains(["alert"]), Txn.sources), else_=func.array_append(Txn.sources, "alert"))))
    else:
        d = clf.classify_batch([TxnInput(obs.occurred_at, obs.amount, obs.direction, obs.narration, account.id,
                                         obs.ref_no)])[0]  # type: ignore[arg-type]
        cat_ids = category_ids(s, ctx.household_id)
        txn_id = s.scalar(insert(Txn).returning(Txn.id).values(
            member_id=ctx.member_id, account_id=account.id, occurred_at=obs.occurred_at, amount=obs.amount,
            direction=obs.direction, kind=d.kind, merchant_norm=d.merchant[:120],
            counterparty=parse_narration(obs.narration).payee, ref_no=obs.ref_no, narration=obs.narration,
            vpa=d.vpa, payee_key=d.payee_key[:80], category_id=cat_ids.get(d.category) if d.category else None,
            bucket=d.bucket, classified_by=d.classified_by, rule_id=d.rule_id, review_reason=d.inbox_reason,
            status="pending", sources=["alert"],
            dedupe_key=hashlib.sha256(f"alert|{msg.sha256}|{obs.occurred_at}|{obs.amount}".encode()).hexdigest()))
    s.execute(pg_insert(TxnObservation).on_conflict_do_nothing().values(txn_id=txn_id, observation_id=obs_id,
                                                                        member_id=ctx.member_id))
    return txn_id
