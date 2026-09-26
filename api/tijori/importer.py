"""One-off import of the 2026-09-26 prototype: 349 labelled txns and the net-worth sheet."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tijori.bootstrap import ensure_member
from tijori.classify.engine import Classifier, TxnInput
from tijori.classify.kinds import MemberProfile
from tijori.classify.merchants import name_key
from tijori.classify.narration import parse_narration
from tijori.classify.taxonomy import bucket_of, kind_of
from tijori.db import make_engine, member_session
from tijori.legacy import LegacyTxn, join_statement_lines, load_labels, load_sheet, load_statement
from tijori.models import Statement, Txn
from tijori.parsers import Observation, reconcile
from tijori.services.ingest import category_ids, ensure_account, line_keys
from tijori.services.networth import upsert_sheet
from tijori.settings import Settings, get_settings

LEGACY_RULE_ID = "legacy:2026-09-26"


def _legacy_key(t: LegacyTxn, occurrence: int) -> str:
    raw = f"legacy|{t.bank}|{t.occurred_at.isoformat()}|{t.amount}|{t.direction}|{occurrence}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _row(member_id: int, account_id: int, t: LegacyTxn, obs: Observation | None, category_id: int,
         key: str) -> dict[str, Any]:
    ident = None
    if obs is not None:
        ident = Classifier.identify(TxnInput(t.occurred_at, t.amount, t.direction, obs.narration))  # type: ignore[arg-type]
    category = t.category
    return dict(
        member_id=member_id, account_id=account_id, occurred_at=t.occurred_at,
        posted_at=obs.value_date if obs else None, amount=t.amount, direction=t.direction, kind=kind_of(category),
        merchant_norm=t.who[:120], counterparty=parse_narration(obs.narration).payee if obs else None,
        narration=obs.narration if obs else None, ref_no=obs.ref_no if obs else None,
        vpa=ident.narration.vpa if ident else None,
        payee_key=ident.payee_key if ident else "name:" + name_key(t.who, 8),
        category_id=category_id, bucket=bucket_of(category), classified_by="system", rule_id=LEGACY_RULE_ID,
        status="reconciled" if obs else "posted", sources=["import", "statement"] if obs else ["import"],
        dedupe_key=key,
    )


def import_legacy(
    *,
    data_path: Path,
    sheet_path: Path,
    member_email: str,
    member_name: str | None = None,
    statement_paths: list[Path] | None = None,
    profile: MemberProfile | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    labels = load_labels(data_path)
    sheet = load_sheet(sheet_path)
    statements = [load_statement(p) for p in statement_paths or []]
    joined = join_statement_lines(labels, statements)

    ctx = ensure_member(make_engine(settings.admin_url()), member_email, member_name, profile=profile)
    engine = make_engine(settings.database_url.get_secret_value())
    with member_session(engine, ctx) as s:
        masks = {st.institution: st.account_mask for st in statements}
        accounts = {bank: ensure_account(s, ctx.member_id, bank, masks.get(bank)).id
                    for bank in sorted({t.bank for t in labels})}
        # Statement lines get the identity an upload would give them, so a later upload matches.
        obs_key = {id(o): k for st in statements
                   for o, k in zip(st.lines, line_keys(accounts[st.institution], st.lines))}
        cats = category_ids(s, ctx.household_id)
        rows, seen, bucket_mismatches = [], {}, 0
        for t, obs in joined:
            base = (t.bank, t.occurred_at, t.amount, t.direction)
            seen[base] = seen.get(base, 0) + 1
            if t.direction == "debit" and bucket_of(t.category) != t.bucket:
                bucket_mismatches += 1
            key = obs_key[id(obs)] if obs is not None else _legacy_key(t, seen[base])
            rows.append(_row(ctx.member_id, accounts[t.bank], t, obs, cats[t.category], key))
        present = set(s.scalars(select(Txn.dedupe_key).where(
            Txn.member_id == ctx.member_id, Txn.dedupe_key.in_([r["dedupe_key"] for r in rows]))))
        new = [r for r in rows if r["dedupe_key"] not in present]
        if new:
            s.execute(insert(Txn), new)
        stored = 0
        for st in statements:
            rec = reconcile(st)
            done = s.scalar(pg_insert(Statement).values(
                member_id=ctx.member_id, account_id=accounts[st.institution], period_start=st.period_start,
                period_end=st.period_end, opening=st.summary.opening, closing=st.summary.closing,
                parser=st.parser, parser_version=st.parser_version,
                reconciled_at=datetime.now(UTC) if rec.ok else None, diff=rec.closing_diff,
            ).on_conflict_do_nothing().returning(Statement.id))
            stored += done is not None
        snapshots = upsert_sheet(s, ctx.member_id, sheet)
    return {
        "member_id": ctx.member_id,
        "household_id": ctx.household_id,
        "txns_in_file": len(labels),
        "txns_inserted": len(new),
        "txns_already_present": len(labels) - len(new),
        "txns_with_statement_line": sum(1 for _, o in joined if o is not None),
        "debit_bucket_mismatches": bucket_mismatches,
        "snapshots_upserted": snapshots,
        "statements_stored": stored,
    }
