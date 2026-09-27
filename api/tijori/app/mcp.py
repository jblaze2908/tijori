"""MCP server (PLAN §9): JSON-RPC over streamable HTTP at POST /mcp, one response per request.

Auth is a member's bearer token only (never the site cookie): its SHA-256 is looked up through
mcp_member_by_token() and binds row-level security like a web session. Person UPI handles are masked in
everything returned, since the output leaves Tijori (PLAN §10). Per call: the token lookup, then the
tool's own queries (the same services the web uses).
"""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Callable

from fastapi import APIRouter, Request, Response
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from tijori.db import MemberContext
from tijori.models import McpToken
from tijori.services import alerts, networth, recurring, reports, txns
from tijori.services.common import month_start_day, today_ist

router = APIRouter()
PROTOCOL = "2025-06-18"
_BIND = text("SELECT t.member_id, t.household_id, t.token_id, set_config('tijori.member_id', t.member_id::text, true),"
             " set_config('tijori.household_id', t.household_id::text, true) FROM mcp_member_by_token(:h) AS t")
_PERSON_VPA = re.compile(r"^([a-z0-9.\-_]{1,2})[a-z0-9.\-_]*(@[a-z]+)$", re.I)


def _mask(v: Any) -> Any:
    """Person handles (name@bank) become as***@okicici; merchant QR handles are left as they are."""
    if isinstance(v, dict):
        return {k: (_mask_vpa(x) if k == "vpa" and isinstance(x, str) else _mask(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_mask(x) for x in v]
    return v


def _mask_vpa(v: str) -> str:
    if re.match(r"^(paytmqr|bharatpe|q\d|vyapar|upi\.|gpay-|ibkpos)", v, re.I):
        return v
    m = _PERSON_VPA.match(v)
    return f"{m[1]}***{m[2]}" if m else v


def _json(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(type(o).__name__)


def _month(args: dict[str, Any]) -> str:
    m = str(args.get("month") or today_ist().strftime("%Y-%m"))
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m):
        raise ValueError("month must be YYYY-MM")
    return m


def t_summary(s: Session, ctx: MemberContext, a: dict[str, Any], actor: str) -> Any:
    m = _month(a)
    return reports.summary(s, ctx.member_id, m, month_start_day(s, ctx.member_id))


def t_transactions(s: Session, ctx: MemberContext, a: dict[str, Any], actor: str) -> Any:
    def d(k: str) -> date | None:
        return date.fromisoformat(a[k]) if a.get(k) else None
    q = str(a["q"])[:100] if a.get("q") else None
    f = txns.TxnFilter(date_from=d("from"), date_to=d("to"), q=q,
                       min_amount=Decimal(str(a["min"])) if a.get("min") is not None else None,
                       max_amount=Decimal(str(a["max"])) if a.get("max") is not None else None,
                       sort=a.get("sort") if a.get("sort") in txns.SORTS else "date_desc")
    out = txns.list_txns(s, ctx.member_id, f, 1, max(1, min(int(a.get("limit", 50)), 100)))
    keep = ("id", "occurred_at", "amount", "direction", "merchant", "vpa", "category", "bucket", "account", "status")
    return {"total": out["total"], "totals": out["totals"],
            "items": [{k: (i[k]["name"] if k == "category" and i[k] else i[k]["label"] if k == "account" and i[k] else i[k])
                       for k in keep} for i in out["items"]]}


def t_subscriptions(s: Session, ctx: MemberContext, a: dict[str, Any], actor: str) -> Any:
    out = recurring.list_recurring(s, ctx.member_id)
    return {"totals": out["totals"], "items": [{k: x[k] for k in ("merchant", "kind", "cadence", "state", "amount_expected",
                                                                "monthly_cost", "next_due", "last_at", "account")}
                                               for x in out["items"]]}


def t_net_worth(s: Session, ctx: MemberContext, a: dict[str, Any], actor: str) -> Any:
    out = networth.live(s, ctx.member_id, today_ist())
    return {k: out[k] for k in ("as_of", "net_worth", "liquid", "components", "by_asset_class", "changes")}


def t_alerts(s: Session, ctx: MemberContext, a: dict[str, Any], actor: str) -> Any:
    m = _month(a)
    return alerts.month_alerts(s, ctx.member_id, m, month_start_day(s, ctx.member_id))


def t_inbox(s: Session, ctx: MemberContext, a: dict[str, Any], actor: str) -> Any:
    return txns.inbox_by_payee(s, ctx.member_id, 1, 25)


def t_categorize(s: Session, ctx: MemberContext, a: dict[str, Any], actor: str) -> Any:
    scope = a.get("scope", "this")
    if scope not in ("this", "payee"):
        raise ValueError("scope must be this or payee")
    return txns.set_category(s, ctx, actor, int(a["txn_id"]), category_id=None, category=str(a["category"])[:80], scope=scope)


_S = {"type": "object", "additionalProperties": False}
TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[Session, MemberContext, dict[str, Any], str], Any]]] = {
    "get_month_summary": ("Spend, income, invested and spend by category for one month cycle (YYYY-MM; default this month).",
                          {**_S, "properties": {"month": {"type": "string"}}}, t_summary),
    "list_transactions": ("Transactions with optional filters: from/to (YYYY-MM-DD), q (text), min/max (rupees), sort, "
                          "limit (≤100). Returns the page and totals of the whole filtered set.",
                          {**_S, "properties": {"from": {"type": "string"}, "to": {"type": "string"}, "q": {"type": "string"},
                                                "min": {"type": "number"}, "max": {"type": "number"},
                                                "sort": {"type": "string", "enum": list(txns.SORTS)},
                                                "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}, t_transactions),
    "list_subscriptions": ("Recurring charges: subscriptions, bills, SIPs; cost per month, next due date, state.", _S, t_subscriptions),
    "get_net_worth": ("Live net worth by component and asset class, with changes over month, year and FY.", _S, t_net_worth),
    "list_alerts": ("Rule alerts for a month cycle: duplicate charge, bounce risk, price increase, missed charge, budgets.",
                    {**_S, "properties": {"month": {"type": "string"}}}, t_alerts),
    "list_inbox": ("Payees waiting to be categorised, with their payments.", _S, t_inbox),
    "categorize": ("File a transaction under a category by name; scope 'payee' also files that payee's future payments.",
                   {**_S, "required": ["txn_id", "category"], "properties": {"txn_id": {"type": "integer"},
                    "category": {"type": "string"}, "scope": {"type": "string", "enum": ["this", "payee"]}}}, t_categorize),
}


def _rpc(id_: Any, result: Any = None, error: tuple[int, str] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, **({"error": {"code": error[0], "message": error[1]}} if error else {"result": result})}


def _token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


@router.post("/mcp")
async def mcp(request: Request) -> Response:
    tok = _token(request)
    if not tok:
        return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
    try:
        msg = json.loads(await request.body())
    except ValueError:
        return Response(json.dumps(_rpc(None, error=(-32700, "parse error"))), media_type="application/json")
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return Response(json.dumps(_rpc(None, error=(-32600, "invalid request"))), media_type="application/json")
    method, id_, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    engine = request.app.state.engine
    with Session(engine) as s, s.begin():
        row = s.execute(_BIND, {"h": hashlib.sha256(tok.encode()).hexdigest()}).first()
        if row is None:
            return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
        if id_ is None:  # a notification (e.g. notifications/initialized): nothing to answer
            return Response(status_code=202)
        ctx = MemberContext(row.member_id, row.household_id)
        s.execute(update(McpToken).where(McpToken.id == row.token_id).values(last_used_at=datetime.now(UTC)))
        if method == "initialize":
            out = _rpc(id_, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                             "serverInfo": {"name": "tijori", "version": "1.0"}})
        elif method == "ping":
            out = _rpc(id_, {})
        elif method == "tools/list":
            out = _rpc(id_, {"tools": [{"name": n, "description": d, "inputSchema": sch} for n, (d, sch, _) in TOOLS.items()]})
        elif method == "tools/call" and params.get("name") in TOOLS:
            try:
                data = _mask(json.loads(json.dumps(TOOLS[params["name"]][2](s, ctx, params.get("arguments") or {}, f"mcp:{row.token_id}"), default=_json)))
                out = _rpc(id_, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                                 "structuredContent": data if isinstance(data, dict) else {"items": data}})
            except (ValueError, LookupError, TypeError) as exc:  # Invalid, NotFound, bad arguments
                s.rollback()
                out = _rpc(id_, {"content": [{"type": "text", "text": f"error: {exc}"[:300]}], "isError": True})
        else:
            out = _rpc(id_, error=(-32601, "method not found"))
    return Response(json.dumps(out, ensure_ascii=False), media_type="application/json")
