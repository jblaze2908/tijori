"""Member secrets through SecretBox. Plaintext exists only in memory for the call that needs it."""

from sqlalchemy import delete, func, select
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


def statement_password_name(account_id: int) -> str:
    return f"statement_password:account:{account_id}"


def put(s: Session, ctx: MemberContext, box: SecretBox, name: str, plaintext: str) -> None:
    sealed = box.seal(ctx.member_id, name, plaintext.encode())
    stmt = insert(Secret).values(member_id=ctx.member_id, name=name, key_version=sealed.key_version,
                                 wrapped_key=sealed.wrapped_key, key_nonce=sealed.key_nonce, nonce=sealed.nonce,
                                 ciphertext=sealed.ciphertext)
    s.execute(stmt.on_conflict_do_update(
        constraint="uq_secret_member_id_name",
        set_={c: stmt.excluded[c] for c in ("key_version", "wrapped_key", "key_nonce", "nonce", "ciphertext")}
        | {"updated_at": func.now()},
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
