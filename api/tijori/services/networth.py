"""Net worth by account (account_kinds). Every account the member counts is valued from its own evidence, and on any
day takes its newest value: a printed balance moved on by the alerts since (banks), the card statement plus card
txns since (cards, as what is owed), units × that day's price (holdings), or a dated value set by hand or printed by
a statement; EPF also gets an estimate. Assets add, liabilities subtract, and open loans to and from people add two
rows of their own. Which accounts count, which are liquid and their asset class are the member's settings."""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tijori.account_kinds import COMPONENT_ACCOUNTS, KINDS, SUBTYPE_LABELS, asset_class, liquid
from tijori.db import MemberContext
from tijori.models import Account, AccountValuation, Holding, Observation, Price, Statement, Txn
from tijori.money import ZERO, fmt
from tijori.services import loans
from tijori.services.common import account_label, audit
from tijori.services.errors import Invalid, NotFound
from tijori.services.recurring import alerts_from, balances

STALE_DAYS = 30
PROJECTION_MONTHS = 12
ETF = re.compile(r"\bETF\b|BEES", re.I)
# Same day, the more direct evidence wins: a price over a statement's total, a statement over a value set by hand.
RANK = {"estimate": 0, "imported": 1, "manual": 2, "statement": 3, "prices": 4}
# Where an investment debit lands, by the payee label or narration: the subtype of the account it adds to.
INVESTS_INTO = ((re.compile(r"MF|SIP|NACH", re.I), "mf"), (re.compile(r"stock", re.I), "stocks"),
                (re.compile(r"PPF", re.I), "ppf"), (re.compile(r"\bFD\b|fixed deposit", re.I), "fd"))

Entry = tuple[date, Decimal, str, date]  # as of, value (what is owed, for a liability), source, what it already includes
Flow = tuple[date, Decimal]  # money into (out of) the account after its newest value, not yet in it


@dataclass(frozen=True, slots=True)
class Acct:
    id: int
    label: str
    kind: str
    subtype: str | None
    asset_class: str
    liquid: bool
    opened_at: date | None

    @property
    def liability(self) -> bool:
        return KINDS[self.kind].side == "liability"


def _accounts(s: Session, member_id: int) -> dict[int, Acct]:
    rows = s.scalars(select(Account).where(Account.member_id == member_id, Account.in_net_worth)).all()
    return {a.id: Acct(a.id, account_label(a.institution, a.name, a.mask) or a.institution, a.kind, a.subtype,
                       asset_class(a.kind, a.asset_class), liquid(a.kind, a.liquid), a.opened_at) for a in rows}


def _equity(isin: str | None, name: str | None) -> bool:
    return (isin or "").startswith("INE") or bool(ETF.search(name or ""))


def _latest_holdings(s: Session, member_id: int, accts: dict[int, Acct], today: date) -> dict[int, tuple[date, list[Any]]]:
    """Each account's holdings at its newest statement date. A security held in two accounts (a fund on a CAMS
    statement and again in the CAS) counts once, in the account read most recently. One query."""
    newest = (select(Holding.account_id, func.max(Holding.as_of).label("as_of"))
              .where(Holding.member_id == member_id, Holding.as_of <= today).group_by(Holding.account_id).subquery())
    rows = s.execute(select(Holding.account_id, Holding.as_of, Holding.isin, Holding.name, Holding.units)
                     .join(newest, (newest.c.account_id == Holding.account_id) & (newest.c.as_of == Holding.as_of))
                     .where(Holding.member_id == member_id)).all()
    owner: dict[str, tuple[date, int]] = {}
    for r in rows:
        if r.account_id in accts and r.isin:
            owner[r.isin] = max(owner.get(r.isin, (r.as_of, -r.account_id)), (r.as_of, -r.account_id))
    out: dict[int, tuple[date, list[Any]]] = {}
    for r in rows:
        if r.account_id in accts and (not r.isin or owner[r.isin] == (r.as_of, -r.account_id)):
            out.setdefault(r.account_id, (r.as_of, []))[1].append(r)
    return out


