"""Net-worth snapshots: the sheet, 1:1. Liquid = SBI + HDFC savings + FD, as the sheet defines it."""

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.legacy import SheetRow
from tijori.models import Account, ComponentValue, Holding, Price, Snapshot, Statement, Txn
from tijori.money import ZERO, fmt
from tijori.services.common import audit
from tijori.services import loans
from tijori.services.recurring import balances

COMPONENT_KEYS = ("sbi", "hdfc", "fd", "stocks", "mf", "ppf", "epf", "gold", "other")
LIQUID_KEYS = ("sbi", "hdfc", "fd")
LABELS = {
    "sbi": ("SBI savings", "cash"), "hdfc": ("HDFC savings", "cash"), "fd": ("Fixed deposits", "deposits"),
    "stocks": ("Stocks", "equity"), "mf": ("Mutual funds", "equity"), "ppf": ("PPF", "retirement"),
    "epf": ("EPF", "retirement"), "gold": ("Gold", "gold"), "other": ("Other", "other"),
}
MAX_REMARK = 2000


def _inr_short(v: Decimal) -> str:
    sign = "+" if v >= 0 else "−"
    a = abs(v)
    if a >= 100_000:
        return f"{sign}₹{a / 100_000:.2f}L"
    if a >= 1_000:
        return f"{sign}₹{a / 1_000:.1f}K"
    return f"{sign}₹{a:.0f}"


def build_commentary(prev: dict[str, Any] | None, cur: dict[str, Any]) -> str | None:
    """Template commentary (no AI), e.g. "Net worth +₹2.19L. Biggest mover: HDFC savings +₹1.66L."."""
    if prev is None:
        return None
    text = f"Net worth {_inr_short(cur['net_worth'] - prev['net_worth'])}."
    deltas = {k: cur["components"].get(k, ZERO) - prev["components"].get(k, ZERO)
              for k in set(cur["components"]) | set(prev["components"])}
    if deltas:
        key = max(sorted(deltas), key=lambda k: abs(deltas[k]))
        if deltas[key] != 0:
            text += f" Biggest mover: {LABELS.get(key, (key.title(),))[0]} {_inr_short(deltas[key])}."
    return text


def _liquid(components: dict[str, Decimal]) -> Decimal | None:
    parts = [components[k] for k in LIQUID_KEYS if k in components]
    return sum(parts, ZERO) if parts else None


def list_networth(s: Session, member_id: int) -> dict[str, Any]:
    rows = s.scalars(select(Snapshot).where(Snapshot.member_id == member_id).order_by(Snapshot.date)).all()
    snaps: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for r in rows:
        comps = {k: Decimal(v) for k, v in r.components_json.items()}
        liquid = _liquid(comps)
        cur = {"date": r.date, "components": comps, "net_worth": r.net_worth, "liquid": liquid}
        commentary, source = r.commentary, "stored"
        if not commentary:
            commentary, source = build_commentary(prev, cur), "template"
        snaps.append({
            "date": r.date,
            "components": {k: fmt(comps[k]) if k in comps else None for k in COMPONENT_KEYS},
            "net_worth": fmt(r.net_worth),
            "net_change": fmt(r.net_worth - prev["net_worth"]) if prev else None,
            "liquid": fmt(liquid) if liquid is not None else None,
            "liquid_change": fmt(liquid - prev["liquid"])
            if prev and liquid is not None and prev["liquid"] is not None else None,
            "remark": r.remark, "commentary": commentary,
            "commentary_source": source if commentary else None, "locked": r.locked,
        })
        prev = cur
    latest = None
    if prev is not None:
        total = sum(prev["components"].values(), ZERO)
        comps_out, by_class = [], {}
        for key, amount in sorted(prev["components"].items(), key=lambda kv: (-kv[1], kv[0])):
            label, asset_class = LABELS.get(key, (key.replace("_", " ").title(), "other"))
            by_class[asset_class] = by_class.get(asset_class, ZERO) + amount
            comps_out.append({"key": key, "label": label, "asset_class": asset_class, "amount": fmt(amount),
                              "share_pct": float(round(amount * 100 / total, 1)) if total else 0.0})
        latest = {"date": prev["date"], "net_worth": fmt(prev["net_worth"]), "components": comps_out,
                  "by_asset_class": {k: fmt(v) for k, v in by_class.items()}}
    snaps.reverse()  # newest first
    return {"snapshots": snaps, "latest": latest}


