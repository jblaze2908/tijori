"""Freshness and typed tools against a migrated Postgres, through POST /mcp with real tokens, RLS and the owner lock.
Skipped unless TIJORI_TEST_DATABASE_URL (the tijori_app role) and TIJORI_TEST_ADMIN_DATABASE_URL (owner) are set."""

import hashlib
import json
import os
import secrets
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from tijori.app import create_app
from tijori.db import MemberContext, member_session
from tijori.models import Account, Household, MailSource, McpToken, Member, Observation, RawMessage, Statement, Txn
from tijori.parsers.alerts import COVERED, AlertParser
from tijori.services.freshness import freshness
from tijori.services.mcp_oauth import READ_SCOPE
from tijori.settings import Settings

APP_URL = os.environ.get("TIJORI_TEST_DATABASE_URL")
ADMIN_URL = os.environ.get("TIJORI_TEST_ADMIN_DATABASE_URL")


async def post_mcp(app: Any, token: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    raw = json.dumps(body).encode()
    out: dict[str, Any] = {"status": 500, "body": b""}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(msg: dict[str, Any]) -> None:
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/mcp", "raw_path": b"/mcp", "root_path": "", "query_string": b"", "client": ("127.0.0.1", 0),
             "server": ("test", 80), "headers": [(b"content-type", b"application/json"),
                                                 (b"authorization", f"Bearer {token}".encode())]}
    await app(scope, receive, send)
    return out["status"], json.loads(out["body"]) if out["body"] else {}


def tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


