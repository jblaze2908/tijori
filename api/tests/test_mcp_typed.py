"""Typed MCP tools: their schemas, the validation in front of them, dispatch to the right route, and freshness.
No database: the app is built offline and the in-process request (mcp._asgi) is replaced by a recorder."""

import json
import os
import unittest
from typing import Any
from unittest import mock

os.environ.setdefault("TIJORI_ENV", "test")
os.environ.setdefault("TIJORI_DATABASE_URL", "postgresql+psycopg://nobody:none@127.0.0.1:1/none")

from tijori.app import create_app  # noqa: E402
from tijori.app import mcp  # noqa: E402
from tijori.app.auth import Identity  # noqa: E402
from tijori.app.routes import MONTH_PATTERN  # noqa: E402

APP = create_app()
REG: mcp.Registry = APP.state.mcp
TYPED = {n: t for n, t in REG.tools.items() if t.single is not None}
OPS = {(g, a): op for g in mcp.TOOLS for a, op in REG.tools[g].ops.items()}  # every grouped action
FRESH_BODY = {"generated_at": "2026-10-04T05:00:00Z", "mailboxes": [],
              "accounts": [{"id": 1, "label": "HDFC ••0001", "kind": "bank", "last_txn_at": "2026-10-03",
                            "last_recorded_at": "2026-10-03T09:00:00Z", "last_seen_at": "2026-10-03T08:58:00Z",
                            "covered_through": "2026-09-30", "last_statement_end": "2026-09-30", "live_through": None}]}


def sample(sc: dict[str, Any]) -> Any:
    """A value the schema accepts, for required fields only."""
    if "anyOf" in sc:
        return sample(next(x for x in sc["anyOf"] if x.get("type") != "null"))
    if "enum" in sc:
        return sc["enum"][0]
    kind = sc.get("type")
    if kind == "object":
        return {k: sample(sc["properties"][k]) for k in sc.get("required", ())}
    if kind == "array":
        return [sample(sc.get("items", {})) for _ in range(max(1, sc.get("minItems", 1)))]
    if kind in ("integer", "number"):
        return 1
    if kind == "boolean":
        return True
    if sc.get("format") == "date":
        return "2026-09-01"
    return PATTERNS.get(sc.get("pattern", ""), "x")


PATTERNS = {MONTH_PATTERN: "2026-09", r"^\d{1,12}(\.\d{1,2})?$": "10.00", r"^rule:\d{1,18}$": "rule:1"}


