"""MCP server (PLAN §9): JSON-RPC over streamable HTTP at POST /mcp, one response per request.

The grouped tools ({action, args}) reach every operation; the ones agents call most also have a typed tool
with the route's JSON Schema, and describe_action gives any other action's schema on request. All run the
/api route in-process through the app, so validation, RLS, the owner lock, audit and error shapes are the
web's own. build_tools() fails the boot when an /api route is neither an action nor in EXCLUDED, which keeps
MCP able to do what the API does.

Auth is a bearer token only, never the site cookie: a pasted tjm_ token, or an OAuth access token from any
MCP client (app/oauth.py), whose scope may be read-only. Per tools/call: one token check (lookup and
last_used_at), then the route's own bind and queries. Person UPI handles are masked in all text returned,
since the output leaves Tijori (PLAN §10); payee keys stay whole because the writes take them back.
"""

import base64
import binascii
import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

import anyio
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from sqlalchemy import Engine, update
from sqlalchemy.orm import Session
from starlette.routing import compile_path

from tijori.app.auth import MCP_IDENTITY, Identity
from tijori.app.multipart import read_capped
from tijori.app.ratelimit import RateLimiter
from tijori.app.routes import MONTH_PATTERN
from tijori.app.web import origin_ok
from tijori.db import bind_member_by_mcp_token
from tijori.models import McpToken
from tijori.services import mcp_oauth as oauth
from tijori.services.networth import MANUAL_KEYS

log = logging.getLogger(__name__)
router = APIRouter()
MODERN = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")  # initialize-based revisions, newest first
CALLS = RateLimiter(limit=120, window_s=60)  # per token; filing a whole Inbox stays well under it
READ, WRITE, DESTRUCTIVE = "read", "write", "destructive"