@unittest.skipUnless(APP_URL and ADMIN_URL, "needs TIJORI_TEST_DATABASE_URL and TIJORI_TEST_ADMIN_DATABASE_URL")
class FreshnessDB(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.admin = create_engine(ADMIN_URL)
        cls.polled, cls.alerted = datetime(2026, 10, 4, 5, 0, tzinfo=UTC), datetime(2026, 10, 3, 8, 58, tzinfo=UTC)
        assert ("HDFC", "bank") in COVERED  # live_through below needs an alert-covered account
        tag = secrets.token_hex(4)
        cls.tokens = {k: f"tjm_test_{k}_{secrets.token_hex(8)}" for k in ("owner", "other", "reader")}
        with Session(cls.admin) as s, s.begin():
            h = Household(name=f"test-{tag}")
            s.add(h)
            s.flush()
            owner = Member(household_id=h.id, name="Owner", email=f"owner-{tag}@example.com")
            other = Member(household_id=h.id, name="Other", email=f"other-{tag}@example.com")
            s.add_all([owner, other])
            s.flush()
            bank = Account(member_id=owner.id, kind="bank", institution="HDFC", name="HDFC savings", mask="0001")
            card = Account(member_id=owner.id, kind="card", institution="ICICI", mask="0002")  # no data yet
            s.add_all([bank, card])
            s.flush()
            for d, sha in ((date(2026, 9, 20), "a"), (date(2026, 10, 3), "b")):
                s.add(Txn(member_id=owner.id, account_id=bank.id, occurred_at=d, amount=Decimal("100"), direction="debit",
                          kind="spend", dedupe_key=f"{tag}{sha}"))
            box = MailSource(member_id=owner.id, provider="gmail", host="imap.gmail.com", port=993, status="ok",
                             username="owner@example.com", last_poll_at=cls.polled, last_ok_poll_at=cls.polled)
            s.add(box)
            s.flush()
            msg = RawMessage(member_id=owner.id, received_at=cls.alerted, sha256=tag * 8, blob_ref="test",
                             mail_source_id=box.id)
            s.add(msg)
            s.flush()
            s.add(Observation(member_id=owner.id, raw_message_id=msg.id, parser=AlertParser.name, parser_version="1",
                              account_id=bank.id, occurred_at=date(2026, 10, 3), amount=Decimal("100"), direction="debit"))
            s.add(Statement(member_id=owner.id, account_id=bank.id, period_start=date(2026, 9, 1),
                            period_end=date(2026, 9, 30), opening=Decimal("0"), closing=Decimal("0"),
                            reconciled_at=cls.alerted))
            s.add(Statement(member_id=owner.id, account_id=bank.id, period_start=date(2026, 10, 1),
                            period_end=date(2026, 10, 2), opening=Decimal("0"), closing=Decimal("1")))  # not reconciled
            for key, member, scope in (("owner", owner, None), ("other", other, None), ("reader", owner, READ_SCOPE)):
                s.add(McpToken(member_id=member.id, name=key, scope=scope,
                               token_hash=hashlib.sha256(cls.tokens[key].encode()).hexdigest()))
            cls.household, cls.owner = h.id, MemberContext(owner.id, h.id)
            cls.owner_email, cls.bank, cls.card = owner.email, bank.id, card.id
        cls.app = create_app(Settings(env="test", database_url=APP_URL, owner_email=cls.owner_email))

    @classmethod
    def tearDownClass(cls) -> None:
        with Session(cls.admin) as s, s.begin():
            s.execute(delete(Household).where(Household.id == cls.household))
        cls.app.state.engine.dispose()
        cls.admin.dispose()

    def test_service_reads_each_source(self) -> None:
        with member_session(self.app.state.engine, self.owner) as s:
            out = freshness(s, self.owner.member_id, now=self.polled)
        by_id = {a["id"]: a for a in out["accounts"]}
        bank, card = by_id[self.bank], by_id[self.card]
        self.assertEqual(bank["label"], "HDFC savings ••0001")
        self.assertEqual(bank["last_txn_at"], date(2026, 10, 3))
        self.assertIsNotNone(bank["last_recorded_at"])
        self.assertEqual(bank["last_seen_at"], self.alerted)
        self.assertEqual(bank["covered_through"], date(2026, 9, 30))  # reconciled only
        self.assertEqual(bank["last_statement_end"], date(2026, 10, 2))
        self.assertEqual(bank["live_through"], self.polled)
        fields = ("last_txn_at", "last_recorded_at", "last_seen_at", "covered_through", "last_statement_end", "live_through")
        self.assertEqual({k: card[k] for k in fields}, dict.fromkeys(fields))
        self.assertEqual(out["mailboxes"], [{"id": out["mailboxes"][0]["id"], "label": "tijori",
                                             "last_ok_poll_at": self.polled, "healthy": True, "problem": None}])

    async def test_typed_read_carries_freshness(self) -> None:
        status, body = await post_mcp(self.app, self.tokens["owner"], tool_call("search_transactions", {"month": "2026-10"}))
        self.assertEqual(status, 200)
        result = body["result"]["structuredContent"]
        self.assertEqual(result["total"], 1)
        fresh = {a["id"]: a for a in result["freshness"]["accounts"]}
        self.assertEqual(fresh[self.bank]["last_txn_at"], "2026-10-03")

    async def test_typed_tool_refuses_bad_args_before_the_db(self) -> None:
        _, body = await post_mcp(self.app, self.tokens["owner"], tool_call("get_month_summary", {}))
        self.assertTrue(body["result"]["isError"])
        self.assertIn("month is required", body["result"]["content"][0]["text"])

    async def test_owner_lock_holds_for_typed_tools(self) -> None:
        _, body = await post_mcp(self.app, self.tokens["other"], tool_call("get_freshness", {}))
        self.assertTrue(body["result"]["isError"])
        self.assertIn("403: Tijori is private to its owner", body["result"]["content"][0]["text"])

    async def test_read_only_grant_sees_no_typed_writes(self) -> None:
        _, body = await post_mcp(self.app, self.tokens["reader"], {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in body["result"]["tools"]}
        self.assertLessEqual({"get_freshness", "describe_action", "search_transactions", "get_setup"}, set(tools))
        self.assertNotIn("plan_spending", tools)
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in tools.values()))
        call = tool_call("plan_spending", {"action": "set_budget", "args": {"category_id": 1, "amount": "1"}})
        status, body = await post_mcp(self.app, self.tokens["reader"], call)
        self.assertEqual(status, 403)

    async def test_describe_action_needs_a_token(self) -> None:
        status, _ = await post_mcp(self.app, "tjm_not_a_token", tool_call("describe_action", {"tool": "get_setup", "action": "coverage"}))
        self.assertEqual(status, 401)
        _, body = await post_mcp(self.app, self.tokens["owner"], tool_call("describe_action", {"tool": "get_setup", "action": "coverage"}))
        self.assertEqual(body["result"]["structuredContent"]["arguments"]["properties"]["from"]["format"], "date")


if __name__ == "__main__":
    unittest.main()