def update_remark(s: Session, ctx: MemberContext, actor: str, day: date, remark: str | None) -> dict[str, Any] | None:
    row = s.execute(
        update(Snapshot).where(Snapshot.member_id == ctx.member_id, Snapshot.date == day)
        .values(remark=remark).returning(Snapshot.date, Snapshot.remark)
    ).first()
    if row is None:
        return None
    audit(s, ctx, actor, "snapshot.remark", f"snapshot:{day.isoformat()}", {"remark_length": len(remark or "")})
    return {"date": row.date, "remark": row.remark}


def upsert_sheet(s: Session, member_id: int, sheet: list[SheetRow]) -> int:
    """Idempotent. A remark edited in Tijori wins over the sheet's on re-import."""
    if not sheet:
        return 0
    stmt = insert(Snapshot).values([
        dict(member_id=member_id, date=r.month, components_json={k: str(v) for k, v in r.components.items()},
             net_worth=r.net_worth, liquid=r.liquid, remark=r.remark, commentary=r.commentary, locked=True)
        for r in sheet
    ])
    s.execute(stmt.on_conflict_do_update(
        constraint="uq_snapshot_member_id_date",
        set_={"components_json": stmt.excluded.components_json, "net_worth": stmt.excluded.net_worth,
              "liquid": stmt.excluded.liquid,
              "remark": func.coalesce(Snapshot.remark, stmt.excluded.remark),
              "commentary": func.coalesce(stmt.excluded.commentary, Snapshot.commentary)},
    ))
    return len(sheet)


# --- live net worth -----------------------------------------------------------------------------
# Each component takes its newest dated value from statements, the CAS, daily prices or a value set by hand;
# history is those values at each month-end. The imported sheet isn't read here.

STALE_DAYS = 30
PROJECTION_MONTHS = 12
BANK_KEYS = {"SBI": "sbi", "HDFC": "hdfc"}
HISTORY_CORE = ("sbi", "hdfc", "stocks", "mf", "ppf", "epf")  # history starts once each of these (if held) is known
CARRY_BACK = ("gold", "other")  # before its first value, a component holds that value as a base
ETF = re.compile(r"\bETF\b|BEES", re.I)


def _bank_balances(s: Session, member_id: int) -> dict[str, tuple[Decimal, date]]:
    kinds = dict(s.execute(select(Account.id, Account.institution).where(
        Account.member_id == member_id, Account.kind == "bank")).all())
    out: dict[str, tuple[Decimal, date]] = {}
    for account_id, (amount, as_of) in balances(s, member_id).items():
        key = BANK_KEYS.get((kinds.get(account_id) or "").upper())
        if key is None:
            continue
        prev = out.get(key)
        out[key] = (amount + (prev[0] if prev else ZERO), max(as_of, prev[1]) if prev else as_of)
    return out


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def _priced(s: Session, member_id: int, today: date) -> dict[str, tuple[Decimal, date, date]]:
    """Stocks and funds at the newest daily price (NSE close, AMFI NAV) on the latest CAS holdings; only when
    a price is newer than the CAS. Units bought since the CAS show up with the next one. One query."""
    latest = s.scalar(select(func.max(Holding.as_of)).where(Holding.member_id == member_id, Holding.source == "cdsl_cas"))
    if latest is None:
        return {}
    px = (select(Price.close, Price.date).where(Price.isin_or_symbol == Holding.isin, Price.date <= today)
          .order_by(Price.date.desc()).limit(1).lateral())
    rows = s.execute(select(Holding.isin, Holding.name, Holding.units, px.c.close, px.c.date)
                     .outerjoin(px, true())
                     .where(Holding.member_id == member_id, Holding.source == "cdsl_cas", Holding.as_of == latest)).all()
    out: dict[str, list[Any]] = {}
    for isin, name, units, close, day in rows:
        if close is None:
            continue
        key = "stocks" if (isin or "").startswith("INE") or ETF.search(name or "") else "mf"
        v = out.setdefault(key, [ZERO, latest])
        v[0] += (units * close).quantize(Decimal("0.01"))
        v[1] = max(v[1], day)
    return {k: (v[0], v[1], latest) for k, v in out.items() if v[1] > latest}