def _priced(s: Session, member_id: int, accts: dict[int, Acct], today: date) -> dict[int, tuple[Decimal, date, date]]:
    """Holdings at the newest daily price (NSE close, AMFI NAV) up to today: (value, price date, holdings date).
    Units bought since the statement show up with the next one."""
    latest = _latest_holdings(s, member_id, accts, today)
    isins = {r.isin for _, rows in latest.values() for r in rows if r.isin}
    if not isins:
        return {}
    ranked = (select(Price.isin_or_symbol, Price.close, Price.date,
                     func.row_number().over(partition_by=Price.isin_or_symbol, order_by=Price.date.desc()).label("rn"))
              .where(Price.isin_or_symbol.in_(isins), Price.date <= today).subquery())
    px = {r.isin_or_symbol: (r.close, r.date) for r in s.execute(select(ranked).where(ranked.c.rn == 1))}
    out: dict[int, tuple[Decimal, date, date]] = {}
    for aid, (as_of, rows) in latest.items():
        priced = [(r.units * px[r.isin][0], px[r.isin][1]) for r in rows if r.isin in px]
        if priced:
            out[aid] = (sum((v for v, _ in priced), ZERO).quantize(Decimal("0.01")), max(d for _, d in priced), as_of)
    return out


def _series(s: Session, member_id: int, accts: dict[int, Acct], today: date) -> dict[int, list[Entry]]:
    """Every dated value per account, oldest first (same day: by RANK). Five queries plus the balance lookup."""
    out: dict[int, list[Entry]] = {}

    def add(aid: int | None, e: Entry) -> None:
        if aid in accts:
            out.setdefault(aid, []).append(e)

    for aid, day, closing in s.execute(select(Statement.account_id, Statement.period_end, Statement.closing)
                                       .where(Statement.member_id == member_id)):
        if aid in accts:  # a card statement's closing is minus what is due
            add(aid, (day, -closing if accts[aid].kind == "card" else closing, "statement", day))
    for aid, (amount, day) in balances(s, member_id).items():
        if aid in accts and accts[aid].kind != "card":
            add(aid, (day, amount, "statement", day))
    for v in s.scalars(select(AccountValuation).where(AccountValuation.member_id == member_id)):
        add(v.account_id, (v.as_of, v.amount, v.source, v.as_of))
    for aid, (amount, day, units_of) in _priced(s, member_id, accts, today).items():
        add(aid, (day, amount, "prices", units_of))
    for v in out.values():
        v[:] = sorted((x for x in v if x[0] <= today), key=lambda x: (x[0], RANK[x[2]]))
    for aid, v in out.items():
        if accts[aid].subtype == "epf" and v:
            v += _epf_estimate(v, today)
    return {k: v for k, v in out.items() if v}


def _epf_estimate(epf: list[Entry], today: date) -> list[Entry]:
    """EPF is credited on the 1st: from the last value, add the usual monthly credit on each 1st since, for up to
    6 months. The usual credit is the commonest month-on-month rise over the last 6 months."""
    by_month = {(d.year, d.month): v for d, v, _, _ in epf}
    months = sorted(by_month)[-7:]
    rises = [by_month[b] - by_month[a] for a, b in zip(months, months[1:])
             if (b[0] * 12 + b[1]) - (a[0] * 12 + a[1]) == 1 and by_month[b] > by_month[a]]
    if not rises:
        return []
    step = max(set(rises), key=rises.count)
    last_day, value, _, _ = epf[-1]
    out, d = [], date(last_day.year + (last_day.month == 12), last_day.month % 12 + 1, 1)
    while d <= today and len(out) < 6:
        value += step
        out.append((d, value, "estimate", d))
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def _flows(s: Session, member_id: int, accts: dict[int, Acct], series: dict[int, list[Entry]],
           since: date) -> dict[int, list[Flow]]:
    """Money that moves an account between its values: a card's purchases (+ owed) and payments and refunds
    (− owed); an investment debit into the one account of its subtype, or the most recently valued of several."""
    out: dict[int, list[Flow]] = {}
    cards = [a for a in accts if accts[a].kind == "card"]
    if cards:
        for aid, day, direction, amount in s.execute(
                select(Txn.account_id, Txn.occurred_at, Txn.direction, Txn.amount)
                .where(Txn.member_id == member_id, Txn.account_id.in_(cards), Txn.occurred_at >= since,
                       Txn.status.is_distinct_from("flagged"), Txn.split_of.is_(None))):
            out.setdefault(aid, []).append((day, amount if direction == "debit" else -amount))
    into: dict[str, int] = {}
    for aid, a in accts.items():
        if a.subtype in ("mf", "stocks", "ppf", "fd") and aid in series:
            best = into.get(a.subtype)
            if best is None or series[aid][-1][0] > series[best][-1][0]:
                into[a.subtype] = aid
    if into:
        for day, amount, merchant, narration in s.execute(
                select(Txn.occurred_at, Txn.amount, Txn.merchant_norm, Txn.narration)
                .where(Txn.member_id == member_id, Txn.bucket == "invest", Txn.direction == "debit", Txn.occurred_at >= since)):
            sub = next((k for rx, k in INVESTS_INTO if rx.search(merchant or "")), None) or \
                next((k for rx, k in INVESTS_INTO if rx.search(narration or "")), None)
            if sub in into:
                out.setdefault(into[sub], []).append((day, amount))
    return out