# tool: (description, kind, {action: (route name, hint)}). Reads and writes are separate tools so a
# client can allow the reads and still ask before a write.
TOOLS: dict[str, tuple[str, str, dict[str, tuple[str, str]]]] = {
    "get_reports": ("Spending and income views. A month is YYYY-MM, in the member's month cycle.", READ, {
        "summary": ("summary", "spend, income, invested and spend by category for one month"),
        "months": ("get_months", "every month with data, and its totals"),
        "trends": ("trends", "totals per period, optionally grouped"),
        "budgets": ("get_budgets", "budget vs actual and pace, per category"),
        "recurring": ("get_recurring", "subscriptions, bills and SIPs: cost per month, next due date, state"),
        "alerts": ("get_alerts", "rule alerts: duplicate charge, bounce risk, price rise, missed charge, budgets, stale backup or mailbox"),
        "filing_stats": ("inbox_stats", "how the month's txns got their category, and how many still wait"),
    }),
    "find_transactions": (("Search and inspect transactions, and see what waits in the Inbox. No txns on a day can mean a "
                           "feed is behind: check get_setup coverage before saying there were none."), READ, {
        "search": ("transactions", "a page of txns and the totals of the whole filtered set; category takes ids or 'none'"),
        "get": ("transaction", "one txn with its observations, links, payee history and split parts"),
        "sources": ("txn_sources", "the emails and files the txn was read from"),
        "link_candidates": ("link_candidates", "txns this one could be linked to"),
        "inbox": ("inbox", "txns waiting for a category; group=payee groups them by payee"),
    }),
    "get_loans": ("Money lent or borrowed.", READ, {
        "list": ("get_loans", "every loan: who, how much is still owed"),
        "get": ("get_loan", "one loan with its repayments"),
        "for_txn": ("loan_picker", "open loans a txn could be filed under"),
    }),
    "get_net_worth": ("Net worth, its history and holdings.", READ, {
        "live": ("networth_live", "net worth now by component and asset class, with month, year and FY change"),
        "history": ("get_networth", "monthly snapshots, with remarks"),
        "holdings": ("get_holdings", "funds and stocks with units and value"),
    }),
    "get_setup": (("Accounts, categories, rules, payees, settings and household. Call coverage before claiming there were "
                   "no transactions in a period: a missing day may be a feed that is behind."), READ, {
        "me": ("get_me", "the signed-in member"),
        "accounts": ("get_accounts", "accounts with their last alert, statement reach (covered_through) and live_through"),
        "coverage": ("get_coverage", ("per account per day (default the last 7, IST): confirmed (a reconciled statement), "
                                      "live (alerts read past that day; not proof) or unknown with the reason, plus mailbox health")),
        "cards": ("get_cards", "credit card bills: statement, due date, paid"),
        "categories": ("categories", "every category with its id, kind and bucket"),
        "rules": ("get_rules", "classification rules and payee memory"),
        "payees": ("get_payees", "find payees by name"),
        "payee_names": ("get_payee_aliases", "the member's payee renames, and suggested ones"),
        "settings": ("get_settings", "month start day, notifications, retention"),
        "onboarding": ("get_onboarding", "onboarding progress"),
        "classify_profile": ("get_profile", "what the classifier knows about the member (employer, family, own accounts)"),
        "household": ("get_household", "household members and open invites"),
        "mail_sources": ("list_mail_sources", "connected mailboxes, their last poll and last full read (last_ok_poll_at)"),
        "statement_queue": ("parse_queue", "documents that no parser could read yet"),
        "backup": ("backup_status", "when the last backup ran"),
    }),
    "classify": ("File transactions under categories, and manage rules and payee names.", WRITE, {
        "set_category": ("categorize", "file one txn by category_id or category name; scope=payee also files that payee's future payments"),
        "file_payee": ("file_inbox", "file a payee's Inbox txns at once; remember=true keeps doing it"),
        "undo": ("undo_filing", "send txns back to the Inbox, or undo what a rule filed"),
        "set_rule": ("patch_rule", "turn a rule on or off"),
        "rename_payees": ("rename_payees", "give payees one name"),
        "reset_payee_names": ("reset_payees", "drop the member's names for these payees"),
        "dismiss_name_suggestion": ("dismiss_alias_suggestion", "stop suggesting this name for the payee"),
    }),
    "edit_transactions": ("Notes, tags, splits and links on a transaction.", WRITE, {
        "notes": ("patch_txn", "set notes and tags"),
        "split": ("split_txn", "split one txn into 2 to 10 parts, each with its category; amounts are rupee strings"),
        "unsplit": ("unsplit_txn", "undo a split"),
        "link": ("link_txn", "link this txn to other_txn_id as a transfer, refund, duplicate or pass-through"),
        "unlink": ("unlink_txn", "remove that link"),
    }),
    "plan_spending": ("Budgets and recurring charges.", WRITE, {
        "set_budget": ("put_budget", "set or clear (amount null) a category's monthly budget; amount is a rupee string"),
        "set_recurring": ("put_recurring", "confirm, reject or end a recurring series, with its cadence and expected amount"),
    }),
    "edit_loans": ("Record and settle money lent or borrowed.", WRITE, {
        "create": ("create_loan", "a new loan, optionally from txns"),
        "edit": ("patch_loan", "change who, when, the opening amount or the note"),
        "attach_txns": ("attach_loan_txns", "count txns as this loan's lending or repayments"),
        "detach_txns": ("detach_loan_txns", "stop counting those txns"),
        "settle": ("settle_loan", "mark it repaid"),
        "reopen": ("reopen_loan", "open it again"),
        "write_off": ("write_off_loan", "give up on the rest, filed under category_id"),
    }),
    "edit_net_worth": ("Manual net-worth values, snapshot remarks and the sheet import.", WRITE, {
        "set_value": ("put_component", f"set a manual component ({'|'.join(MANUAL_KEYS)}) as of a date; the rest come from statements"),
        "set_remark": ("patch_remark", "the remark on a monthly snapshot"),
        "import_sheet": ("import_sheet", "the net-worth Google Sheet as CSV text; upserts by month"),
    }),
    "edit_setup": ("Accounts, statements, mailboxes, settings and household. Deletes cannot be undone.", DESTRUCTIVE, {
        "add_account": ("create_account", "a new bank, card or investment account"),
        "edit_account": ("patch_account", "rename it or change its last digits"),
        "delete_account": ("delete_account", "delete the account"),
        "upload_statement": ("upload", "a statement as pdftotext -layout text, or a PDF as base64; locked PDFs try the saved passwords"),
        "remove_statement_password": ("remove_statement_password", "forget one saved statement password"),
        "edit_mail_source": ("patch_mail_source", "relabel a mailbox"),
        "delete_mail_source": ("delete_mail_source", "disconnect a mailbox"),
        "test_mail_source": ("test_mail_source", "log in to a saved mailbox and count its messages"),
        "update_settings": ("patch_settings", "month start day, local shop cap, notifications, raw retention; null notify_topic clears it"),
        "rename_me": ("rename_me", "the member's display name"),
        "set_onboarding": ("patch_onboarding", "move the onboarding step"),
        "set_classify_profile": ("put_profile", "replace what the classifier knows about the member"),
        "test_notification": ("notify_test", "send a test push to the notification topic"),
        "revoke_invite": ("revoke_invite", "cancel an open household invite"),
    }),
}

# Routes MCP deliberately leaves out, and why.
EXCLUDED = {
    "invite_info": "public invite landing page; there is no member to act as",
    "create_invite": "grants household access, and the invite link would pass through the model",
    "get_mcp_tokens": "MCP tokens are managed by the signed-in person only",
    "create_mcp_token": "a token must not mint tokens",
    "revoke_mcp_token": "MCP tokens are managed by the signed-in person only",
    "create_mail_source": "takes an IMAP app password, which must not pass through the model",
    "test_new_mail_source": "takes an IMAP app password, which must not pass through the model",
    "put_statement_password": "takes a statement password, which must not pass through the model",
    "add_statement_password": "takes a statement password, which must not pass through the model",
    "delete_statement_password": "remove_statement_password does the same",
    "raw_attachment": "binary original with unmasked account details",
}
RENAMES = {"link_txn": {"other_txn_id": "txn_id"}, "unlink_txn": {"other_txn_id": "txn_id"}}  # argument → body field
DENIED = {"patch_mail_source": {"app_password"}}  # body fields MCP never sends
# Multipart routes, which declare no models: (field, signature, JSON Schema properties, required, returns).
# _multipart enforces text xor pdf_base64, which a top-level oneOf can't say to every client.
UPLOADS: dict[str, tuple[str, str, dict[str, Any], list[str], str]] = {
    "upload": ("file", "text?: str, pdf_base64?: str, filename?: str", {
        "text": {"type": "string", "description": "pdftotext -layout output; send this or pdf_base64"},
        "pdf_base64": {"type": "string", "description": "the PDF, base64; send this or text"},
        "filename": {"type": "string", "maxLength": 200}}, [],
        "{statement_id: int, raw_message_id: int, duplicate: bool, account: obj, period_start: YYYY-MM-DD, "
        "period_end: YYYY-MM-DD, reconciliation: obj, txns: obj}"),
    "import_sheet": ("sheet", "csv: str", {"csv": {"type": "string", "description": "the sheet exported as CSV"}}, ["csv"],
                     "{snapshots_upserted: int}"),
}

