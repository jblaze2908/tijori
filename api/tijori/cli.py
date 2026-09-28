"""Command line: `python -m tijori.cli <command>`."""

import argparse
import json
import sys
from pathlib import Path

from tijori.classify.backtest import LabeledTxn, format_report, run_backtest
from tijori.classify.engine import TxnInput
from tijori.classify.kinds import MemberProfile
from tijori.legacy import join_statement_lines, load_labels, load_statement


def _profile(path: str | None) -> MemberProfile:
    return MemberProfile.from_json(json.loads(Path(path).read_text())) if path else MemberProfile()


def cmd_backtest(args: argparse.Namespace) -> int:
    labels = load_labels(Path(args.labels))
    statements = [load_statement(Path(p)) for p in args.statement]
    if not statements:
        print("warning: no --statement given; narrations fall back to the label's payee name, "
              "so kind rules and UPI heuristics see far less than they would in production", file=sys.stderr)
    joined = join_statement_lines(labels, statements)
    unmatched = sum(1 for _, o in joined if o is None)
    if statements and unmatched:
        print(f"warning: {unmatched} labels had no matching statement line", file=sys.stderr)
    account_ids = {bank: i for i, bank in enumerate(sorted({t.bank for t in labels}), start=1)}
    items = [
        LabeledTxn(
            TxnInput(
                occurred_at=t.occurred_at,
                amount=t.amount,
                direction=t.direction,  # type: ignore[arg-type]
                narration=o.narration if o else t.who,
                account_id=account_ids[t.bank],
                ref_no=o.ref_no if o else None,
            ),
            t.category,
        )
        for t, o in joined
    ]
    report = run_backtest(items, _profile(args.profile), train_until=args.train_until)
    print(format_report(report, show_misses=args.show_misses))
    return 0


def cmd_import_legacy(args: argparse.Namespace) -> int:
    from tijori.importer import import_legacy  # DB stack loads only for DB commands

    result = import_legacy(
        data_path=Path(args.data),
        sheet_path=Path(args.sheet),
        member_email=args.member,
        member_name=args.name,
        statement_paths=[Path(p) for p in args.statement],
        profile=_profile(args.profile) if args.profile else None,
    )
    print(json.dumps(result, indent=2))
    return 0


def cmd_rotate_master_key(args: argparse.Namespace) -> int:
    """Re-wrap every data key under TIJORI_MASTER_KEY, reading old rows with TIJORI_MASTER_KEY_OLD.
    Runs on the owner (superuser) connection: secret is FORCE RLS for everyone else."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from tijori.db import make_engine
    from tijori.models import Secret
    from tijori.secretbox import Sealed
    from tijori.settings import get_settings

    settings = get_settings()
    box = settings.secret_box()
    if box is None or settings.master_key_old is None:
        print("set TIJORI_MASTER_KEY (new) and TIJORI_MASTER_KEY_OLD (current) first", file=sys.stderr)
        return 2
    with Session(make_engine(settings.admin_url())) as s, s.begin():
        rows = s.scalars(select(Secret).where(Secret.key_version != box.current)).all()
        for r in rows:
            new = box.rewrap(r.member_id, r.name, Sealed(r.key_version, r.wrapped_key, r.key_nonce, r.nonce,
                                                         r.ciphertext))
            r.key_version, r.wrapped_key, r.key_nonce = new.key_version, new.wrapped_key, new.key_nonce
    print(json.dumps({"rewrapped": len(rows), "key_version": box.current}))
    return 0


def cmd_lock_to_owner(args: argparse.Namespace) -> int:
    """End every session, token, connected app and invite not held by TIJORI_OWNER_EMAIL. Other members' data
    stays; deps.bind already refuses them, this just removes what they hold. Runs on the owner connection."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from tijori.db import make_engine
    from tijori.settings import get_settings

    owner = get_settings().owner_email
    if owner is None:
        print("set TIJORI_OWNER_EMAIL first", file=sys.stderr)
        return 2
    others = "SELECT id FROM member WHERE lower(email) <> :owner"
    with Session(make_engine(get_settings().admin_url())) as s, s.begin():
        if s.execute(text("SELECT 1 FROM member WHERE lower(email) = :owner"), {"owner": owner}).first() is None:
            print("the owner has no member yet; sign in once first", file=sys.stderr)
            return 2
        n = {k: s.execute(text(q), {"owner": owner}).rowcount for k, q in (
            ("sessions", f"DELETE FROM auth_session WHERE member_id IN ({others})"),
            ("oauth_tokens", f"DELETE FROM oauth_token WHERE member_id IN ({others})"),
            ("oauth_codes", f"DELETE FROM oauth_code WHERE member_id IN ({others})"),
            ("mcp_grants", f"UPDATE mcp_token SET revoked_at = now() WHERE revoked_at IS NULL AND member_id IN ({others})"),
            ("invites", "DELETE FROM invite"),
        )}
        n["other_members"] = s.scalar(text(f"SELECT count(*) FROM ({others}) o"), {"owner": owner})
    print(json.dumps(n))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tijori")
    sub = p.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="replay labelled history through the classifier")
    bt.add_argument("--labels", required=True, help="prototype data.json with labelled txns")
    bt.add_argument("--statement", action="append", default=[],
                    help="pdftotext -layout statement text to recover narrations (repeatable)")
    bt.add_argument("--profile", help="member profile JSON (own names, handles, account masks)")
    bt.add_argument("--train-until", help="YYYY-MM: fixed split instead of rolling months")
    bt.add_argument("--show-misses", type=int, default=0, help="print the first N misfiled txns")
    bt.set_defaults(func=cmd_backtest)

    il = sub.add_parser("import-legacy", help="load prototype txns and the net-worth sheet for one member")
    il.add_argument("--data", required=True)
    il.add_argument("--sheet", required=True)
    il.add_argument("--member", required=True, help="member email")
    il.add_argument("--name", help="member display name (default: email local part)")
    il.add_argument("--statement", action="append", default=[],
                    help="statement text to attach narrations, refs and balances (repeatable)")
    il.add_argument("--profile", help="member profile JSON to store as classify_config")
    il.set_defaults(func=cmd_import_legacy)

    rk = sub.add_parser("rotate-master-key", help="re-wrap all secrets under a new TIJORI_MASTER_KEY")
    rk.set_defaults(func=cmd_rotate_master_key)

    lo = sub.add_parser("lock-to-owner", help="end all access held by anyone but TIJORI_OWNER_EMAIL")
    lo.set_defaults(func=cmd_lock_to_owner)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
