"""Registration by invite, onboarding state, and the classifier profile."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, literal, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.models import ONBOARDING_STEPS, Invite, MailSource, Member, RawMessage, Secret
from tijori.services.common import audit, sha256_hex
from tijori.services.errors import Invalid, NotFound

INVITE_TTL = timedelta(days=7)

# Gmail filter for the label step (PLAN §4 sources). Gmail's from: matches whole address tokens,
# so these are the distinctive tokens of each institution's sender domains, not full addresses.
GMAIL_SENDERS = ("hdfcbank", "sbi", "icicibank", "amazonpay", "cred.club", "groww", "camsonline", "kfintech",
                 "cdslindia", "cdslstatement", "pluxee")
# Mail that looks transactional but carries no transaction, or carries an OTP (never ingested).
GMAIL_EXCLUDED_SUBJECTS = ("OTP", '"Instalment due"', '"Payment Reminder"', '"Daily Margin"',
                           '"Portfolio Disclosure"')


def gmail_filter() -> str:
    return (f"from:({' OR '.join(GMAIL_SENDERS)}) "
            f"-subject:({' OR '.join(GMAIL_EXCLUDED_SUBJECTS)})")
PROFILE_KEYS = ("own_names", "own_vpas", "own_account_masks", "investment_account_masks", "employer_patterns")


# --- invites --------------------------------------------------------------------------------

def create_invite(s: Session, ctx: MemberContext, actor: str, email: str, public_url: str) -> dict[str, Any]:
    role = s.scalar(select(Member.role).where(Member.id == ctx.member_id))
    if role != "admin":
        raise PermissionError("only the household admin can invite")
    email = email.strip().lower()
    if s.execute(text("SELECT 1 FROM auth_member_by_email(:e)"), {"e": email}).first():
        raise Invalid("that email already belongs to a Tijori member")
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    s.add(Invite(household_id=ctx.household_id, email=email, token_hash=sha256_hex(token),
                 created_by=ctx.member_id, expires_at=now + INVITE_TTL))
    audit(s, ctx, actor, "invite.create", f"invite:{email}", {})
    # The token is returned once; only its hash is stored.
    return {"email": email, "token": token, "url": f"{public_url.rstrip('/')}/invite/{token}",
            "expires_at": now + INVITE_TTL}


def lookup_invite(s: Session, token: str) -> dict[str, Any]:
    row = s.execute(text("SELECT * FROM invite_lookup(:h)"), {"h": sha256_hex(token)}).first()
    if row is None:
        raise NotFound("invite not found")
    status = "used" if row.used else "expired" if row.expires_at <= datetime.now(UTC) else "valid"
    return {"email": row.email, "household": row.household, "expires_at": row.expires_at, "status": status}


def register_by_invite(s: Session, email: str, name: str, invite_hash: str) -> MemberContext | None:
    row = s.execute(text("SELECT * FROM auth_register_invited(:e, :n, :h)"),
                    {"e": email, "n": name, "h": invite_hash}).first()
    return MemberContext(row.member_id, row.household_id) if row else None


# --- onboarding state ------------------------------------------------------------------------

def onboarding(s: Session, ctx: MemberContext) -> dict[str, Any]:
    """Stored step plus a checklist derived from the data, in one round-trip."""
    m = ctx.member_id
    row = s.execute(select(
        Member.onboarding_step, Member.onboarding_completed_at, Member.classify_config,
        exists().where(MailSource.member_id == m).label("mail"),
        exists().where(Secret.member_id == m, Secret.name.startswith("statement_password:")).label("passwords"),
        exists().where(RawMessage.member_id == m, RawMessage.parse_status == "parsed").label("upload"),
    ).where(Member.id == m)).one()
    return {
        "step": row.onboarding_step, "completed_at": row.onboarding_completed_at, "steps": list(ONBOARDING_STEPS),
        "gmail_filter": gmail_filter(), "label": "tijori",
        "checklist": {"profile": bool((row.classify_config or {}).get("own_names")), "mail_source": row.mail,
                      "statement_passwords": row.passwords, "first_upload": row.upload},
    }


def update_onboarding(s: Session, ctx: MemberContext, actor: str, *, step: str | None,
                      completed: bool | None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if step is not None:
        values["onboarding_step"] = step
    if completed is True:
        values |= {"onboarding_step": "done", "onboarding_completed_at": func.now()}
    elif completed is False:
        values["onboarding_completed_at"] = None
    if values:
        s.execute(update(Member).where(Member.id == ctx.member_id).values(**values))
        audit(s, ctx, actor, "onboarding.update", f"member:{ctx.member_id}", {"step": step, "completed": completed})
    return onboarding(s, ctx)


# --- classifier profile ------------------------------------------------------------------------

def get_profile(s: Session, member_id: int) -> dict[str, Any]:
    cfg = s.scalar(select(Member.classify_config).where(Member.id == member_id)) or {}
    return {k: list(cfg.get(k, [])) for k in PROFILE_KEYS}


def put_profile(s: Session, ctx: MemberContext, actor: str, profile: dict[str, list[str]]) -> dict[str, Any]:
    """Replaces the five lists; other classify_config keys (local_shop_cap) are kept."""
    s.execute(update(Member).where(Member.id == ctx.member_id)
              .values(classify_config=Member.classify_config.op("||")(literal(profile, type_=JSONB))))
    audit(s, ctx, actor, "profile.classify", f"member:{ctx.member_id}", {k: len(v) for k, v in profile.items()})
    return get_profile(s, ctx.member_id)


# --- household ------------------------------------------------------------------------------

def household(s: Session, ctx: MemberContext) -> dict[str, Any]:
    """Members and invites of the caller's household (RLS scopes both to it)."""
    from tijori.models import Household

    h = s.get(Household, ctx.household_id)
    members = s.scalars(select(Member).where(Member.household_id == ctx.household_id).order_by(Member.id)).all()
    invites = s.scalars(select(Invite).where(Invite.household_id == ctx.household_id)
                        .order_by(Invite.created_at.desc())).all()
    now = datetime.now(UTC)
    return {
        "id": ctx.household_id, "name": h.name if h else None,
        "members": [{"id": m.id, "name": m.name, "email": m.email, "role": m.role, "joined_at": m.created_at}
                    for m in members],
        "invites": [{"id": i.id, "email": i.email, "created_at": i.created_at, "expires_at": i.expires_at,
                     "status": "accepted" if i.used_at else "expired" if i.expires_at <= now else "pending"}
                    for i in invites],
    }


def revoke_invite(s: Session, ctx: MemberContext, actor: str, invite_id: int) -> None:
    role = s.scalar(select(Member.role).where(Member.id == ctx.member_id))
    if role != "admin":
        raise PermissionError("only the household admin can revoke invites")
    inv = s.scalars(select(Invite).where(Invite.id == invite_id, Invite.household_id == ctx.household_id)).first()
    if inv is None or inv.used_at is not None:
        raise NotFound("no pending invite with that id")
    s.delete(inv)
    audit(s, ctx, actor, "invite.revoke", f"invite:{inv.email}", {})


def rename_member(s: Session, ctx: MemberContext, actor: str, name: str) -> None:
    s.execute(update(Member).where(Member.id == ctx.member_id).values(name=name))
    audit(s, ctx, actor, "member.rename", f"member:{ctx.member_id}", {})