# Typed tools for the operations agents call, each with the route's JSON Schema, so the common calls need no
# guessing. Picked from Engram's trace and the Pitcrew rollouts, 2026-10-02/03: these ten were 66 of the 79
# Tijori calls, all reads. Every other action stays reachable through its grouped tool, with describe_action for
# its exact schema, so the listing every agent loads each turn stays small. The grouped tools' text is untouched:
# gateways that pin tool text (Engram) block a tool whose text changes.
TYPED: dict[str, tuple[str, str]] = {  # typed tool: (grouped tool, action)
    "search_transactions": ("find_transactions", "search"), "get_month_summary": ("get_reports", "summary"),
    "list_accounts": ("get_setup", "accounts"), "get_budgets": ("get_reports", "budgets"),
    "get_alerts": ("get_reports", "alerts"), "list_inbox": ("find_transactions", "inbox"),
    "get_transaction": ("find_transactions", "get"), "get_net_worth_live": ("get_net_worth", "live"),
    "get_filing_stats": ("get_reports", "filing_stats"), "list_recurring": ("get_reports", "recurring"),
}
# Typed-only tools, kept out of the grouped ones so their pinned text stays put: (route name, kind, hint).
EXTRA: dict[str, tuple[str, str, str]] = {
    "get_freshness": ("get_freshness", READ, "how current the data is, in one block: per account the newest txn date, when "
                      "Tijori last recorded one, the newest alert, and how far reconciled statements and read alerts reach; "
                      "per mailbox whether it is being read. Day by day, with reasons: get_setup action coverage"),
}
DESCRIBE = "describe_action"
# Actions that delete, overwrite or can't be undone (route names): a typed tool and describe_action report these
# alone as destructive. The grouped tools keep their original hints, so their listing stays exactly as it was.
DESTRUCTIVE_OPS = frozenset({"delete_account", "delete_mail_source", "remove_statement_password", "revoke_invite",
                             "put_profile", "import_sheet", "patch_settings"})  # profile replace, sheet upsert, retention purge
# Ledger reads whose result also carries `freshness` (GET /api/freshness): one more in-process request per call.
FRESH = frozenset({"summary", "get_months", "trends", "get_budgets", "get_recurring", "get_alerts", "inbox_stats",
                   "transactions", "inbox", "get_cards", "get_loans", "networth_live"})
_TOOL_NAME = re.compile(r"^[a-z0-9_]{1,64}$")  # no dots: the Anthropic API refuses them in tool names


@dataclass(frozen=True, slots=True)
class Op:
    name: str  # the route's name
    method: str
    path: str  # the path format, e.g. /api/loans/{loan_id}
    path_params: frozenset[str]
    query: frozenset[str]
    body: frozenset[str] | None  # argument names that go in the JSON body; None: no JSON body
    renames: dict[str, str]
    upload: str | None  # the multipart field, for the upload routes
    sig: str
    hint: str
    schema: dict[str, Any]  # the typed tool's inputSchema
    returns: str  # compact shape of the 2xx body


@dataclass(frozen=True, slots=True)
class Tool:
    ops: dict[str, Op]
    listing: dict[str, Any]  # the tools/list entry
    read_only: bool
    single: Op | None = None  # a typed tool: arguments are the op's own, not {action, args}


@dataclass(frozen=True, slots=True)
class Registry:
    tools: dict[str, Tool]
    matchers: list[tuple[re.Pattern[str], frozenset[str], str]]  # every API route in router order: regex, methods, name


