"""Per-request member-scoped DB access: every request runs inside one RLS-bound transaction."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated, TypeVar

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.datastructures import State

from tijori.app.auth import Identity, authenticate
from tijori.db import MemberContext, bind_member_by_email, bind_member_by_mcp_token, bind_member_by_session


@dataclass(frozen=True, slots=True)
class MemberDB:
    session: Session
    ctx: MemberContext
    actor: str  # the signed-in email, for audit_log


_owner_ids: dict[str, int] = {}  # an email's member id never changes, so one lookup per process


def _owner_id(session: Session, owner: str) -> int | None:
    if owner not in _owner_ids:
        row = session.execute(text("SELECT member_id FROM auth_member_by_email(:e)"), {"e": owner}).first()
        if row is None:
            return None  # not registered yet; nothing to cache
        _owner_ids[owner] = row.member_id
    return _owner_ids[owner]


def bind(session: Session, identity: Identity, owner: str | None) -> tuple[MemberContext, str]:
    """Resolve the login and SET LOCAL the RLS context: one round-trip per request. With an owner (always in
    prod), any other member is refused, whatever session, pasted token or OAuth grant it still holds."""
    if identity.kind == "mcp":
        token = bind_member_by_mcp_token(session, identity.value)
        if token is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token revoked")
        ctx, actor = token[0], f"mcp:{token[1]}"
        if owner and ctx.member_id != _owner_id(session, owner):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Tijori is private to its owner")
        return ctx, actor
    if identity.kind == "session":
        found = bind_member_by_session(session, identity.value)
        if found is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
        if owner and found[1].lower() != owner:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Tijori is private to its owner")
        return found
    ctx = bind_member_by_email(session, identity.value)
    if ctx is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no Tijori member for this login")
    return ctx, identity.value


def member_db(request: Request, identity: Annotated[Identity, Depends(authenticate)]) -> Iterator[MemberDB]:
    with Session(request.app.state.engine) as session, session.begin():
        ctx, email = bind(session, identity, request.app.state.settings.owner_email)
        yield MemberDB(session, ctx, email)


MemberDep = Annotated[MemberDB, Depends(member_db)]
AuthDep = Annotated[Identity, Depends(authenticate)]
T = TypeVar("T")


def run_as_member(state: State, identity: Identity, work: Callable[[Session, MemberContext, str], T]) -> T:
    """For async endpoints that read the body first: call via run_in_threadpool so the blocking
    session work stays off the event loop. `state` is request.app.state (engine and settings)."""
    with Session(state.engine) as session, session.begin():
        ctx, email = bind(session, identity, state.settings.owner_email)
        return work(session, ctx, email)


def require_json(request: Request) -> None:
    """Writes accept application/json only: it forces a CORS preflight, so no cross-site form
    can reach a mutation."""
    ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")


def require_upload_header(request: Request) -> None:
    # multipart/form-data is a CORS "simple" request; a custom header forces a preflight.
    if request.headers.get("x-requested-with", "").lower() != "tijori":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "uploads need the header X-Requested-With: tijori")