Entry = tuple[date, Decimal, str, date]  # as of, amount, source, what it already includes (a priced CAS: its date)


def _series(s: Session, member_id: int, today: date) -> dict[str, list[Entry]]:
    """Every dated value per component, oldest first: bank statement closings and the newest balance, values
    read from statements or set by hand, and daily prices. Not the sheet: its EPF, gold and other rows were
    imported as values set by hand. Four queries plus the balance lookup."""
    out: dict[str, list[Entry]] = {}
    for inst, day, amount in s.execute(
            select(Account.institution, Statement.period_end, func.sum(Statement.closing))
            .join(Account, Account.id == Statement.account_id)
            .where(Statement.member_id == member_id, Account.kind == "bank")
            .group_by(Account.institution, Statement.period_end)):
        if key := BANK_KEYS.get((inst or "").upper()):
            out.setdefault(key, []).append((day, amount, "statement", day))
    for key, (amount, day) in _bank_balances(s, member_id).items():
        out.setdefault(key, []).append((day, amount, "statement", day))
    for cv in s.scalars(select(ComponentValue).where(ComponentValue.member_id == member_id)):
        out.setdefault(cv.key, []).append((cv.as_of, cv.amount, "statement" if cv.source == "statement" else "manual", cv.as_of))
    for key, (amount, day, units_of) in _priced(s, member_id, today).items():
        out.setdefault(key, []).append((day, amount, "prices", units_of))
    for key, v in out.items():
        v[:] = sorted((x for x in v if x[0] <= today), key=lambda x: x[0])
    if out.get("epf"):
        out["epf"] += _epf_estimate(out["epf"], today)
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


def _value_at(series: list[Entry], day: date, carry_back: bool, bought: list[tuple[date, Decimal]]) -> Decimal | None:
    """The newest value on or before `day`, plus money invested into it after what that value includes (units
    bought since the CAS, a deposit the next statement will show)."""
    best = None
    for e in series:
        if e[0] > day:
            break
        best = e
    if best is None:
        return series[0][1] if carry_back else None
    return best[1] + sum((a for d, a in bought if best[3] < d <= day), ZERO)


# Where an investment lands, by the payee label the classifier gives it; unmatched ones join no component.
INVESTS_INTO = ((re.compile(r"MF|SIP|NACH", re.I), "mf"), (re.compile(r"stock", re.I), "stocks"),
                (re.compile(r"PPF", re.I), "ppf"), (re.compile(r"\bFD\b|fixed deposit", re.I), "fd"))


def _bought(s: Session, member_id: int, since: date) -> dict[str, list[tuple[date, Decimal]]]:
    out: dict[str, list[tuple[date, Decimal]]] = {}
    for day, amount, merchant, narration in s.execute(
            select(Txn.occurred_at, Txn.amount, Txn.merchant_norm, Txn.narration)
            .where(Txn.member_id == member_id, Txn.bucket == "invest", Txn.direction == "debit", Txn.occurred_at >= since)):
        key = next((k for rx, k in INVESTS_INTO if rx.search(merchant or "")), None) or \
            next((k for rx, k in INVESTS_INTO if rx.search(narration or "")), None)
        if key:
            out.setdefault(key, []).append((day, amount))
    return out


def _month_end(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)