def build_tools(app: FastAPI) -> Registry:
    """Once per process, after the routers are included. Raises when MCP and the API disagree: an /api
    route with no action and no EXCLUDED reason, a name no route has, an argument with two homes, or a typed
    tool that names no grouped action, shares one, or takes a used name."""
    spec = app.openapi()
    defs = spec.get("components", {}).get("schemas", {})
    every = [c for c in iter_route_contexts(app.routes) if isinstance(c.original_route, APIRoute)]
    api = [c for c in every if c.path.startswith("/api/")]
    routes = {r.name: r for r in api}
    mapped = {name for _, _, acts in TOOLS.values() for name, _ in acts.values()} | {r for r, _, _ in EXTRA.values()}
    errors = [f"two routes are named {n}" for n in routes if sum(r.name == n for r in api) > 1]
    errors += [f"route {n} is neither an MCP action nor in EXCLUDED" for n in routes.keys() - mapped - EXCLUDED.keys()]
    errors += [f"{n} names no /api route" for n in (mapped | EXCLUDED.keys()) - routes.keys()]
    errors += [f"{n} is both an action and excluded" for n in mapped & EXCLUDED.keys()]
    pairs = [(t, a) for t, (_, _, acts) in TOOLS.items() for a in acts]
    errors += [f"typed tool {n} names no grouped action" for n, ta in TYPED.items() if ta not in pairs]
    errors += [f"{ta[0]}.{ta[1]} has two typed tools" for ta in set(TYPED.values()) if list(TYPED.values()).count(ta) > 1]
    errors += [f"tool name {n} is taken or malformed" for n in [*TYPED, *EXTRA, DESCRIBE]
               if n in TOOLS or [*TYPED, *EXTRA, DESCRIBE].count(n) > 1 or not _TOOL_NAME.match(n)]
    errors += [f"{n} in DESTRUCTIVE_OPS is not a write action" for n in DESTRUCTIVE_OPS
               if not any(r == n for _, kind, acts in TOOLS.values() if kind != READ for r, _ in acts.values())]
    grouped: dict[str, Tool] = {}
    for name, (desc, kind, acts) in TOOLS.items():
        ops: dict[str, Op] = {}
        for action, (route_name, hint) in acts.items():
            if route_name in routes:
                try:
                    ops[action] = _op(routes[route_name], spec, defs, hint)
                except ValueError as exc:
                    errors.append(str(exc))
        lines = "\n".join(f"- {a}({op.sig}): {op.hint}" for a, op in ops.items())
        grouped[name] = Tool(ops, {
            "name": name, "description": f"{desc} Pass the action and its args.\n{lines}",
            "inputSchema": {"type": "object", "additionalProperties": False, "required": ["action"], "properties": {
                "action": {"type": "string", "enum": list(ops)},
                "args": {"type": "object", "description": "The action's arguments, as listed in the description."}}},
            "annotations": {"readOnlyHint": kind == READ, "destructiveHint": kind == DESTRUCTIVE}}, kind == READ)
    typed: dict[str, Tool] = {}
    for name, (group, action) in TYPED.items():
        op = grouped[group].ops.get(action) if group in grouped else None
        if op is not None:
            typed[name] = _typed(name, op, TOOLS[group][1])
    for name, (route_name, kind, hint) in EXTRA.items():
        if route_name in routes:
            try:
                typed[name] = _typed(name, _op(routes[route_name], spec, defs, hint), kind)
            except ValueError as exc:
                errors.append(str(exc))
    typed[DESCRIBE] = _describer(grouped)
    if errors:
        raise RuntimeError("MCP is out of step with the API: " + "; ".join(sorted(errors)))
    return Registry({**typed, **grouped}, [(compile_path(c.path)[0], frozenset(c.methods), c.name) for c in every])


def _typed(name: str, op: Op, kind: str) -> Tool:
    desc = op.hint[0].upper() + op.hint[1:] + "."
    if "month" in op.schema["properties"]:
        desc += " A month is YYYY-MM, in the member's month cycle."
    desc += f" Returns {op.returns}."
    if op.name in FRESH:
        desc += " Adds freshness (as get_freshness); before saying a period had no txns, call get_setup action coverage."
    if op.name == "transactions":
        desc += " Other transaction actions are on find_transactions; describe_action gives their arguments."
    return Tool({}, {"name": name, "description": desc, "inputSchema": op.schema,
                     "annotations": {"readOnlyHint": kind == READ, "destructiveHint": op.name in DESTRUCTIVE_OPS}},
                kind == READ, op)


def _describer(grouped: dict[str, Tool]) -> Tool:
    """describe_action: any grouped action's exact schema on request, so the listing needn't carry them all."""
    actions = sorted({a for t in grouped.values() for a in t.ops})
    return Tool({}, {"name": DESCRIBE, "description": (
        "The exact JSON Schema of the arguments, and the return shape, of any action of the grouped tools "
        f"({', '.join(grouped)}). Use it before calling an action you haven't used, then call the grouped tool with "
        "{action, args}. Returns {tool, action, typed_tool, description, kind, destructive, arguments, returns}. Reads "
        "no data."), "inputSchema": {"type": "object", "additionalProperties": False, "required": ["tool", "action"],
                                     "properties": {"tool": {"type": "string", "enum": list(grouped)},
                                                    "action": {"type": "string", "enum": actions}}},
        "annotations": {"readOnlyHint": True, "destructiveHint": False}}, True)


def _describe(reg: Registry, tool: Tool, arguments: dict[str, Any]) -> dict[str, Any]:
    problem = _check(tool.listing["inputSchema"], arguments)
    if problem:
        return _error(problem)
    name, action = arguments["tool"], arguments["action"]
    op = reg.tools[name].ops.get(action)
    if op is None:
        return _error(f"{name} has no action {action!r}; it has {', '.join(reg.tools[name].ops)}")
    data = {"tool": name, "action": action, "typed_tool": next((n for n, ta in TYPED.items() if ta == (name, action)), None),
            "description": op.hint, "kind": "read" if TOOLS[name][1] == READ else "write",
            "destructive": op.name in DESTRUCTIVE_OPS, "arguments": op.schema, "returns": op.returns}
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}],
            "structuredContent": data}


