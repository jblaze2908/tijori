"""The signed-in member: profile, settings, accounts."""

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, true, update
from sqlalchemy.orm import Session

from tijori.account_kinds import KINDS, subtype_ok
from tijori.classify.kinds import DEFAULT_LOCAL_SHOP_CAP
from tijori.db import MemberContext
from tijori.models import Account, Holding, Household, Member, Statement, Txn
from tijori.money import fmt
from tijori.parsers.alerts import COVERED
from tijori.services import coverage
from tijori.services.common import account_label, audit
from tijori.services.errors import Invalid, NotFound
from tijori.services.recurring import balances
from tijori.services.secrets import account_passwords, remove_account_passwords

DEFAULT_MONTH_START_DAY = 1
DEFAULT_RETENTION_DAYS = 0  # keep forever until the member picks a window: purging is irreversible


def _settings(settings: dict[str, Any], classify_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "month_start_day": int(settings.get("month_start_day", DEFAULT_MONTH_START_DAY)),
        "local_shop_cap": fmt(Decimal(str(classify_config.get("local_shop_cap", DEFAULT_LOCAL_SHOP_CAP)))),
        "raw_retention_days": int(settings.get("raw_retention_days", DEFAULT_RETENTION_DAYS)),
        "notify_topic": settings.get("notify_topic"),
        "notify_enabled": bool(settings.get("notify_enabled", False)),
    }


def get_settings(s: Session, member_id: int) -> dict[str, Any]:
    settings, cfg = s.execute(select(Member.settings, Member.classify_config).where(Member.id == member_id)).one()
    return _settings(settings, cfg)


