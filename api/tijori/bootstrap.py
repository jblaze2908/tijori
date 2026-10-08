"""Household and member bootstrap. Runs on the owner connection: the app role cannot create
members, by design (only the household admin invites)."""

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tijori.classify.kinds import MemberProfile
from tijori.classify.taxonomy import DEFAULT_CATEGORIES
from tijori.db import MemberContext, set_member_context
from tijori.models import Category, Household, Member


def seed_categories(session: Session, household_id: int) -> None:
    rows = [
        dict(household_id=household_id, member_id=None, name=c.name, description=c.description,
             kind=c.kind, bucket=c.bucket, credit_bucket=c.credit_bucket, sort_order=i)
        for i, c in enumerate(DEFAULT_CATEGORIES)
    ]
    session.execute(insert(Category).values(rows).on_conflict_do_nothing())


def ensure_member(
    admin_engine: Engine,
    email: str,
    name: str | None = None,
    *,
    household_id: int | None = None,
    profile: MemberProfile | None = None,
) -> MemberContext:
    """Idempotent. A new member without a household gets a new household and admin role."""
    with Session(admin_engine) as s, s.begin():
        member = s.scalar(select(Member).where(func.lower(Member.email) == email.lower()))
        if member is None:
            if household_id is None:
                household = Household(name=f"{name or email.split('@')[0]}'s household")
                s.add(household)
                s.flush()
                household_id, role = household.id, "admin"
            else:
                role = "member"
            member = Member(household_id=household_id, name=name or email.split("@")[0], email=email, role=role)
            s.add(member)
            s.flush()
        if profile is not None:
            member.classify_config = profile.to_json()
        ctx = MemberContext(member.id, member.household_id)
        # Context lets a non-superuser owner pass the FORCEd category policy too.
        set_member_context(s, ctx)
        seed_categories(s, ctx.household_id)
    return ctx