def _op(route: RouteContext, spec: dict[str, Any], defs: dict[str, Any], hint: str) -> Op:
    method = next(iter(route.methods))
    if route.name in UPLOADS:
        field, sig, props, required, returns = UPLOADS[route.name]
        schema = {"type": "object", "properties": props, "required": required, "additionalProperties": False}
        return Op(route.name, method, route.path_format, frozenset(), frozenset(), None, {}, field, sig, hint, schema, returns)
    doc = spec["paths"][route.path_format][method.lower()]
    returns = _returns(doc, defs)
    params = doc.get("parameters", [])
    path = [p for p in params if p["in"] == "path"]
    query = [p for p in params if p["in"] == "query"]
    body_ref = doc.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    body = _deref(body_ref, defs) if body_ref else None
    renames = RENAMES.get(route.name, {})
    arg_of = {field: arg for arg, field in renames.items()}
    fields = {k: v for k, v in (body or {}).get("properties", {}).items() if k not in DENIED.get(route.name, ())}
    required = set((body or {}).get("required", ()))
    body_args = {arg_of.get(k, k): (v, k in required) for k, v in fields.items()}
    clash = body_args.keys() & {p["name"] for p in params}
    if clash:
        raise ValueError(f"{route.name}: {sorted(clash)} is both a parameter and a body field; add a RENAMES entry")
    sig = [f"{p['name']}{'' if p.get('required') else '?'}: {_ty(p.get('schema', {}), defs)}" for p in path + query]
    sig += [f"{a}{'' if req else '?'}: {_ty(v, defs)}" for a, (v, req) in body_args.items()]
    props = {p["name"]: {**_optional(_clean(p.get("schema", {}), defs)),
                         **({"description": p["description"]} if p.get("description") else {})} for p in path + query}
    props |= {a: _clean(v, defs) for a, (v, _) in body_args.items()}
    need = [p["name"] for p in path + query if p.get("required")] + [a for a, (_, req) in body_args.items() if req]
    schema = {"type": "object", "properties": props, "required": need, "additionalProperties": False}
    return Op(route.name, method, route.path_format, frozenset(p["name"] for p in path), frozenset(p["name"] for p in query),
              frozenset(body_args) if body is not None else None, renames, None, ", ".join(sig), hint, schema, returns)