def _value_at(series: list[Entry], day: date, carry_back: bool, flows: list[Flow]) -> Decimal | None:
    """The newest value on or before `day`, plus the flows after what that value includes."""
    best = None
    for e in series:
        if e[0] > day:
            break
        best = e
    if best is None:
        return series[0][1] if carry_back else None
    return best[1] + sum((a for d, a in flows if best[3] < d <= day), ZERO)


def _month_end(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def _pct(part: Decimal, whole: Decimal) -> float:
    return float(round(abs(part) * 100 / whole, 1)) if whole else 0.0


def live(s: Session, member_id: int, today: date) -> dict[str, Any]:
    accts = _accounts(s, member_id)
    series = _series(s, member_id, accts, today)
    owed, owe = loans.balances(s, member_id)  # open loans: money owed to you is an asset, money you owe a liability
    if not series:
        return {"as_of": today, "net_worth": None, "liquid": None, "components": [], "by_asset_class": {},
                "changes": [], "history": [], "months": [], "projection": None}
    flows = _flows(s, member_id, accts, series, min(v[0][0] for v in series.values()))
    # A feed (statements, prices) is what history waits for. An account with only values set by hand, or a store of
    # value (gold, property), held its first value before it was set, back to its opening date if the member gave one.
    fed = {aid for aid, v in series.items() if any(e[2] in ("statement", "prices") for e in v)}
    carry = {aid for aid in series if KINDS[accts[aid].kind].carry_back or aid not in fed}

    def at(day: date) -> dict[int, Decimal]:
        """Each counted account's signed value: liabilities negative."""
        vals: dict[int, Decimal] = {}
        for aid, v in series.items():
            a = accts[aid]
            if a.opened_at and day < a.opened_at:
                continue
            x = _value_at(v, day, aid in carry, flows.get(aid, []))
            if x is not None:
                vals[aid] = -x if a.liability else x
        return vals

    # Month-end points from the first month every fed asset account is known (one with an opening date isn't
    # waited for: before its first value it held nothing); liabilities count from their first value. Then today.
    core = [v[0][0] for aid, v in series.items()
            if aid in fed and aid not in carry and not accts[aid].liability and accts[aid].opened_at is None]
    first = _month_end(max(core) if core else min(v[0][0] for v in series.values()))
    points: list[tuple[date, dict[int, Decimal]]] = []
    d = first
    while d < today:
        points.append((d, at(d)))
        d = _month_end(d + timedelta(days=1))
    now = at(today)
    points.append((today, now))
    nw = {day: sum(c.values(), ZERO) for day, c in points}

    total = sum(now.values(), ZERO) + owed - owe
    # Shares are of what you own, or for a liability of what you owe: a share of a net total means nothing once it
    # nears zero or goes negative.
    own = sum((v for v in now.values() if v > 0), ZERO) + owed
    owes = -sum((v for v in now.values() if v < 0), ZERO) + owe

    def share(v: Decimal) -> float:
        return _pct(v, own if v > 0 else owes)

    base = next((c for day, c in reversed(points) if day < today.replace(day=1)), None)
    comps, by_class = [], {}
    for aid in sorted(now, key=lambda a: (-abs(now[a]), a)):
        a, (day, _, source, _) = accts[aid], series[aid][-1]
        by_class[a.asset_class] = by_class.get(a.asset_class, ZERO) + now[aid]
        comps.append({"key": str(aid), "account_id": aid, "label": a.label, "kind": a.kind, "subtype": a.subtype,
                      "side": KINDS[a.kind].side, "asset_class": a.asset_class, "liquid": a.liquid,
                      "amount": fmt(now[aid]), "share_pct": share(now[aid]), "source": source, "as_of": day,
                      "stale": (today - day).days > STALE_DAYS and source != "estimate" and not flows.get(aid),
                      "editable": True,
                      "change_since": fmt(now[aid] - base[aid]) if base and aid in base else None})
    for key, label, amount, side in (("loans_given", "Loans given", owed, "asset"),
                                     ("loans_taken", "Loans taken", -owe, "liability")):
        if amount:
            by_class["loans"] = by_class.get("loans", ZERO) + amount
            comps.append({"key": key, "account_id": None, "label": label, "kind": None, "subtype": None, "side": side,
                          "asset_class": "loans", "liquid": False, "amount": fmt(amount), "share_pct": share(amount),
                          "source": "loans", "as_of": today, "stale": False, "editable": False, "change_since": None})
    liquid_ids = {aid for aid, a in accts.items() if a.liquid}
    liquid_now = sum((v for aid, v in now.items() if aid in liquid_ids), ZERO)

    changes = []
    fy_start = date(today.year if today.month >= 4 else today.year - 1, 4, 1)
    for label, since in (("month", today.replace(day=1)), ("year", date(today.year, 1, 1)), ("fy", fy_start)):
        prior = [nw[day] for day, _ in points if day < since]
        base_nw = prior[-1] if prior else None
        net_now = nw[today]
        changes.append({"period": label, "since": since,
                        "amount": fmt(net_now - base_nw) if base_nw is not None else None,
                        "pct": float(round((net_now - base_nw) * 100 / base_nw, 1)) if base_nw else None})

    history = [{"date": day, "net_worth": fmt(nw[day]), "kind": "live" if day == today else "snapshot"} for day, _ in points]

    # Month by month: change = liquid change + contributions + market (and anything set by hand).
    liq = [(day, nw[day], sum((v for aid, v in c.items() if aid in liquid_ids), ZERO)) for day, c in points]
    intervals = list(zip(liq, liq[1:]))[-6:]
    invest = s.execute(
        select(Txn.occurred_at, Txn.amount)
        .where(Txn.member_id == member_id, Txn.direction == "debit", Txn.bucket == "invest",
               Txn.occurred_at > intervals[0][0][0])
    ).all() if intervals else []
    months = []
    for (d0, nw0, lq0), (d1, nw1, lq1) in intervals:
        contrib = sum((a for day, a in invest if d0 < day <= d1), ZERO)
        change, cash = nw1 - nw0, lq1 - lq0
        months.append({"start": d0, "end": d1, "start_value": fmt(nw0), "end_value": fmt(nw1),
                       "change": fmt(change), "cash_change": fmt(cash), "contributions": fmt(contrib),
                       "market": fmt(change - cash - contrib), "live": d1 == today})
    months.reverse()

    projection = None
    recent = [(day, nw[day]) for day, _ in points if day != today][-(PROJECTION_MONTHS + 1):]
    if len(recent) >= 3:
        span = (recent[-1][0].year - recent[0][0].year) * 12 + recent[-1][0].month - recent[0][0].month
        if span > 0:
            per_month = (recent[-1][1] - recent[0][1]) / span
            projection = {"monthly_change": fmt(per_month), "basis_months": span,
                          "points": [{"date": _add_months(today, n), "net_worth": fmt(total + per_month * n)}
                                     for n in (3, 6)]}
    return {"as_of": today, "net_worth": fmt(total), "liquid": fmt(liquid_now), "components": comps,
            "by_asset_class": {k: fmt(v) for k, v in by_class.items()}, "changes": changes,
            "history": history, "months": months, "projection": projection}


# --- one account, day by day ----------------------------------------------------------------------

def _balance_days(s: Session, member_id: int, account_id: int, today: date) -> list[Entry]:
    """The account's balance on each day one is printed (statement lines, balance mails), then moved on by the
    pending alerts after the newest one, as `recurring.balances` does. Two queries."""
    days: dict[date, Decimal] = {}
    anchor = None
    for day, bal, parser in s.execute(
            select(Observation.occurred_at, Observation.balance_after, Observation.parser)
            .where(Observation.member_id == member_id, Observation.account_id == account_id,
                   Observation.balance_after.is_not(None), Observation.occurred_at <= today)
            .order_by(Observation.occurred_at, Observation.id)):
        days[day] = bal
        anchor = alerts_from(day, parser)
    if anchor is None:
        return []
    for day, direction, amount in s.execute(
            select(Txn.occurred_at, Txn.direction, Txn.amount)
            .where(Txn.member_id == member_id, Txn.account_id == account_id, Txn.status == "pending",
                   Txn.split_of.is_(None), Txn.occurred_at >= anchor, Txn.occurred_at <= today)
            .order_by(Txn.occurred_at, Txn.id)):
        now = days[max(days)]
        days[day] = now + amount if direction == "credit" else now - amount
    return [(day, v, "statement", day) for day, v in sorted(days.items())]


def _priced_days(s: Session, member_id: int, acct: Acct, accts: dict[int, Acct], today: date) -> list[Entry]:
    """The account's holdings on every day a price is stored: its newest holdings on or before that day × each
    security's newest price up to it, kept only once a price is at least as new as those holdings. Two queries."""
    held: dict[date, list[tuple[str, Decimal]]] = {}
    for as_of, isin, units in s.execute(
            select(Holding.as_of, Holding.isin, Holding.units)
            .where(Holding.member_id == member_id, Holding.account_id == acct.id, Holding.isin.is_not(None),
                   Holding.as_of <= today)):
        held.setdefault(as_of, []).append((isin, units))
    if not held:
        return []
    latest = _latest_holdings(s, member_id, accts, today).get(acct.id)
    counted = {r.isin for r in latest[1]} if latest else set()  # a security counted in another account stays there
    by_day: dict[date, list[tuple[str, Decimal]]] = {}
    for isin, day, close in s.execute(select(Price.isin_or_symbol, Price.date, Price.close)
                                      .where(Price.isin_or_symbol.in_({i for rows in held.values() for i, _ in rows}),
                                             Price.date <= today)):
        by_day.setdefault(day, []).append((isin, close))
    held_days = sorted(held)
    last: dict[str, tuple[Decimal, date]] = {}
    out: list[Entry] = []
    for day in sorted(by_day):
        last.update({isin: (close, day) for isin, close in by_day[day]})
        h = max((d for d in held_days if d <= day), default=None)
        if h is None:
            continue
        rows = [(last[i], u) for i, u in held[h] if i in last and i in counted]
        if rows and max(px[1] for px, _ in rows) >= h:
            out.append((day, sum(((px[0] * u) for px, u in rows), ZERO).quantize(Decimal("0.01")), "prices", h))
    return out


def account_history(s: Session, member_id: int, account_id: int, today: date) -> dict[str, Any]:
    """One account's value on every day from its first known value to today, ending at what `live` shows, and
    the dated values behind it. Balances read every printed balance; holdings every stored price (the newest
    unbroken daily run; statement steps before it); the rest carry each value forward, with the flows since."""
    accts = _accounts(s, member_id)
    acct = accts.get(account_id)
    if acct is None:
        a = s.scalars(select(Account).where(Account.member_id == member_id, Account.id == account_id)).first()
        if a is None:
            raise NotFound("account not found")
        acct = Acct(a.id, account_label(a.institution, a.name, a.mask) or a.institution, a.kind, a.subtype,
                    asset_class(a.kind, a.asset_class), liquid(a.kind, a.liquid), a.opened_at)
        accts = {**accts, a.id: acct}
    full = _series(s, member_id, accts, today)
    series = full.get(account_id, [])
    priced_from = None
    if acct.kind != "card" and (days := _balance_days(s, member_id, account_id, today)):
        series = sorted(series + days, key=lambda e: (e[0], RANK[e[2]]))
    if acct.kind == "holding":
        priced = _priced_days(s, member_id, acct, accts, today)
        # Start of the newest unbroken run (≤ a week between prices): a stray older price isn't daily valuation.
        run = len(priced) - 1
        while run > 0 and (priced[run][0] - priced[run - 1][0]).days <= 7:
            run -= 1
        priced_from = priced[run][0] if priced else None
        series = sorted(series + priced[max(run, 0):], key=lambda e: (e[0], RANK[e[2]]))
    flows = _flows(s, member_id, accts, full, series[0][0]).get(account_id, []) if series else []
    points, d, i = [], series[0][0] if series else today, -1
    while series and d <= today:
        while i + 1 < len(series) and series[i + 1][0] <= d:
            i += 1
        e = series[i]
        points.append({"date": d, "value": fmt(e[1] + sum((a for b, a in flows if e[3] < b <= d), ZERO))})
        d += timedelta(days=1)
    vals = s.execute(select(AccountValuation.as_of, AccountValuation.amount, AccountValuation.source)
                     .where(AccountValuation.member_id == member_id, AccountValuation.account_id == account_id)
                     .order_by(AccountValuation.as_of.desc())).all()
    return {"account_id": account_id, "label": acct.label, "side": KINDS[acct.kind].side, "points": points,
            "priced_from": priced_from,
            "valuations": [{"as_of": d, "amount": fmt(v), "source": src} for d, v, src in vals]}


# --- writes -------------------------------------------------------------------------------------------

def _own_account(s: Session, member_id: int, account_id: int) -> Account:
    a = s.scalars(select(Account).where(Account.member_id == member_id, Account.id == account_id)).first()
    if a is None:
        raise NotFound("account not found")
    return a


def set_valuation(s: Session, ctx: MemberContext, actor: str, account_id: int, amount: Decimal, as_of: date) -> dict[str, Any]:
    """The member's value for the account on a day (what is owed, for a liability). Replaces a value on that day."""
    _own_account(s, ctx.member_id, account_id)
    stmt = insert(AccountValuation).values(member_id=ctx.member_id, account_id=account_id, amount=amount, as_of=as_of,
                                           source="manual")
    s.execute(stmt.on_conflict_do_update(constraint="uq_account_valuation_account_id_as_of",
                                         set_={"amount": stmt.excluded.amount, "source": "manual"}))
    audit(s, ctx, actor, "account.valuation", f"account:{account_id}", {"as_of": as_of.isoformat()})
    return {"account_id": account_id, "amount": fmt(amount), "as_of": as_of, "source": "manual"}


def delete_valuation(s: Session, ctx: MemberContext, actor: str, account_id: int, as_of: date) -> None:
    _own_account(s, ctx.member_id, account_id)
    v = s.scalars(select(AccountValuation).where(AccountValuation.member_id == ctx.member_id,
                                                 AccountValuation.account_id == account_id,
                                                 AccountValuation.as_of == as_of)).first()
    if v is None:
        raise NotFound("no value on that date")
    if v.source == "statement":
        raise Invalid("this value was read from a statement; set your own value on that date instead")
    s.delete(v)
    audit(s, ctx, actor, "account.valuation.delete", f"account:{account_id}", {"as_of": as_of.isoformat()})


def _component_account(s: Session, member_id: int, institution: str, key: str) -> int:
    """The account a statement's printed value belongs to (a PPF balance on an SBI statement, a CAS total): the
    member's account of that kind and subtype at that institution, else their only one of that kind and subtype
    (one they set up by hand), else a new one they can rename."""
    kind, subtype = COMPONENT_ACCOUNTS[key]
    q = select(Account.id).where(Account.member_id == member_id, Account.kind == kind, Account.subtype == subtype)
    found = s.scalars(q.where(func.upper(Account.institution) == institution.upper()).order_by(Account.id)).first()
    if found is None:
        ids = s.scalars(q).all()
        found = ids[0] if len(ids) == 1 else None
    if found is None:
        a = Account(member_id=member_id, kind=kind, subtype=subtype, institution=institution,
                    name=f"{institution} {SUBTYPE_LABELS[subtype]}")
        s.add(a)
        s.flush()
        found = a.id
    return found


def record_components(s: Session, member_id: int, institution: str, values: tuple[tuple[str, Decimal], ...],
                      as_of: date) -> None:
    """Values a statement prints for accounts it isn't about. Newest wins in `live`; re-reading a statement corrects
    its own date and never overrides a value set by hand on that day."""
    for key, amount in values:
        if key not in COMPONENT_ACCOUNTS or amount < 0:
            continue
        stmt = insert(AccountValuation).values(member_id=member_id, account_id=_component_account(s, member_id, institution, key),
                                               amount=amount, as_of=as_of, source="statement")
        s.execute(stmt.on_conflict_do_update(constraint="uq_account_valuation_account_id_as_of",
                                             set_={"amount": stmt.excluded.amount},
                                             where=AccountValuation.source == "statement"))


def holding_account(s: Session, member_id: int, source: str, isin: str | None, name: str | None) -> int:
    """The account a statement's holding sits in: for a CAS, its demat securities (an INE ISIN or an ETF) or its funds;
    for a fund house's statement, its funds. Matched by the statement's source as the institution."""
    institution = "CDSL" if source == "cdsl_cas" else source.upper()
    subtype = "stocks" if source == "cdsl_cas" and _equity(isin, name) else "mf"
    found = s.scalars(select(Account.id).where(Account.member_id == member_id, Account.kind == "holding",
                                               Account.subtype == subtype,
                                               func.upper(Account.institution) == institution).order_by(Account.id)).first()
    if found is None:
        a = Account(member_id=member_id, kind="holding", subtype=subtype, institution=institution,
                    name=f"{institution} {SUBTYPE_LABELS[subtype]}")
        s.add(a)
        s.flush()
        found = a.id
    return found


def holdings(s: Session, member_id: int) -> dict[str, Any]:
    """Latest units per holding × the newest price on or before today, with the account each sits in."""
    rows = s.execute(select(Holding, Account).outerjoin(Account, Account.id == Holding.account_id)
                     .where(Holding.member_id == member_id)
                     .order_by(Holding.isin, Holding.as_of.desc(), Holding.id.desc())).all()
    latest: dict[Any, tuple[Holding, Account | None]] = {}
    for h, a in rows:
        latest.setdefault((h.account_id, h.isin or h.name), (h, a))
    keys = [h.isin for h, _ in latest.values() if h.isin]
    prices: dict[str, tuple[Decimal, date]] = {}
    if keys:
        ranked = (select(Price.isin_or_symbol, Price.close, Price.date,
                         func.row_number().over(partition_by=Price.isin_or_symbol,
                                                order_by=Price.date.desc()).label("rn"))
                  .where(Price.isin_or_symbol.in_(keys)).subquery())
        prices = {r.isin_or_symbol: (r.close, r.date) for r in s.execute(select(ranked).where(ranked.c.rn == 1))}
    items = []
    for h, a in latest.values():
        price = prices.get(h.isin or "")
        items.append({"name": h.name, "isin": h.isin, "units": str(h.units), "units_as_of": h.as_of,
                      "source": h.source, "account_id": h.account_id,
                      "account": account_label(a.institution, a.name, a.mask) if a else None,
                      "price": str(price[0]) if price else None, "price_date": price[1] if price else None,
                      "value": fmt((h.units * price[0]).quantize(Decimal("0.01"))) if price else None})
    items.sort(key=lambda x: -Decimal(x["value"] or 0))
    return {"items": items}