class Recorder:
    """Stands in for mcp._asgi: records each request and answers from `answers` by path."""

    def __init__(self, answers: dict[str, tuple[int, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str, list[tuple[str, str]], bytes]] = []
        self.answers = answers or {}

    async def __call__(self, app, method, path, query, body, ctype, identity):  # noqa: ANN001
        self.calls.append((method, path, query, body))
        status, data = self.answers.get(path, (200, {"ok": 1}))
        return status, json.dumps(data).encode()


async def call(name: str, arguments: dict[str, Any], rec: Recorder) -> dict[str, Any]:
    with mock.patch.object(mcp, "_asgi", rec), mock.patch.object(mcp.CALLS, "allow", return_value=True):
        return await mcp._call(APP, REG, REG.tools[name], arguments, Identity("mcp", "0" * 64), 1)


def route_of(method: str, path: str) -> str | None:
    return next((n for rx, methods, n in REG.matchers if method in methods and rx.match(path)), None)


class Listing(unittest.TestCase):
    def test_typed_tools_are_the_used_few(self) -> None:
        self.assertEqual(len(mcp.TYPED), 10)
        self.assertLessEqual(set(mcp.TYPED.values()), set(OPS))
        self.assertEqual(len(set(mcp.TYPED.values())), len(mcp.TYPED))
        self.assertEqual(set(TYPED), set(mcp.TYPED) | set(mcp.EXTRA))
        self.assertEqual(set(REG.tools), set(TYPED) | set(mcp.TOOLS) | {mcp.DESCRIBE})

    def test_typed_tool_runs_its_grouped_action_op(self) -> None:
        for name, (group, action) in mcp.TYPED.items():
            self.assertIs(TYPED[name].single, OPS[(group, action)], name)

    def test_grouped_tools_keep_their_shape(self) -> None:
        for name in mcp.TOOLS:
            listing = REG.tools[name].listing
            self.assertEqual(set(listing), {"name", "description", "inputSchema", "annotations"})
            self.assertEqual(listing["inputSchema"]["required"], ["action"])
            self.assertTrue(listing["description"].endswith(tuple(f"{op.hint}" for op in REG.tools[name].ops.values())))

    def test_schemas_are_strict_and_inlined(self) -> None:
        for (group, action), op in OPS.items():
            sc, where = op.schema, f"{group}.{action}"
            self.assertEqual(sc["type"], "object", where)
            self.assertIs(sc["additionalProperties"], False, where)
            self.assertLessEqual(set(sc["required"]), set(sc["properties"]), where)
            self.assertNotIn("$ref", json.dumps(sc), where)
        for name, tool in TYPED.items():
            self.assertIn("Returns ", tool.listing["description"], name)

    def test_schemas_carry_requirements_and_enums(self) -> None:
        summary = TYPED["get_month_summary"].listing["inputSchema"]
        self.assertEqual(summary["required"], ["month"])
        self.assertEqual(summary["properties"]["month"]["pattern"], MONTH_PATTERN)
        search = TYPED["search_transactions"].listing["inputSchema"]["properties"]
        self.assertIn("not names", search["category"]["description"])
        self.assertNotIn("query", search)
        self.assertNotIn("limit", search)
        budget = OPS[("plan_spending", "set_budget")].schema
        self.assertEqual(set(budget["required"]), {"category_id", "amount"})
        link = OPS[("edit_transactions", "link")].schema
        self.assertEqual(set(link["required"]), {"txn_id", "other_txn_id", "kind"})
        self.assertIn("refund", link["properties"]["kind"]["enum"])

    def test_secrets_stay_out_of_schemas(self) -> None:
        self.assertNotIn("app_password", OPS[("edit_setup", "edit_mail_source")].schema["properties"])
        names = {n for op in OPS.values() for n in op.schema["properties"]}
        self.assertFalse({"password", "app_password", "token"} & names)

    def test_kinds_and_destructive_flags(self) -> None:
        for name, (group, _) in mcp.TYPED.items():
            self.assertEqual(TYPED[name].read_only, mcp.TOOLS[group][1] == mcp.READ, name)
            self.assertFalse(TYPED[name].listing["annotations"]["destructiveHint"], name)
        self.assertTrue(TYPED["get_freshness"].read_only)
        self.assertEqual(mcp.DESTRUCTIVE_OPS, {"delete_account", "delete_mail_source", "remove_statement_password",
                                               "revoke_invite", "put_profile", "import_sheet", "patch_settings"})
        hints = {n: REG.tools[n].listing["annotations"]["destructiveHint"] for n in mcp.TOOLS}
        self.assertEqual({n for n, d in hints.items() if d}, {"edit_setup"})  # unchanged from before the typed tools

    def test_describe_action_lists_every_action(self) -> None:
        props = REG.tools[mcp.DESCRIBE].listing["inputSchema"]["properties"]
        self.assertEqual(props["tool"]["enum"], list(mcp.TOOLS))
        self.assertEqual(set(props["action"]["enum"]), {a for _, a in OPS})
        self.assertTrue(REG.tools[mcp.DESCRIBE].read_only)


class Validation(unittest.TestCase):
    def check(self, name: str, args: dict[str, Any]) -> str | None:
        return mcp._check(TYPED[name].single.schema, args)

    def check_op(self, group: str, action: str, args: dict[str, Any]) -> str | None:
        return mcp._check(OPS[(group, action)].schema, args)

    def test_rejects_what_agents_got_wrong(self) -> None:
        self.assertEqual(self.check("get_month_summary", {}), "month is required")
        self.assertEqual(self.check("search_transactions", {"query": "swiggy"}), "unknown argument 'query'")
        self.assertEqual(self.check("search_transactions", {"limit": 5}), "unknown argument 'limit'")
        self.assertEqual(self.check("search_transactions", {"category": 12}), "category: expected array")

    def test_rejects_wrong_types_enums_and_patterns(self) -> None:
        self.assertEqual(self.check("get_month_summary", {"month": "2026-13"}), f"month: must match {MONTH_PATTERN}")
        self.assertEqual(self.check("get_transaction", {"txn_id": "12"}), "txn_id: expected integer")
        self.assertEqual(self.check("get_transaction", {"txn_id": True}), "txn_id: expected integer")
        self.assertIn("sort: one of date_desc", self.check("search_transactions", {"sort": "newest"}))
        self.assertIn("kind: one of transfer", self.check_op("edit_transactions", "link", {"txn_id": 1, "other_txn_id": 2, "kind": "x"}))

    def test_rejects_bad_nested_items(self) -> None:
        parts = [{"amount": "10.00", "category_id": 3}, {"amount": "5"}]
        self.assertEqual(self.check_op("edit_transactions", "split", {"txn_id": 1, "parts": parts}), "parts[1].category_id is required")
        parts[1] = {"amount": "5.123", "category_id": 4}
        self.assertIn("parts[1].amount: must match", self.check_op("edit_transactions", "split", {"txn_id": 1, "parts": parts}))

    def test_accepts_valid_input(self) -> None:
        self.assertIsNone(self.check("get_month_summary", {"month": "2026-09"}))
        self.assertIsNone(self.check("search_transactions", {"month": "2026-09", "category": ["12", "none"],
                                                             "min": "100", "sort": "amount_desc", "page_size": 20}))
        self.assertIsNone(self.check_op("edit_setup", "update_settings", {"notify_topic": None}))  # null clears it
        self.assertIsNone(self.check("get_freshness", {}))


class Dispatch(unittest.IsolatedAsyncioTestCase):
    async def test_each_typed_tool_reaches_its_route(self) -> None:
        for name, tool in TYPED.items():
            args = {"text": "x"} if name == "upload_statement" else sample(tool.single.schema)
            rec = Recorder()
            out = await call(name, args, rec)
            self.assertNotIn("isError", out, f"{name}: {out}")
            method, path, _, _ = rec.calls[0]
            self.assertEqual(method, tool.single.method, name)
            self.assertEqual(route_of(method, path), tool.single.name, name)

    async def test_bad_input_never_reaches_the_app(self) -> None:
        rec = Recorder()
        out = await call("get_month_summary", {"month": "Sept"}, rec)
        self.assertTrue(out["isError"])
        self.assertIn("month: must match", out["content"][0]["text"])
        self.assertIn("this tool takes (month: YYYY-MM)", out["content"][0]["text"])
        self.assertEqual(rec.calls, [])

    async def test_arguments_land_where_the_route_reads_them(self) -> None:
        rec = Recorder()
        await call("search_transactions", {"month": "2026-09", "category": ["12", "none"], "paid_with": "card"}, rec)
        self.assertEqual(rec.calls[0][2], [("month", "2026-09"), ("category", "12"), ("category", "none"), ("paid_with", "card")])
        rec = Recorder()
        await call("edit_transactions", {"action": "link", "args": {"txn_id": 5, "other_txn_id": 9, "kind": "refund"}}, rec)
        self.assertEqual(rec.calls[0][1], "/api/transactions/5/links")
        self.assertEqual(json.loads(rec.calls[0][3]), {"txn_id": 9, "kind": "refund"})

    async def test_describe_action_returns_each_actions_schema(self) -> None:
        for (group, action), op in OPS.items():
            rec = Recorder()
            out = await call(mcp.DESCRIBE, {"tool": group, "action": action}, rec)
            data = out["structuredContent"]
            self.assertEqual(rec.calls, [], f"{group}.{action}")  # metadata only: nothing runs
            self.assertEqual(data["arguments"], op.schema, f"{group}.{action}")
            self.assertEqual(data["returns"], op.returns, f"{group}.{action}")
            self.assertEqual(data["destructive"], op.name in mcp.DESTRUCTIVE_OPS, f"{group}.{action}")
            self.assertEqual(data["kind"], "read" if mcp.TOOLS[group][1] == mcp.READ else "write")
            typed = next((n for n, ta in mcp.TYPED.items() if ta == (group, action)), None)
            self.assertEqual(data["typed_tool"], typed, f"{group}.{action}")
            self.assertEqual(json.loads(out["content"][0]["text"]), data)
        add = (await call(mcp.DESCRIBE, {"tool": "edit_setup", "action": "add_account"}, Recorder()))["structuredContent"]
        delete = (await call(mcp.DESCRIBE, {"tool": "edit_setup", "action": "delete_account"}, Recorder()))["structuredContent"]
        self.assertEqual((add["destructive"], delete["destructive"]), (False, True))

    async def test_describe_action_refuses_bad_input(self) -> None:
        out = await call(mcp.DESCRIBE, {"tool": "get_reports", "action": "search"}, Recorder())
        self.assertIn("get_reports has no action 'search'", out["content"][0]["text"])
        out = await call(mcp.DESCRIBE, {"tool": "get_reports", "action": "nope"}, Recorder())
        self.assertIn("action: one of", out["content"][0]["text"])
        out = await call(mcp.DESCRIBE, {"tool": "get_reports"}, Recorder())
        self.assertIn("action is required", out["content"][0]["text"])

    async def test_grouped_tool_still_works(self) -> None:
        rec = Recorder({"/api/summary": (200, {"month": "2026-09"})})
        out = await call("get_reports", {"action": "summary", "args": {"month": "2026-09"}}, rec)
        self.assertEqual(route_of("GET", rec.calls[0][1]), "summary")
        self.assertEqual(out["structuredContent"]["month"], "2026-09")
        out = await call("get_reports", {"action": "nope"}, Recorder())
        self.assertIn("unknown action", out["content"][0]["text"])


class Freshness(unittest.IsolatedAsyncioTestCase):
    async def test_ledger_reads_carry_freshness(self) -> None:
        rec = Recorder({"/api/transactions": (200, {"items": [], "total": 0}), "/api/freshness": (200, FRESH_BODY)})
        out = await call("search_transactions", {"month": "2026-09"}, rec)
        self.assertEqual([c[1] for c in rec.calls], ["/api/transactions", "/api/freshness"])
        self.assertEqual(out["structuredContent"]["freshness"], FRESH_BODY)
        self.assertEqual(json.loads(out["content"][0]["text"])["freshness"]["accounts"][0]["last_txn_at"], "2026-10-03")

    async def test_grouped_reads_carry_it_too(self) -> None:
        rec = Recorder({"/api/freshness": (200, FRESH_BODY)})
        out = await call("get_reports", {"action": "months"}, rec)
        self.assertEqual(out["structuredContent"]["freshness"]["generated_at"], FRESH_BODY["generated_at"])

    async def test_other_reads_and_errors_skip_it(self) -> None:
        rec = Recorder()
        out = await call("get_transaction", {"txn_id": 4}, rec)
        self.assertEqual(len(rec.calls), 1)
        self.assertNotIn("freshness", out["structuredContent"])
        rec = Recorder({"/api/summary": (422, {"detail": "bad month"})})
        out = await call("get_month_summary", {"month": "2026-09"}, rec)
        self.assertTrue(out["isError"])
        self.assertEqual(len(rec.calls), 1)

    async def test_a_failed_freshness_keeps_the_answer(self) -> None:
        rec = Recorder({"/api/budgets": (200, {"month": "2026-09"}), "/api/freshness": (500, {"detail": "boom"})})
        with self.assertLogs(mcp.log, "WARNING"):
            out = await call("get_budgets", {"month": "2026-09"}, rec)
        self.assertEqual(out["structuredContent"], {"month": "2026-09"})

    def test_ledger_reads_point_at_coverage(self) -> None:
        self.assertNotIn("get_coverage", TYPED)  # unused by agents so far; get_setup action coverage
        for name in mcp.TYPED:
            self.assertEqual("get_setup action coverage" in TYPED[name].listing["description"],
                             TYPED[name].single.name in mcp.FRESH, name)

    def test_route_is_typed_only(self) -> None:
        self.assertEqual(TYPED["get_freshness"].single.path, "/api/freshness")
        self.assertNotIn("freshness", REG.tools["get_setup"].ops)
        self.assertLessEqual(mcp.FRESH, {op.name for t in REG.tools.values() for op in t.ops.values()})


if __name__ == "__main__":
    unittest.main()
