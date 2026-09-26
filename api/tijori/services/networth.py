"""Net-worth snapshots: the sheet, 1:1. Liquid = SBI + HDFC savings + FD, as the sheet defines it."""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.legacy import SheetRow
from tijori.models import Snapshot
from tijori.money import ZERO, fmt
from tijori.services.common import audit

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
