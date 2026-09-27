"""Payee aliases: the member's own name for a payee, and suggestions for payees that look like one.

A shop paid through its owner's UPI handle, or one store printed several ways, gets one name. The name is
written onto txn.merchant_norm, so everything grouped by merchant (Spending, trends, search) follows it, and
ingest.load_classifier applies it to new txns. payee_key is never rewritten, so a reset is exact.
"""

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.models import Member, PayeeAlias, Txn
from tijori.money import fmt
from tijori.services.common import audit
from tijori.services.errors import Invalid, NotFound

MAX_KEYS = 50
PAYEE_SEARCH_LIMIT = 20
MAX_DISMISSED = 500

# --- how alike two names are ------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")
# Words that don't tell one shop from another.
_NOISE = frozenset("pvt ltd private limited llp co company the and of in india store stores shop mart "
                   "enterprise enterprises traders".split())
MIN_PREFIX = 8  # SBI keeps 8+ chars of a name (merchants.payee_key), so 8 shared leading chars is one name
MIN_RATIO = 0.85


def _squash(name: str) -> str:
    return "".join(_WORD.findall(name.lower()))


def _words(name: str) -> set[str]:
    return {w for w in _WORD.findall(name.lower()) if len(w) > 1 and w not in _NOISE}


def likeness(a: str, b: str) -> str | None:
    """Why two names look like one payee, or None. "prefix": one starts the other ("Style Hub" /
    "STYLEHUB PVT LTD"); "words": the same telling words in any order; "spelling": a near-identical spelling."""
    sa, sb = _squash(a), _squash(b)
    short = min(len(sa), len(sb))
    if short < 4:
        return None
    common = len(os.path.commonprefix([sa, sb]))
    if common >= MIN_PREFIX or (common == short and short >= 5):
        return "prefix"
    wa, wb = _words(a), _words(b)
    shared = wa & wb
    if any(len(w) >= 4 for w in shared) and len(shared) * 2 >= len(wa | wb):
        return "words"
    if short >= 6:
        m = SequenceMatcher(None, sa, sb, autojunk=False)
        # The cheap upper bounds first: most pairs stop at the length check.
        if m.real_quick_ratio() >= MIN_RATIO and m.quick_ratio() >= MIN_RATIO and m.ratio() >= MIN_RATIO:
            return "spelling"
    return None


# --- reads -------------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _Alias:
    payee_key: str
    name: str
    original: str


def _aliases(s: Session, member_id: int) -> list[_Alias]:
    return [_Alias(*r) for r in s.execute(
        select(PayeeAlias.payee_key, PayeeAlias.name, PayeeAlias.original)
        .where(PayeeAlias.member_id == member_id).order_by(PayeeAlias.name, PayeeAlias.original)).all()]


def group_of(s: Session, member_id: int, payee_key: str) -> tuple[str | None, str | None, set[str]]:
    """(alias name, this payee's original name, every payee_key sharing the name). One query."""
    rows = _aliases(s, member_id)
    me = next((a for a in rows if a.payee_key == payee_key), None)
    if me is None:
        return None, None, {payee_key}
    return me.name, me.original, {a.payee_key for a in rows if a.name == me.name}


def _payees(s: Session, member_id: int) -> list[Any]:
    """Every payee with its names newest first, its count, total and last date. One grouped scan of the
    member's txns: callers run it once per request, never per payee."""
    return s.execute(
        select(Txn.payee_key,
               func.array_agg(aggregate_order_by(Txn.merchant_norm, Txn.occurred_at.desc())).label("names"),
               func.array_agg(aggregate_order_by(Txn.counterparty, Txn.occurred_at.desc())).label("parties"),
               func.max(Txn.vpa).label("vpa"), func.count().label("n"), func.sum(Txn.amount).label("total"),
               func.max(Txn.occurred_at).label("last_at"))
        .where(Txn.member_id == member_id, Txn.payee_key.is_not(None)).group_by(Txn.payee_key)
    ).all()


def _names(p: Any) -> list[str]:
    return list(dict.fromkeys(n for n in (*p.names, *p.parties) if n))