def _deref(sc: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in sc:
        sc = defs[sc["$ref"].rsplit("/", 1)[1]]
    return sc


def _clean(sc: dict[str, Any], defs: dict[str, Any], seen: tuple[str, ...] = ()) -> dict[str, Any]:
    """The route's JSON Schema with $refs inlined (not every client resolves them) and OpenAPI titles dropped."""
    if "$ref" in sc:
        name = sc["$ref"].rsplit("/", 1)[1]
        if name in seen:
            raise ValueError(f"{name} is recursive; typed tool schemas are inlined")
        return _clean({**defs[name], **{k: v for k, v in sc.items() if k != "$ref"}}, defs, (*seen, name))
    out: dict[str, Any] = {}
    for k, v in sc.items():
        if k == "title":
            continue
        if k == "properties":
            out[k] = {n: _clean(p, defs, seen) for n, p in v.items()}
        elif k in ("items", "additionalProperties") and isinstance(v, dict):
            out[k] = _clean(v, defs, seen)
        elif k in ("anyOf", "oneOf", "allOf"):
            out[k] = [_clean(x, defs, seen) for x in v]
        else:
            out[k] = v
    return out


def _optional(sc: dict[str, Any]) -> dict[str, Any]:
    """A parameter's `X | null` as plain X: _request drops a null parameter anyway, and the listing is loaded every turn."""
    alts = [x for x in sc.get("anyOf", ()) if x.get("type") != "null"]
    if len(alts) == 1 and len(sc["anyOf"]) == 2:
        return {**alts[0], **{k: v for k, v in sc.items() if k != "anyOf"}}
    return sc


def _returns(doc: dict[str, Any], defs: dict[str, Any]) -> str:
    """The 2xx body in _ty's compact form, one level deep; a body-less 2xx is what _result returns for it."""
    for code, resp in doc.get("responses", {}).items():
        if code.startswith("2"):
            sc = resp.get("content", {}).get("application/json", {}).get("schema")
            return "{ok: true}" if sc is None else _ty(sc, defs, 1)
    return "{ok: true}"


_TYPES = {"integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
          "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
          "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
          "array": lambda v: isinstance(v, list), "object": lambda v: isinstance(v, dict), "null": lambda v: v is None}


def _check(sc: dict[str, Any], v: Any, at: str = "") -> str | None:
    """The first way v breaks a typed tool's schema, or None. Shape, enums and patterns only: the route still
    applies its own bounds, so this can only refuse earlier, never let more through."""
    where = at or "arguments"
    if "anyOf" in sc:
        errs = [_check(x, v, at) for x in sc["anyOf"]]
        if None in errs:
            return None
        fits = [e for x, e in zip(sc["anyOf"], errs) if _TYPES.get(x.get("type", ""), lambda _: True)(v)]
        return (fits or errs)[0]
    if "type" in sc and not _TYPES.get(sc["type"], lambda _: True)(v):
        return f"{where}: expected {sc['type']}"
    if "enum" in sc and v not in sc["enum"]:
        return f"{where}: one of {'|'.join(map(str, sc['enum']))}"
    if "const" in sc and v != sc["const"]:
        return f"{where}: must be {sc['const']}"
    if isinstance(v, str) and "pattern" in sc and not re.search(sc["pattern"], v):
        return f"{where}: must match {sc['pattern']}"
    if isinstance(v, list) and isinstance(sc.get("items"), dict):
        return next((e for i, x in enumerate(v) if (e := _check(sc["items"], x, f"{at}[{i}]"))), None)
    if isinstance(v, dict) and "properties" in sc:
        props, dot = sc["properties"], f"{at}." if at else ""
        missing = [k for k in sc.get("required", ()) if k not in v]
        if missing:
            return f"{dot}{missing[0]} is required"
        extra = sorted(v.keys() - props.keys()) if sc.get("additionalProperties") is False else []
        if extra:
            return f"unknown argument {dot}{extra[0]!r}"
        return next((e for k, x in v.items() if k in props and (e := _check(props[k], x, f"{dot}{k}"))), None)
    return None


def _ty(sc: dict[str, Any], defs: dict[str, Any], depth: int = 0) -> str:
    """A compact type for the tool description: int, YYYY-MM, a|b, {x, y?}[] and so on."""
    sc = _deref(sc, defs)
    if "enum" in sc:
        return "|".join(map(str, sc["enum"]))
    if "const" in sc:
        return str(sc["const"])
    if "anyOf" in sc:
        return "|".join(dict.fromkeys(_ty(x, defs, depth) for x in sc["anyOf"] if x.get("type") != "null"))
    kind = sc.get("type")
    if kind == "array":
        return _ty(sc.get("items", {}), defs, depth) + "[]"
    if kind == "object" and "properties" in sc:
        if depth >= 2:
            return "obj"
        req = set(sc.get("required", ()))
        return "{" + ", ".join(f"{k}{'' if k in req else '?'}: {_ty(v, defs, depth + 1)}"
                               for k, v in sc["properties"].items()) + "}"
    if sc.get("pattern") == MONTH_PATTERN:
        return "YYYY-MM"
    if sc.get("format") == "date":
        return "YYYY-MM-DD"
    return {"integer": "int", "number": "num", "boolean": "bool", "string": "str", "object": "obj"}.get(kind, "any")


def _request(op: Op, args: dict[str, Any]) -> tuple[str, list[tuple[str, str]], bytes, str]:
    """(path, query, body, content type) for one action; ValueError names the bad argument."""
    if op.upload:
        return op.path, [], *_multipart(op.upload, args)
    path: dict[str, str] = {}
    query: list[tuple[str, str]] = []
    body: dict[str, Any] = {}
    for k, v in args.items():
        if k in op.path_params:
            path[k] = str(v)
        elif k in op.query:
            query += [(k, str(x).lower() if isinstance(x, bool) else str(x))
                      for x in (v if isinstance(v, list) else [v]) if x is not None]
        elif op.body is not None and k in op.body:
            body[op.renames.get(k, k)] = v  # an explicit null is kept: some edits clear a field with it
        else:
            raise ValueError(f"unknown argument {k!r}; this action takes ({op.sig})")
    missing = op.path_params - path.keys()
    if missing:
        raise ValueError(f"missing {', '.join(sorted(missing))}; this action takes ({op.sig})")
    raw = json.dumps(body).encode() if op.body is not None else b""
    return op.path.format(**path), query, raw, "application/json"


def _multipart(field: str, args: dict[str, Any]) -> tuple[bytes, str]:
    allowed = {"csv"} if field == "sheet" else {"text", "pdf_base64", "filename"}
    if args.keys() - allowed:
        raise ValueError(f"unknown argument {sorted(args.keys() - allowed)[0]!r}; takes {', '.join(sorted(allowed))}")
    if field == "sheet":
        if not isinstance(args.get("csv"), str):
            raise ValueError("csv is required: the sheet exported as CSV text")
        data, name = args["csv"].encode(), "sheet.csv"
    else:
        text, pdf = args.get("text"), args.get("pdf_base64")
        if (text is None) == (pdf is None):
            raise ValueError("send exactly one of text (pdftotext -layout output) or pdf_base64")
        try:
            data = str(text).encode() if text is not None else base64.b64decode(str(pdf), validate=True)
        except binascii.Error as exc:
            raise ValueError("pdf_base64 is not valid base64") from exc
        name = re.sub(r"[^\w .\-]", "_", str(args.get("filename") or ("statement.pdf" if pdf else "statement.txt")))[:200]
    boundary = secrets.token_hex(16)
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def _resolves(reg: Registry, op: Op, path: str) -> bool:
    """The first route that takes this method and path must be the action's own, so no argument can steer a
    call to another route (say, an excluded one)."""
    for regex, methods, name in reg.matchers:
        if op.method in methods and regex.match(path):
            return name == op.name
    return False


async def _asgi(app: FastAPI, method: str, path: str, query: list[tuple[str, str]], body: bytes, ctype: str,
                identity: Identity) -> tuple[int, bytes]:
    """Run one request through the whole app, as the MCP token's member."""
    done = anyio.Event()
    status, chunks, sent = 500, [], False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(msg: dict[str, Any]) -> None:
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body"):
                done.set()

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": quote(path).encode(), "root_path": "", "query_string": urlencode(query).encode(),
             "headers": [(b"content-type", ctype.encode()), (b"content-length", str(len(body)).encode()),
                         (b"x-requested-with", b"tijori")],
             "client": ("127.0.0.1", 0), "server": ("mcp", 0), MCP_IDENTITY: identity}
    await app(scope, receive, send)
    return status, b"".join(chunks)


_PERSON_VPA = re.compile(r"^([a-z0-9.\-_]{1,2})[a-z0-9.\-_]*(@[a-z]+)$", re.I)
_HANDLE = re.compile(r"[a-z0-9.\-_]+@[a-z]+\b", re.I)
_KEEP = frozenset({"payee_key", "payee_keys"})  # identifiers the writes take back