def live(s: Session, member_id: int, today: date) -> dict[str, Any]:
    series = _series(s, member_id, today)
    owed, owe = loans.balances(s, member_id)  # open loans: money owed to you is an asset, money you owe a liability
    if not series:
        return {"as_of": today, "net_worth": None, "liquid": None, "components": [], "by_asset_class": {},
                "changes": [], "history": [], "months": [], "projection": None}

    bought = _bought(s, member_id, min(v[0][0] for v in series.values()))

    def at(day: date) -> dict[str, Decimal]:
        vals = {k: _value_at(series[k], day, k in CARRY_BACK, bought.get(k, [])) for k in COMPONENT_KEYS if k in series}
        return {k: v for k, v in vals.items() if v is not None}

    # Month-end points from the first month every core account is known; then today.
    core = [series[k][0][0] for k in HISTORY_CORE if k in series]
    first = _month_end(max(core)) if core else _month_end(min(v[0][0] for v in series.values()))
    points: list[tuple[date, dict[str, Decimal]]] = []
    d = first
    while d < today:
        points.append((d, at(d)))
        d = _month_end(d + timedelta(days=1))
    now = at(today)
    points.append((today, now))
    nw = {day: sum(c.values(), ZERO) for day, c in points}

    total = sum(now.values(), ZERO) + owed - owe
    base = next((c for day, c in reversed(points) if day < today.replace(day=1)), None)
    comps, by_class = [], {}
    for key in COMPONENT_KEYS:
        if key not in now:
            continue
        day, _, source, _ = series[key][-1]
        label, asset_class = LABELS[key]
        by_class[asset_class] = by_class.get(asset_class, ZERO) + now[key]
        comps.append({"key": key, "label": label, "asset_class": asset_class, "amount": fmt(now[key]),
                      "share_pct": float(round(now[key] * 100 / total, 1)) if total else 0.0,
                      "source": source, "as_of": day, "stale": (today - day).days > STALE_DAYS and source != "estimate",
                      "editable": True,
                      "change_since": fmt(now[key] - base[key]) if base and key in base else None})
    for key, label, amount in (("loans_given", "Loans given", owed), ("loans_taken", "Loans taken", -owe)):
        if amount:
            by_class["loans"] = by_class.get("loans", ZERO) + amount
            comps.append({"key": key, "label": label, "asset_class": "loans", "amount": fmt(amount),
                          "share_pct": float(round(amount * 100 / total, 1)) if total else 0.0, "source": "loans",
                          "as_of": today, "stale": False, "editable": False, "change_since": None})
    liquid = sum((now[k] for k in LIQUID_KEYS if k in now), ZERO)

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

    # Month by month: change = cash change + contributions + market (and anything set by hand).
    liq = [(day, nw[day], sum((c[k] for k in LIQUID_KEYS if k in c), ZERO)) for day, c in points]
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
    return {"as_of": today, "net_worth": fmt(total), "liquid": fmt(liquid), "components": comps,
            "by_asset_class": {k: fmt(v) for k, v in by_class.items()}, "changes": changes,
            "history": history, "months": months, "projection": projection}


def set_component(s: Session, ctx: MemberContext, actor: str, key: str, amount: Decimal, as_of: date) -> dict[str, Any]:
    stmt = insert(ComponentValue).values(member_id=ctx.member_id, key=key, amount=amount, as_of=as_of)
    s.execute(stmt.on_conflict_do_update(constraint="uq_component_value_member_id_key_as_of",
                                         set_={"amount": stmt.excluded.amount}))
    audit(s, ctx, actor, "networth.component", f"component:{key}", {"as_of": as_of.isoformat()})
    return {"key": key, "amount": fmt(amount), "as_of": as_of}


def holdings(s: Session, member_id: int) -> dict[str, Any]:
    """Latest units per holding × the newest price on or before today. Empty until the CDSL CAS parser lands."""
    rows = s.scalars(select(Holding).where(Holding.member_id == member_id)
                     .order_by(Holding.isin, Holding.as_of.desc(), Holding.id.desc())).all()
    latest: dict[str, Any] = {}
    for h in rows:
        latest.setdefault(h.isin or h.name, h)
    keys = [h.isin for h in latest.values() if h.isin]
    prices: dict[str, tuple[Decimal, date]] = {}
    if keys:
        ranked = (select(Price.isin_or_symbol, Price.close, Price.date,
                         func.row_number().over(partition_by=Price.isin_or_symbol,
                                                order_by=Price.date.desc()).label("rn"))
                  .where(Price.isin_or_symbol.in_(keys)).subquery())
        prices = {r.isin_or_symbol: (r.close, r.date) for r in s.execute(select(ranked).where(ranked.c.rn == 1))}
    items = []
    for h in latest.values():
        price = prices.get(h.isin or "")
        items.append({"name": h.name, "isin": h.isin, "units": str(h.units), "units_as_of": h.as_of,
                      "source": h.source, "price": str(price[0]) if price else None,
                      "price_date": price[1] if price else None,
                      "value": fmt((h.units * price[0]).quantize(Decimal("0.01"))) if price else None})
    items.sort(key=lambda x: -Decimal(x["value"] or 0))
    return {"items": items}
