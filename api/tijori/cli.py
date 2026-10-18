"""Command line: `python -m tijori.cli <command>`."""

import argparse
import json
import sys


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

    rk = sub.add_parser("rotate-master-key", help="re-wrap all secrets under a new TIJORI_MASTER_KEY")
    rk.set_defaults(func=cmd_rotate_master_key)

    lo = sub.add_parser("lock-to-owner", help="end all access held by anyone but TIJORI_OWNER_EMAIL")
    lo.set_defaults(func=cmd_lock_to_owner)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
