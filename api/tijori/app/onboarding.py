"""Onboarding endpoints: invites, onboarding state, classifier profile, IMAP mail sources and
statement passwords. Secrets are write-only: no response ever carries one."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from tijori.app.deps import AuthDep, MemberDep, require_json, run_as_member
from tijori.app.ratelimit import RateLimiter
from tijori.app.schemas import (
    ClassifyProfile,
    InviteCreated,
    InviteIn,
    InviteInfo,
    MailSourceIn,
    MailSourceOut,
    MailSourcePatch,
    MailSources,
    MailTestOut,
    OnboardingIn,
    OnboardingOut,
    StatementPasswordIn,
)
from tijori.db import MemberContext
from tijori.models import Account
from tijori.secretbox import SecretBox
from tijori.services import mail, onboarding
from tijori.services import secrets as vault
from tijori.services.common import audit
from tijori.services.errors import NotFound

router = APIRouter(prefix="/api")
public = APIRouter(prefix="/api")
JSON = [Depends(require_json)]
Id = Annotated[int, Path(ge=1)]
# Each test logs in to a real mail server; keep members from hammering it (or us).
MAIL_TESTS = RateLimiter(limit=5, window_s=600)


def _box(request: Request) -> SecretBox:
    box = request.app.state.secret_box
    if box is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "TIJORI_MASTER_KEY is not configured")
    return box


@public.get("/invites/{token}", response_model=InviteInfo)
def invite_info(request: Request, token: Annotated[str, Path(min_length=20, max_length=128)]) -> dict:
    """No sign-in needed: the invite landing page shows who is invited and to which household."""
    with Session(request.app.state.engine) as s, s.begin():
        return onboarding.lookup_invite(s, token)


@router.post("/invites", response_model=InviteCreated, status_code=201, dependencies=JSON)
def create_invite(request: Request, db: MemberDep, body: InviteIn) -> dict:
    try:
        return onboarding.create_invite(db.session, db.ctx, db.actor, body.email,
                                        request.app.state.settings.public_url)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.get("/onboarding", response_model=OnboardingOut)
def get_onboarding(db: MemberDep) -> dict:
    return onboarding.onboarding(db.session, db.ctx)


@router.patch("/onboarding", response_model=OnboardingOut, dependencies=JSON)
def patch_onboarding(db: MemberDep, body: OnboardingIn) -> dict:
    return onboarding.update_onboarding(db.session, db.ctx, db.actor, step=body.step, completed=body.completed)


@router.get("/profile/classify", response_model=ClassifyProfile)
def get_profile(db: MemberDep) -> dict:
    return onboarding.get_profile(db.session, db.ctx.member_id)


@router.put("/profile/classify", response_model=ClassifyProfile, dependencies=JSON)
def put_profile(db: MemberDep, body: ClassifyProfile) -> dict:
    return onboarding.put_profile(db.session, db.ctx, db.actor, body.model_dump())


@router.get("/mail-sources", response_model=MailSources)
def list_mail_sources(db: MemberDep) -> dict:
    return mail.list_sources(db.session, db.ctx)


@router.post("/mail-sources", response_model=MailSourceOut, status_code=201, dependencies=JSON)
def create_mail_source(request: Request, db: MemberDep, body: MailSourceIn) -> dict:
    return mail.create(db.session, db.ctx, db.actor, _box(request), provider=body.provider, host=body.host,
                       port=body.port, email=body.email, app_password=body.app_password, label=body.label)


@router.patch("/mail-sources/{source_id}", response_model=MailSourceOut, dependencies=JSON)
def patch_mail_source(request: Request, db: MemberDep, source_id: Id, body: MailSourcePatch) -> dict:
    box = _box(request) if body.app_password is not None else None
    return mail.update_source(db.session, db.ctx, db.actor, box, source_id,
                              app_password=body.app_password, label=body.label)


@router.delete("/mail-sources/{source_id}", status_code=204)
def delete_mail_source(db: MemberDep, source_id: Id) -> Response:
    mail.delete_source(db.session, db.ctx, db.actor, source_id)
    return Response(status_code=204)


@router.post("/mail-sources/{source_id}/test", response_model=MailTestOut)
async def test_mail_source(request: Request, identity: AuthDep, source_id: Id) -> dict:
    """Three steps so no transaction stays open across the (up to 10 s) IMAP round-trip:
    read the source and its password, test outside the database, record the outcome."""
    box = _box(request)
    engine = request.app.state.engine

    def load(s: Session, ctx: MemberContext, actor: str) -> tuple[str, int, str, str, str]:
        if not MAIL_TESTS.allow(str(ctx.member_id)):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many connection tests; try again later")
        src, password = mail.load_for_test(s, ctx, box, source_id)
        return src.host, src.port, src.username, src.label, password

    host, port, username, label, password = await run_in_threadpool(run_as_member, engine, identity, load)
    result = await run_in_threadpool(mail.run_test, host, port, username, password, label)
    del password
    return await run_in_threadpool(run_as_member, engine, identity,
                                   lambda s, ctx, actor: mail.record_test(s, ctx, actor, source_id, result))


def _own_account(db: MemberDep, account_id: int) -> None:
    if db.session.scalar(select(Account.id).where(Account.member_id == db.ctx.member_id,
                                                  Account.id == account_id)) is None:
        raise NotFound("account not found")


@router.put("/accounts/{account_id}/statement-password", status_code=204, dependencies=JSON)
def put_statement_password(request: Request, db: MemberDep, account_id: Id, body: StatementPasswordIn) -> Response:
    _own_account(db, account_id)
    vault.put(db.session, db.ctx, _box(request), vault.statement_password_name(account_id), body.password)
    audit(db.session, db.ctx, db.actor, "statement_password.set", f"account:{account_id}", {})
    return Response(status_code=204)


@router.delete("/accounts/{account_id}/statement-password", status_code=204)
def delete_statement_password(db: MemberDep, account_id: Id) -> Response:
    _own_account(db, account_id)
    if not vault.remove(db.session, db.ctx, vault.statement_password_name(account_id)):
        raise NotFound("no statement password for this account")
    audit(db.session, db.ctx, db.actor, "statement_password.delete", f"account:{account_id}", {})
    return Response(status_code=204)
