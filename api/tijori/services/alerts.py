"""All rule alerts for a month cycle: recurring (duplicate, bounce risk, price increase, missed) plus
budget pace. Figures only; the same list feeds the Overview and the push notifications."""

from typing import Any

from sqlalchemy.orm import Session


from tijori.services import budgets, ops, recurring


def month_alerts(s: Session, member_id: int, month: str, month_start_day: int = 1) -> dict[str, Any]:
    out = recurring.alerts(s, member_id, month, month_start_day)
    b = budgets.budgets(s, member_id, month, month_start_day)
    for i in b["items"]:
        if i["state"] == "ok":
            continue
        over = i["state"] == "over"
        out["items"].append({
            "id": f"budget:{i['category_id']}:{month}:{i['state']}", "kind": "budget_over" if over else "budget_pace",
            "severity": "bad" if over else "warn",
            "title": f"{i['category']} {'over budget' if over else 'ahead of pace'}",
            "detail": f"₹{float(i['spent']):,.0f} of ₹{float(i['limit']):,.0f} by day {b['day']} of {b['days']}"
                      + ("" if over else f" · ₹{float(i['expected_by_today']):,.0f} expected"),
            "txn_ids": []})
    bk = ops.backup_status(s)
    if bk["stale"]:
        when = bk["last_ok_at"].date().isoformat() if bk["last_ok_at"] else "never"
        out["items"].append({"id": f"backup:{when}", "kind": "backup_stale", "severity": "bad", "title": "Off-site backup is stale",
                             "detail": f"Last good backup: {when}" + (f" · last run: {bk['last_detail']}" if not bk["last_ok"] else ""),
                             "txn_ids": []})
    return out