def _payee_out(p: Any, **extra: Any) -> dict[str, Any]:
    names = [n for n in p.names if n]
    return {"payee_key": p.payee_key, "merchant": names[0] if names else None,
            "counterparty": next((c for c in p.parties if c), None), "vpa": p.vpa, "count": p.n,
            "total": fmt(p.total), "last_at": p.last_at, **extra}


def _dismissed(s: Session, member_id: int) -> dict[str, list[str]]:
    settings = s.scalar(select(Member.settings).where(Member.id == member_id)) or {}
    out: dict[str, list[str]] = defaultdict(list)
    for key, name in settings.get("alias_not") or []:
        out[key].append(name)
    return out


def _match(names: list[str], targets: dict[str, list[str]], skip: list[str]) -> dict[str, str] | None:
    """The first alias one of these names looks like. `targets`: alias name → the strings it is known by
    (the name and its payees' originals)."""
    for alias, known in targets.items():
        if alias in skip or alias in names:  # already that exact name: it groups without an alias
            continue
        for n in names:
            for k in known:
                if why := likeness(n, k):
                    return {"name": alias, "why": why, "like": k}
    return None


def _targets(rows: list[_Alias]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in rows:
        known = out.setdefault(a.name, [a.name])
        if a.original not in known:
            known.append(a.original)
    return out


def suggestions(s: Session, member_id: int, rows: list[_Alias] | None = None) -> list[dict[str, Any]]:
    """Payees without an alias whose name looks like one of the member's aliases, most txns first.
    Three queries; the comparisons are payees × alias names in Python (likeness prunes on length first)."""
    rows = _aliases(s, member_id) if rows is None else rows
    if not rows:
        return []
    targets, aliased, dismissed = _targets(rows), {a.payee_key for a in rows}, _dismissed(s, member_id)
    out = []
    for p in _payees(s, member_id):
        if p.payee_key in aliased:
            continue
        if hit := _match(_names(p), targets, dismissed.get(p.payee_key, [])):
            out.append(_payee_out(p, suggest=hit))
    out.sort(key=lambda x: (-x["count"], x["payee_key"]))
    return out


def suggest_for(s: Session, member_id: int, payee_key: str, names: list[str]) -> dict[str, str] | None:
    """The alias this one payee looks like, for the txn drawer. Two queries."""
    rows = _aliases(s, member_id)
    if not rows or any(a.payee_key == payee_key for a in rows):
        return None
    return _match([n for n in names if n], _targets(rows), _dismissed(s, member_id).get(payee_key, []))


def list_aliases(s: Session, member_id: int) -> dict[str, Any]:
    """Every alias with its txn count, and the suggestions. Four queries."""
    rows = _aliases(s, member_id)
    n = dict(s.execute(select(Txn.payee_key, func.count()).where(
        Txn.member_id == member_id, Txn.payee_key.in_([r.payee_key for r in rows])).group_by(Txn.payee_key)
    ).tuples().all()) if rows else {}
    return {"items": [{"payee_key": r.payee_key, "name": r.name, "original": r.original,
                       "count": n.get(r.payee_key, 0)} for r in rows],
            "suggestions": suggestions(s, member_id, rows)}


def search_payees(s: Session, member_id: int, q: str) -> dict[str, Any]:
    """Payees whose name contains q, ignoring case, spaces and punctuation, or looks like it (likeness):
    the candidates to give one name. Runs as the member types: one grouped scan, then Python."""
    squashed = _squash(q)
    if len(squashed) < 2:
        return {"items": []}
    alias_of = {a.payee_key: a.name for a in _aliases(s, member_id)}
    hits = []
    for p in _payees(s, member_id):
        names = _names(p)
        if any(squashed in _squash(n) for n in names):
            why = "contains"
        elif not (why := next((w for n in names if (w := likeness(q, n))), None)):
            continue
        hits.append(_payee_out(p, alias=alias_of.get(p.payee_key), why=why))
    hits.sort(key=lambda x: (x["why"] != "contains", -x["count"], x["payee_key"]))
    return {"items": hits[:PAYEE_SEARCH_LIMIT]}


# --- writes ------------------------------------------------------------------------------------

def _spelling(s: Session, member_id: int, name: str, keys: list[str]) -> str:
    """An existing merchant name equal but for case wins, so "sharma sweets" joins "Sharma Sweets". The payees
    being renamed are left out, or they could never change the case of their own name."""
    other = s.scalar(select(Txn.merchant_norm).where(
        Txn.member_id == member_id, func.lower(Txn.merchant_norm) == name.lower(),
        or_(Txn.payee_key.is_(None), Txn.payee_key.not_in(keys))).limit(1))
    return other or name


def _keys(payee_keys: list[str]) -> list[str]:
    keys = sorted(set(payee_keys))
    if not keys or len(keys) > MAX_KEYS:
        raise Invalid(f"give 1 to {MAX_KEYS} payees")
    return keys


def rename(s: Session, ctx: MemberContext, actor: str, name: str, payee_keys: list[str]) -> dict[str, Any]:
    """Name these payees; payees given one name group as one merchant. Four queries for any number of keys,
    plus the suggestions scan so the reply can say how many more look alike."""
    name = " ".join(name.split())[:120]
    keys = _keys(payee_keys)
    if not name:
        raise Invalid("name is required")
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for r in s.execute(select(Txn.payee_key, Txn.merchant_norm, func.count().label("n"))
                       .where(Txn.member_id == ctx.member_id, Txn.payee_key.in_(keys))
                       .group_by(Txn.payee_key, Txn.merchant_norm)):
        counts[r.payee_key][r.merchant_norm or ""] += r.n
    if missing := [k for k in keys if k not in counts]:
        raise NotFound(f"no transactions for {len(missing)} of these payees")
    name = _spelling(s, ctx.member_id, name, keys)
    # On conflict the stored original stays: it is the name before the first alias, not the previous alias.
    ins = pg_insert(PayeeAlias).values([
        dict(member_id=ctx.member_id, payee_key=k, name=name,
             original=(counts[k].most_common(1)[0][0] or name)[:120]) for k in keys])
    s.execute(ins.on_conflict_do_update(constraint="uq_payee_alias_member_id_payee_key",
                                        set_={"name": ins.excluded.name}))
    updated = s.execute(update(Txn).where(Txn.member_id == ctx.member_id, Txn.payee_key.in_(keys))
                        .values(merchant_norm=name, updated_at=func.now())).rowcount
    audit(s, ctx, actor, "payee.rename", f"payees:{len(keys)}", {"payee_keys": keys, "updated": updated})
    similar = sum(1 for x in suggestions(s, ctx.member_id) if x["suggest"]["name"] == name)
    return {"name": name, "payee_keys": keys, "updated": updated, "similar": similar}


def reset(s: Session, ctx: MemberContext, actor: str, payee_keys: list[str]) -> dict[str, Any]:
    """Drop the aliases; each payee's txns take back the name they had before. Two statements."""
    keys = _keys(payee_keys)
    gone = s.execute(delete(PayeeAlias).where(PayeeAlias.member_id == ctx.member_id, PayeeAlias.payee_key.in_(keys))
                     .returning(PayeeAlias.payee_key, PayeeAlias.original)).all()
    if not gone:
        raise NotFound("none of these payees has a name of yours")
    back = dict(gone)
    restored = s.execute(update(Txn).where(Txn.member_id == ctx.member_id, Txn.payee_key.in_(back))
                         .values(merchant_norm=case(back, value=Txn.payee_key), updated_at=func.now())).rowcount
    audit(s, ctx, actor, "payee.reset", f"payees:{len(back)}", {"payee_keys": sorted(back), "restored": restored})
    return {"payee_keys": sorted(back), "restored": restored}


def dismiss(s: Session, ctx: MemberContext, actor: str, payee_key: str, name: str) -> dict[str, Any]:
    """"Not this": never suggest `name` for this payee again. Kept in member.settings as [payee_key, name]
    pairs, a list so the oldest drop first past the cap (JSONB objects don't keep key order)."""
    m = s.scalars(select(Member).where(Member.id == ctx.member_id).with_for_update()).one()
    settings = dict(m.settings or {})
    pairs = [list(x) for x in settings.get("alias_not") or []]
    if [payee_key, name] not in pairs:
        pairs.append([payee_key, name])
    m.settings = {**settings, "alias_not": pairs[-MAX_DISMISSED:]}
    audit(s, ctx, actor, "payee.suggestion_dismiss", f"payee:{payee_key}", {"name": name})
    return {"payee_key": payee_key, "name": name}
