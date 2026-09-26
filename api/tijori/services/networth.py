"""Net-worth snapshots: the sheet, 1:1. Liquid = SBI + HDFC savings + FD, as the sheet defines it."""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.legacy import SheetRow
from tijori.models import Account, ComponentValue, Holding, Price, Snapshot, Txn
from tijori.money import ZERO, fmt
from tijori.services.common import audit
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
# Each component takes its newest known value: a sheet snapshot, a value the member set, or (for the
# two savings accounts) the balance after the newest statement line. Three small queries per call.

STALE_DAYS = 30
PROJECTION_MONTHS = 12
BANK_KEYS = {"SBI": "sbi", "HDFC": "hdfc"}


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


def _as_of_value(snaps: list[Snapshot], day: date) -> Decimal | None:
    """Net worth at the newest snapshot on or before `day`."""
    best = None
    for r in snaps:
        if r.date <= day:
            best = r.net_worth
    return best


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def live(s: Session, member_id: int, today: date) -> dict[str, Any]:
    snaps = s.scalars(select(Snapshot).where(Snapshot.member_id == member_id).order_by(Snapshot.date)).all()
    picked: dict[str, tuple[Decimal, date, str]] = {}

    def offer(key: str, amount: Decimal, as_of: date, source: str) -> None:
        cur = picked.get(key)
        if cur is None or as_of >= cur[1]:
            picked[key] = (amount, as_of, source)

    for r in snaps:
        for k, v in r.components_json.items():
            if k in COMPONENT_KEYS and v is not None:
                offer(k, Decimal(v), r.date, "sheet")
    for k, (amount, as_of) in _bank_balances(s, member_id).items():
        offer(k, amount, as_of, "statement")
    for cv in s.scalars(select(ComponentValue).where(ComponentValue.member_id == member_id)
                        .order_by(ComponentValue.as_of, ComponentValue.id)).all():
        offer(cv.key, cv.amount, cv.as_of, "manual")
    if not picked:
        return {"as_of": today, "net_worth": None, "liquid": None, "components": [], "by_asset_class": {},
                "changes": [], "history": [], "months": [], "projection": None}

    total = sum((v[0] for v in picked.values()), ZERO)
    base_month = next((r for r in reversed(snaps) if r.date <= today.replace(day=1)), None)
    comps, by_class = [], {}
    for key in COMPONENT_KEYS:
        if key not in picked:
            continue
        amount, as_of, source = picked[key]
        label, asset_class = LABELS[key]
        by_class[asset_class] = by_class.get(asset_class, ZERO) + amount
        base = Decimal(base_month.components_json[key]) if base_month and base_month.components_json.get(key) \
            else None
        comps.append({"key": key, "label": label, "asset_class": asset_class, "amount": fmt(amount),
                      "share_pct": float(round(amount * 100 / total, 1)) if total else 0.0,
                      "source": source, "as_of": as_of, "stale": (today - as_of).days > STALE_DAYS,
                      "editable": True,
                      "change_since": fmt(amount - base) if base is not None else None})
    liquid = sum((picked[k][0] for k in LIQUID_KEYS if k in picked), ZERO)

    changes = []
    fy_start = date(today.year if today.month >= 4 else today.year - 1, 4, 1)
    for label, since in (("month", today.replace(day=1)), ("year", date(today.year, 1, 1)), ("fy", fy_start)):
        base_nw = _as_of_value(snaps, since)
        changes.append({"period": label, "since": since,
                        "amount": fmt(total - base_nw) if base_nw is not None else None,
                        "pct": float(round((total - base_nw) * 100 / base_nw, 1)) if base_nw else None})

    # A live point only when something is newer than the last snapshot; otherwise it repeats it.
    newest = max(v[1] for v in picked.values())
    has_live = not snaps or newest > snaps[-1].date
    history = [{"date": r.date, "net_worth": fmt(r.net_worth), "kind": "snapshot"} for r in snaps]
    if has_live:
        history.append({"date": today, "net_worth": fmt(total), "kind": "live"})

    # Month by month: change = cash change + contributions + market (and anything set by hand).
    points = [(r.date, r.net_worth, _liquid({k: Decimal(v) for k, v in r.components_json.items() if v is not None})
               or ZERO) for r in snaps]
    if has_live:
        points.append((today, total, liquid))
    intervals = list(zip(points, points[1:]))[-6:]
    invest = s.execute(
        select(Txn.occurred_at, Txn.amount)
        .where(Txn.member_id == member_id, Txn.direction == "debit", Txn.bucket == "invest",
               Txn.occurred_at >= intervals[0][0][0])
    ).all() if intervals else []
    months = []
    for (d0, nw0, lq0), (d1, nw1, lq1) in intervals:
        # The live interval includes today; snapshot intervals end the day before the next snapshot.
        is_live = has_live and d1 == today
        contrib = sum((a for d, a in invest if d0 <= d and (d <= d1 if is_live else d < d1)), ZERO)
        change, cash = nw1 - nw0, lq1 - lq0
        # Live: the split needs every cash balance to be newer than the interval start, else "market" is noise.
        cash_known = not is_live or all(picked[k][1] > d0 for k in LIQUID_KEYS if k in picked)
        months.append({"start": d0, "end": d1, "start_value": fmt(nw0), "end_value": fmt(nw1),
                       "change": fmt(change), "cash_change": fmt(cash) if cash_known else None,
                       "contributions": fmt(contrib),
                       "market": fmt(change - cash - contrib) if cash_known else None, "live": is_live})
    months.reverse()

    projection = None
    recent = points[-(PROJECTION_MONTHS + 1):]
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