def update_settings(s: Session, ctx: MemberContext, actor: str, *, month_start_day: int | None,
                    local_shop_cap: Decimal | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    for key, value in (extra or {}).items():  # validated by the route: retention, notification topic and switch
        s.execute(update(Member).where(Member.id == ctx.member_id)
                  .values(settings=Member.settings.op("||")(func.jsonb_build_object(key, value))))
    if month_start_day is not None:
        s.execute(update(Member).where(Member.id == ctx.member_id)
                  .values(settings=Member.settings.op("||")(func.jsonb_build_object("month_start_day",
                                                                                     month_start_day))))
    if local_shop_cap is not None:
        s.execute(update(Member).where(Member.id == ctx.member_id)
                  .values(classify_config=Member.classify_config.op("||")(
                      func.jsonb_build_object("local_shop_cap", str(local_shop_cap)))))
    audit(s, ctx, actor, "settings.update", f"member:{ctx.member_id}",
          {"month_start_day": month_start_day, "local_shop_cap": str(local_shop_cap) if local_shop_cap else None,
           "fields": sorted(extra or {})})
    return get_settings(s, ctx.member_id)


def me(s: Session, ctx: MemberContext) -> dict[str, Any]:
    m, h = s.execute(select(Member, Household).join(Household, Household.id == Member.household_id)
                     .where(Member.id == ctx.member_id)).one()
    return {"name": m.name, "email": m.email, "role": m.role, "household": {"id": h.id, "name": h.name},
            "settings": _settings(m.settings, m.classify_config)}


def accounts(s: Session, member_id: int) -> dict[str, Any]:
    """Accounts with activity, the latest statement and how far each feed reaches (services/coverage).
    Grouped/lateral lookups, not one query per account."""
    stats = (select(Txn.account_id, func.count().label("n"), func.min(Txn.occurred_at).label("first_at"),
                    func.max(Txn.occurred_at).label("last_at"))
             .where(Txn.member_id == member_id).group_by(Txn.account_id).subquery())
    latest = (select(Statement.period_start, Statement.period_end, Statement.reconciled_at, Statement.diff)
              .where(Statement.account_id == Account.id).order_by(Statement.period_end.desc()).limit(1)
              .lateral())
    rows = s.execute(
        select(Account, func.coalesce(stats.c.n, 0).label("n"), stats.c.first_at, stats.c.last_at,
               latest.c.period_start, latest.c.period_end, latest.c.reconciled_at, latest.c.diff)
        .outerjoin(stats, stats.c.account_id == Account.id)
        .outerjoin(latest, true())
        .where(Account.member_id == member_id).order_by(Account.institution, Account.id)
    ).all()
    passwords = account_passwords(s, member_id)
    held = _holdings(s, member_id)
    bal = balances(s, member_id)
    feed = coverage.feeds(s, member_id)
    boxes = {m["id"]: m for m in coverage.mailboxes(s, member_id)}
    none = coverage.Feed(None, None, None, None, None)
    return {"items": [
        {"id": a.id, "institution": a.institution, "name": a.name, "kind": a.kind, "mask": a.mask,
         "label": account_label(a.institution, a.name, a.mask), "currency": a.currency, "txn_count": n,
         **_net_worth_settings(a),
         "first_txn_at": first, "last_txn_at": last,
         "last_statement": None if pe is None else {"period_start": ps, "period_end": pe,
                                                    "reconciled": rat is not None,
                                                    "diff": fmt(diff) if diff is not None else None},
         "statement_passwords": passwords.get(a.id, []),
         "has_statement_password": any(p["slot"] == "main" for p in passwords.get(a.id, [])),
         "has_extra_statement_password": any(p["slot"] == "extra" for p in passwords.get(a.id, [])),
         "balance": {"amount": fmt(bal[a.id][0]), "as_of": bal[a.id][1]} if a.id in bal else None,
         "holdings": held.get(a.id),
         **_feed(a, feed.get(a.id, none), boxes)}
        for a, n, first, last, ps, pe, rat, diff in rows]}


def _holdings(s: Session, member_id: int) -> dict[int, dict[str, Any]]:
    """What an investment account's newest statement (a CAS, a fund house's) listed: its date, source and count.
    These statements carry holdings, not lines, so `last_statement` stays null for them. One query."""
    newest = (select(Holding.account_id, func.max(Holding.as_of).label("as_of"))
              .where(Holding.member_id == member_id, Holding.account_id.is_not(None))
              .group_by(Holding.account_id).subquery())
    rows = s.execute(select(Holding.account_id, Holding.as_of, func.min(Holding.source), func.count())
                     .join(newest, (newest.c.account_id == Holding.account_id) & (newest.c.as_of == Holding.as_of))
                     .where(Holding.member_id == member_id)
                     .group_by(Holding.account_id, Holding.as_of)).all()
    return {aid: {"as_of": as_of, "source": src, "count": n} for aid, as_of, src, n in rows}


def _feed(a: Account, f: coverage.Feed, boxes: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {"last_seen_at": f.last_seen_at, "coverage_pct": f.coverage_pct, "covered_through": f.covered_through,
            "live_through": coverage.live_through(a.institution, a.kind, f, boxes),
            "alerts": (a.institution, a.kind) in COVERED}


def _net_worth_settings(a: Account) -> dict[str, Any]:
    """How the account counts in net worth: the kind's defaults, and what the member set over them."""
    k = KINDS[a.kind]
    return {"subtype": a.subtype, "side": k.side, "in_net_worth": a.in_net_worth, "opened_at": a.opened_at,
            "liquid": k.liquid if a.liquid is None else a.liquid, "liquid_default": k.liquid,
            "asset_class": a.asset_class or k.asset_class, "asset_class_default": k.asset_class}


# Account fields the member may set; a null liquid or asset_class goes back to the kind's default.
ACCOUNT_FIELDS = ("name", "mask", "kind", "subtype", "liquid", "asset_class", "in_net_worth", "opened_at")


def _check(acct: Account) -> None:
    if not subtype_ok(acct.kind, acct.subtype):
        raise Invalid(f"subtype must be one of {', '.join(KINDS[acct.kind].subtypes) or 'none'} for this kind")


def create_account(s: Session, ctx: MemberContext, actor: str, *, institution: str, kind: str,
                   values: dict[str, Any]) -> dict[str, Any]:
    acct = Account(member_id=ctx.member_id, institution=institution, kind=kind,
                   **{k: v for k, v in values.items() if k in ACCOUNT_FIELDS and k != "kind"})
    _check(acct)
    s.add(acct)
    s.flush()
    audit(s, ctx, actor, "account.create", f"account:{acct.id}", {"kind": kind})
    return _account_out(s, ctx.member_id, acct.id)


def update_account(s: Session, ctx: MemberContext, actor: str, account_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """Only the fields given change. A new kind drops a subtype it doesn't have."""
    acct = s.scalars(select(Account).where(Account.member_id == ctx.member_id, Account.id == account_id)).first()
    if acct is None:
        raise NotFound("account not found")
    for k, v in values.items():
        if k in ACCOUNT_FIELDS:
            setattr(acct, k, v)
    if "kind" in values and "subtype" not in values and not subtype_ok(acct.kind, acct.subtype):
        acct.subtype = None
    if acct.in_net_worth is None:
        acct.in_net_worth = True
    _check(acct)
    s.flush()
    audit(s, ctx, actor, "account.update", f"account:{account_id}", {"fields": sorted(values)})
    return _account_out(s, ctx.member_id, account_id)


def delete_account(s: Session, ctx: MemberContext, actor: str, account_id: int) -> None:
    """Only an account with no txns and no statements: history is never orphaned by a click."""
    acct = s.scalars(select(Account).where(Account.member_id == ctx.member_id, Account.id == account_id)).first()
    if acct is None:
        raise NotFound("account not found")
    used = s.scalar(select(func.count()).select_from(Txn).where(Txn.account_id == account_id)) or 0
    used += s.scalar(select(func.count()).select_from(Statement).where(Statement.account_id == account_id)) or 0
    if used:
        raise Invalid("this account has transactions or statements; it can't be deleted")
    remove_account_passwords(s, ctx, account_id)
    s.delete(acct)
    audit(s, ctx, actor, "account.delete", f"account:{account_id}", {})


def _account_out(s: Session, member_id: int, account_id: int) -> dict[str, Any]:
    return next(a for a in accounts(s, member_id)["items"] if a["id"] == account_id)