def _mask(v: Any) -> Any:
    """Person handles (name@bank) become as***@okicici wherever they appear; merchant QR handles stay."""
    if isinstance(v, dict):
        return {k: x if k in _KEEP else _mask(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_mask(x) for x in v]
    if isinstance(v, str) and "@" in v:
        return _HANDLE.sub(lambda m: _mask_vpa(m[0]), v)
    return v


def _mask_vpa(v: str) -> str:
    if re.match(r"^(paytmqr|bharatpe|q\d|vyapar|upi\.|gpay-|ibkpos)", v, re.I):
        return v
    m = _PERSON_VPA.match(v)
    return f"{m[1]}***{m[2]}" if m else v


def _error(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {msg}"[:600]}], "isError": True}


def _detail(data: Any) -> str:
    d = data.get("detail", data) if isinstance(data, dict) else data
    if isinstance(d, list):  # pydantic errors: keep where and what, drop the echoed input
        d = "; ".join(f"{'.'.join(map(str, e.get('loc', [])[1:])) or 'body'}: {e.get('msg')}" if isinstance(e, dict)
                      else str(e) for e in d)
    return str(d)


def _result(status: int, raw: bytes, fresh: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        data = json.loads(raw) if raw else {"ok": True}
    except ValueError:
        return _error("internal error")
    if status >= 500:
        return _error("internal error")
    if status >= 400:
        return _error(f"{status}: {_detail(data)}")
    if fresh is not None and isinstance(data, dict):
        data.setdefault("freshness", fresh)
    data = _mask(data)
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}],
            "structuredContent": data if isinstance(data, dict) else {"items": data}}


async def _freshness(app: FastAPI, identity: Identity) -> dict[str, Any] | None:
    """Through the app like any action, so the owner lock and RLS apply. A failure drops the block, not the answer."""
    try:
        status, raw = await _asgi(app, "GET", "/api/freshness", [], b"", "application/json", identity)
        if status == 200:
            return json.loads(raw)
        log.warning("mcp freshness answered %s", status)
    except Exception:
        log.exception("mcp freshness failed")
    return None


async def _call(app: FastAPI, reg: Registry, tool: Tool, arguments: dict[str, Any], identity: Identity, token_id: int) -> dict[str, Any]:
    if tool.listing["name"] == DESCRIBE:  # metadata only: no route, no data
        return _describe(reg, tool, arguments)
    if tool.single is not None:
        op, args, action = tool.single, arguments, tool.listing["name"]
        problem = _check(op.schema, args)
        if problem:
            return _error(f"{problem}; this tool takes ({op.sig})")
    else:
        action, args = arguments.get("action"), arguments.get("args") or {}
        found = tool.ops.get(action) if isinstance(action, str) else None
        if found is None:
            return _error(f"unknown action {action!r}; one of {', '.join(tool.ops)}")
        if not isinstance(args, dict):
            return _error("args must be an object")
        op = found
    if not CALLS.allow(str(token_id)):
        return _error("too many calls; wait a minute")
    try:
        path, query, body, ctype = _request(op, args)
    except ValueError as exc:
        return _error(str(exc))
    if not _resolves(reg, op, path):
        return _error(f"these arguments do not fit {action}({op.sig})")
    try:
        status, raw = await _asgi(app, op.method, path, query, body, ctype, identity)
    except Exception:  # the app already answered 500; log it here too, since the caller only sees "internal error"
        log.exception("mcp action %s failed", action)
        return _error("internal error")
    fresh = await _freshness(app, identity) if status == 200 and op.name in FRESH else None
    return _result(status, raw, fresh)


def _touch(engine: Engine, token_hash: str) -> tuple[int, str | None] | None:
    """(grant id, scope) for a live token, stamping last_used_at; None when it is unknown, expired or revoked."""
    with Session(engine) as s, s.begin():
        found = bind_member_by_mcp_token(s, token_hash)
        if found is None:
            return None
        s.execute(update(McpToken).where(McpToken.id == found[1]).values(last_used_at=datetime.now(UTC)))
        return found[1], found[2]


def _rpc(id_: Any, result: Any = None, error: tuple[int, str] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, **({"error": {"code": error[0], "message": error[1]}} if error else {"result": result})}


