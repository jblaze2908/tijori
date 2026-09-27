"""Engine and member-scoped sessions. Row-level security keys off two transaction-local settings."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

_SET_CONTEXT = text(
    "SELECT set_config('tijori.member_id', :member_id, true),"
    " set_config('tijori.household_id', :household_id, true)"
)
# Resolve the login and bind the RLS context in one round-trip; is_local=true is SET LOCAL,
# so the context dies with the transaction and never leaks to the next pooled checkout.
_BIND_BY_EMAIL = text(
    "SELECT m.member_id, m.household_id,"
    " set_config('tijori.member_id', m.member_id::text, true),"
    " set_config('tijori.household_id', m.household_id::text, true)"
    " FROM auth_member_by_email(:email) AS m"
)


_BIND_BY_SESSION = text(
    "SELECT m.member_id, m.household_id, m.email,"
    " set_config('tijori.member_id', m.member_id::text, true),"
    " set_config('tijori.household_id', m.household_id::text, true)"
    " FROM auth_session_member(:id_hash) AS m"
)


_BIND_BY_MCP_TOKEN = text(
    "SELECT t.member_id, t.household_id, t.token_id,"
    " set_config('tijori.member_id', t.member_id::text, true),"
    " set_config('tijori.household_id', t.household_id::text, true)"
    " FROM mcp_member_by_token(:token_hash) AS t"
)


@dataclass(frozen=True, slots=True)
class MemberContext:
    member_id: int
    household_id: int


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def set_member_context(session: Session, ctx: MemberContext) -> None:
    session.execute(_SET_CONTEXT, {"member_id": str(ctx.member_id), "household_id": str(ctx.household_id)})


def bind_member_by_email(session: Session, email: str) -> MemberContext | None:
    row = session.execute(_BIND_BY_EMAIL, {"email": email}).first()
    return MemberContext(row.member_id, row.household_id) if row else None


def bind_member_by_session(session: Session, id_hash: str) -> tuple[MemberContext, str] | None:
    """(context, email) for a live session; the lookup and SET LOCAL share one round-trip."""
    row = session.execute(_BIND_BY_SESSION, {"id_hash": id_hash}).first()
    return (MemberContext(row.member_id, row.household_id), row.email) if row else None


def bind_member_by_mcp_token(session: Session, token_hash: str) -> tuple[MemberContext, int] | None:
    """(context, token id) for a live MCP token; the lookup and SET LOCAL share one round-trip."""
    row = session.execute(_BIND_BY_MCP_TOKEN, {"token_hash": token_hash}).first()
    return (MemberContext(row.member_id, row.household_id), row.token_id) if row else None


@contextmanager
def member_session(engine: Engine, ctx: MemberContext) -> Iterator[Session]:
    """One transaction with the member's RLS context; commits on success."""
    with Session(engine) as session, session.begin():
        set_member_context(session, ctx)
        yield session
