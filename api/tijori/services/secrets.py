"""Member secrets through SecretBox. Plaintext exists only in memory for the call that needs it."""

import re
import secrets
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.models import Secret
from tijori.secretbox import Sealed, SecretBox


class SecretsUnavailable(RuntimeError):
    """TIJORI_MASTER_KEY is not configured (dev only: prod refuses to start without it)."""


def require(box: SecretBox | None) -> SecretBox:
    if box is None:
        raise SecretsUnavailable("TIJORI_MASTER_KEY is not set")
    return box


def mail_source_name(source_id: int) -> str:
    return f"mail_source:{source_id}"


# An account holds any number of statement passwords (a bank's own, an SBI Quick code, ...). "main" and "extra"
# are the slots from when there were two; new ones get a random slot.
MAX_STATEMENT_PASSWORDS = 10
SLOT = re.compile(r"^[a-z0-9]{1,16}$")
_ACCOUNT_PREFIX = "statement_password:account:"


def statement_password_name(account_id: int, slot: str = "main") -> str:
    return f"{_ACCOUNT_PREFIX}{account_id}" + ("" if slot == "main" else f":{slot}")


def new_slot() -> str:
    return secrets.token_hex(4)


def put(s: Session, ctx: MemberContext, box: SecretBox, name: str, plaintext: str, label: str | None = None) -> None:
    """Upsert. A replace without a label keeps the stored one."""
    sealed = box.seal(ctx.member_id, name, plaintext.encode())
    stmt = insert(Secret).values(member_id=ctx.member_id, name=name, key_version=sealed.key_version,
                                 wrapped_key=sealed.wrapped_key, key_nonce=sealed.key_nonce, nonce=sealed.nonce,
                                 ciphertext=sealed.ciphertext, label=label)
    s.execute(stmt.on_conflict_do_update(
        constraint="uq_secret_member_id_name",
        set_={c: stmt.excluded[c] for c in ("key_version", "wrapped_key", "key_nonce", "nonce", "ciphertext")}
        | {"updated_at": func.now(), "label": func.coalesce(stmt.excluded.label, Secret.label)},
    ))


def get(s: Session, ctx: MemberContext, box: SecretBox, name: str) -> str | None:
    row = s.scalars(select(Secret).where(Secret.member_id == ctx.member_id, Secret.name == name)).first()
    if row is None:
        return None
    sealed = Sealed(row.key_version, row.wrapped_key, row.key_nonce, row.nonce, row.ciphertext)
    return box.open(ctx.member_id, name, sealed).decode()


def get_many(s: Session, ctx: MemberContext, box: SecretBox, prefix: str) -> list[str]:
    rows = s.scalars(select(Secret).where(Secret.member_id == ctx.member_id, Secret.name.startswith(prefix))
                     .order_by(Secret.updated_at.desc())).all()
    return [box.open(ctx.member_id, r.name,
                     Sealed(r.key_version, r.wrapped_key, r.key_nonce, r.nonce, r.ciphertext)).decode() for r in rows]


def remove(s: Session, ctx: MemberContext, name: str) -> bool:
    return bool(s.execute(delete(Secret).where(Secret.member_id == ctx.member_id, Secret.name == name)).rowcount)


def names(s: Session, member_id: int, prefix: str) -> set[str]:
    return set(s.scalars(select(Secret.name).where(Secret.member_id == member_id, Secret.name.startswith(prefix))))


def _account_names(account_id: int) -> Any:
    base = statement_password_name(account_id)
    return or_(Secret.name == base, Secret.name.startswith(base + ":", autoescape=True))


def account_passwords(s: Session, member_id: int, account_id: int | None = None) -> dict[int, list[dict[str, Any]]]:
    """Each account's statement passwords as {slot, label, updated_at}: "main" first, then oldest first; never a
    value. One query."""
    q = select(Secret.name, Secret.label, Secret.updated_at, Secret.created_at).where(
        Secret.member_id == member_id,
        Secret.name.startswith(_ACCOUNT_PREFIX) if account_id is None else _account_names(account_id))
    out: dict[int, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    for name, label, updated, created in s.execute(q):
        acct, _, slot = name[len(_ACCOUNT_PREFIX):].partition(":")
        if acct.isdigit():
            out[int(acct)].append((created, {"slot": slot or "main", "label": label, "updated_at": updated}))
    return {k: [p for _, p in sorted(v, key=lambda x: (x[1]["slot"] != "main", x[0]))] for k, v in out.items()}


def remove_account_passwords(s: Session, ctx: MemberContext, account_id: int) -> int:
    return s.execute(delete(Secret).where(Secret.member_id == ctx.member_id, _account_names(account_id))).rowcount