def _send(out: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> Response:
    return Response(json.dumps(out, ensure_ascii=False), status_code=status, media_type="application/json", headers=headers)


def _server_info(request: Request) -> dict[str, Any]:
    """MCP Implementation, with icons (2025-11-25+) so a client can show Tijori's logo rather than a generic one."""
    base = oauth.issuer(request.app.state.settings.public_url)
    return {"name": "tijori", "title": "Tijori", "version": "2.1", "websiteUrl": base,
            "icons": [{"src": f"{base}/icon-256.png", "mimeType": "image/png", "sizes": ["256x256"]},
                      {"src": f"{base}/favicon.svg", "mimeType": "image/svg+xml", "sizes": ["any"]}]}


def _challenge(request: Request, status: int = 401, error: str | None = None, body: dict[str, Any] | None = None) -> Response:
    """RFC 6750 / 9728: where the authorization server is, and which scopes to ask for."""
    base = oauth.issuer(request.app.state.settings.public_url)
    params = ([f'error="{error}"'] if error else []) + [
        f'resource_metadata="{base}/.well-known/oauth-protected-resource/mcp"', f'scope="{" ".join(oauth.SCOPES)}"']
    headers = {"WWW-Authenticate": "Bearer " + ", ".join(params)}
    return _send(body, status, headers) if body else Response(status_code=status, headers=headers)


def _header_value(v: str | None) -> str | None:
    """Mcp-Name may carry =?base64?…?= (2026-07-28 value encoding)."""
    if v and v.startswith("=?base64?") and v.endswith("?="):
        try:
            return base64.b64decode(v[9:-2], validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            return None
    return v


def _modern_problem(request: Request, msg: dict[str, Any], version: str | None) -> tuple[int, dict[str, Any]] | None:
    """2026-07-28 Streamable HTTP: the mirrored headers must match the body, and the version must be one we speak."""
    h, id_, params = request.headers, msg.get("id"), msg.get("params") or {}
    if h.get("mcp-protocol-version") != version:
        return 400, _rpc(id_, error=(-32020, "MCP-Protocol-Version header does not match _meta protocolVersion"))
    if version != MODERN:
        return 400, {"jsonrpc": "2.0", "id": id_, "error": {"code": -32022, "message": "Unsupported protocol version",
                                                           "data": {"supported": [MODERN, *LEGACY], "requested": version}}}
    if h.get("mcp-method") != msg.get("method"):
        return 400, _rpc(id_, error=(-32020, "Mcp-Method header does not match method"))
    if msg.get("method") == "tools/call" and _header_value(h.get("mcp-name")) != params.get("name"):
        return 400, _rpc(id_, error=(-32020, "Mcp-Name header does not match params.name"))
    return None


async def _dispatch(request: Request, msg: dict[str, Any], modern: bool, token_hash: str, token_id: int,
                    scope: str | None) -> Response:
    reg: Registry = request.app.state.mcp
    method, id_, params = msg["method"], msg.get("id"), msg.get("params") or {}
    writable = oauth.can_write(scope)
    info = _server_info(request)
    stamp = {"resultType": "complete", "_meta": {"io.modelcontextprotocol/serverInfo": info}} if modern else {}
    if method == "initialize" and not modern:
        asked = params.get("protocolVersion")
        return _send(_rpc(id_, {"protocolVersion": asked if asked in LEGACY else LEGACY[0], "capabilities": {"tools": {}},
                                "serverInfo": info}))
    if method == "ping" and not modern:
        return _send(_rpc(id_, {}))
    if method == "server/discover":
        return _send(_rpc(id_, {"resultType": "complete", "supportedVersions": [MODERN, *LEGACY],
                                "capabilities": {"tools": {}}, "_meta": {"io.modelcontextprotocol/serverInfo": info},
                                "ttlMs": 3_600_000, "cacheScope": "public"}))
    if method == "tools/list":
        tools = [t.listing for t in reg.tools.values() if writable or t.read_only]  # a read-only grant sees reads only
        return _send(_rpc(id_, {"tools": tools, **stamp, **({"ttlMs": 300_000, "cacheScope": "private"} if modern else {})}))
    if method == "tools/call":
        tool = reg.tools.get(params.get("name"))  # type: ignore[arg-type]
        arguments = params.get("arguments") or {}
        if tool is None or not isinstance(arguments, dict):
            return _send(_rpc(id_, error=(-32602, f"unknown tool; one of {', '.join(reg.tools)}")))
        if not (writable or tool.read_only):
            return _challenge(request, 403, "insufficient_scope",
                              _rpc(id_, error=(-32001, "this connection is read-only; reconnect and allow changes")))
        result = await _call(request.app, reg, tool, arguments, Identity("mcp", token_hash), token_id)
        return _send(_rpc(id_, {**result, **stamp}))
    return _send(_rpc(id_, error=(-32601, "method not found")), 404 if modern else 200)


@router.post("/mcp")
async def mcp(request: Request) -> Response:
    """Dual-era: a request with per-request _meta (2026-07-28) is served statelessly; anything else is a
    legacy client (initialize-based, 2025-03-26 to 2025-11-25). Both are stateless here."""
    origin = request.headers.get("origin")
    if origin is not None and not origin_ok(origin):  # Streamable HTTP: an invalid Origin gets 403
        return _send({"jsonrpc": "2.0", "error": {"code": -32600, "message": "origin not allowed"}}, 403)
    auth = request.headers.get("authorization", "")
    tok = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    if not tok:
        return _challenge(request)
    # A base64 PDF is a third bigger than the file; the upload route applies the real cap.
    raw = await read_capped(request, request.app.state.settings.max_upload_bytes * 4 // 3 + 64 * 1024)
    try:
        msg = json.loads(raw)
    except ValueError:
        return _send(_rpc(None, error=(-32700, "parse error")), 400)
    if (not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str)
            or not isinstance(msg.get("params") or {}, dict)):
        return _send(_rpc(None, error=(-32600, "invalid request")), 400)
    token_hash = hashlib.sha256(tok.encode()).hexdigest()
    found = await run_in_threadpool(_touch, request.app.state.engine, token_hash)
    if found is None:
        return _challenge(request, error="invalid_token")
    if msg.get("id") is None:  # a notification (e.g. notifications/initialized): nothing to answer
        return Response(status_code=202)
    meta = (msg.get("params") or {}).get("_meta")
    version = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None
    header = request.headers.get("mcp-protocol-version")
    modern = version is not None or header == MODERN
    if modern:
        problem = _modern_problem(request, msg, version)
        if problem:
            return _send(problem[1], problem[0])
    elif header is not None and header not in LEGACY and msg["method"] != "initialize":
        return _send(_rpc(msg.get("id"), error=(-32600, f"unsupported MCP-Protocol-Version {header[:20]}")), 400)
    return await _dispatch(request, msg, modern, token_hash, *found)


@router.get("/mcp")
@router.delete("/mcp")
def mcp_other_methods() -> Response:
    """No standalone SSE stream and no sessions (2026-07-28 drops both; earlier revisions allow a 405)."""
    return Response(status_code=405, headers={"Allow": "POST"})
