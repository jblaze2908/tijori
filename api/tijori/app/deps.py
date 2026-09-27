"""Per-request member-scoped DB access: every request runs inside one RLS-bound transaction."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated, TypeVar

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tijori.app.auth import Identity, authenticate
from tijori.db import MemberContext, bind_member_by_email, bind_member_by_mcp_token, bind_member_by_session


@dataclass(frozen=True, slots=True)
class MemberDB:
    session: Session
    ctx: MemberContext
    actor: str  # the signed-in email, for audit_log


def bind(session: Session, identity: Identity) -> tuple[MemberContext, str]:
    """Resolve the login and SET LOCAL the RLS context: one round-trip per request."""
    if identity.kind == "mcp":
        token = bind_member_by_mcp_token(session, identity.value)
        if token is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token revoked")
        return token[0], f"mcp:{token[1]}"
    if identity.kind == "session":
        found = bind_member_by_session(session, identity.value)
        if found is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
        return found
    ctx = bind_member_by_email(session, identity.value)
    if ctx is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no Tijori member for this login")
    return ctx, identity.value


def member_db(request: Request, identity: Annotated[Identity, Depends(authenticate)]) -> Iterator[MemberDB]:
    with Session(request.app.state.engine) as session, session.begin():
        ctx, email = bind(session, identity)
        yield MemberDB(session, ctx, email)


MemberDep = Annotated[MemberDB, Depends(member_db)]
AuthDep = Annotated[Identity, Depends(authenticate)]
T = TypeVar("T")


def run_as_member(engine: Engine, identity: Identity, work: Callable[[Session, MemberContext, str], T]) -> T:
    """For async endpoints that read the body first: call via run_in_threadpool so the blocking
    session work stays off the event loop."""
    with Session(engine) as session, session.begin():
        ctx, email = bind(session, identity)
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
