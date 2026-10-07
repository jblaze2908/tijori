"""Orders against a migrated Postgres, through POST /mcp with real tokens, RLS and the owner lock.
Skipped unless TIJORI_TEST_DATABASE_URL (the tijori_app role) and TIJORI_TEST_ADMIN_DATABASE_URL (owner) are set."""

import hashlib
import os
import secrets
import unittest
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session
from test_freshness_db import post_mcp, tool_call

from tijori.app import create_app
from tijori.bootstrap import seed_categories
from tijori.db import MemberContext, member_session
from tijori.models import (
    Account,
    Category,
    Household,
    McpToken,
    Member,
    MerchantOrder,
    Txn,
)
from tijori.parsers.base import Observation, Statement, StatementSummary
from tijori.services.common import IST, today_ist
from tijori.services.ingest import ingest_statement
from tijori.settings import Settings

APP_URL = os.environ.get("TIJORI_TEST_DATABASE_URL")
ADMIN_URL = os.environ.get("TIJORI_TEST_ADMIN_DATABASE_URL")


def at(d: date, hh: int = 12, mm: int = 0) -> str:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST).isoformat()


def order(source: str, no: str, placed: str, total: str, **extra: Any) -> dict[str, Any]:
    return {"source": source, "order_no": no, "placed_at": placed, "status": "delivered", "bill_total": total, **extra}


@unittest.skipUnless(APP_URL and ADMIN_URL, "needs TIJORI_TEST_DATABASE_URL and TIJORI_TEST_ADMIN_DATABASE_URL")
class OrdersDB(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.admin = create_engine(ADMIN_URL)
        tag = secrets.token_hex(4)
        cls.tokens = {k: f"tjm_test_{k}_{secrets.token_hex(8)}" for k in ("owner", "other")}
        cls.today = today_ist()
        with Session(cls.admin) as s, s.begin():
            h = Household(name=f"test-{tag}")
            s.add(h)
            s.flush()
            seed_categories(s, h.id)
            owner = Member(household_id=h.id, name="Owner", email=f"owner-{tag}@example.com")
            other = Member(household_id=h.id, name="Other", email=f"other-{tag}@example.com")
            s.add_all([owner, other])
            s.flush()
            bank = Account(member_id=owner.id, kind="bank", institution="HDFC", name="HDFC savings", mask="0001")
            meal = Account(member_id=owner.id, kind="wallet", institution="Pluxee", name="Meal card", mask="0002")
            s.add_all([bank, meal])
            s.flush()
            for n, (d, amount, payee) in enumerate(((date(2026, 9, 27), "1092", "blinkit"),
                                                    (date(2025, 5, 6), "1731", "blinkit"),
                                                    (date(2026, 9, 10), "300", "zomato"))):
                s.add(Txn(member_id=owner.id, account_id=bank.id, occurred_at=d, amount=Decimal(amount), direction="debit",
                          kind="spend", payee_key=f"brand:{payee}", dedupe_key=f"{tag}{n}"))
            for key, member in (("owner", owner), ("other", other)):
                s.add(McpToken(member_id=member.id, name=key, token_hash=hashlib.sha256(cls.tokens[key].encode()).hexdigest()))
            cls.household, cls.owner, cls.other = h.id, MemberContext(owner.id, h.id), MemberContext(other.id, h.id)
            cls.bank, cls.meal, cls.tag = bank.id, meal.id, tag
            cls.owner_email = owner.email
        cls.app = create_app(Settings(env="test", database_url=APP_URL, owner_email=cls.owner_email))

    @classmethod
    def tearDownClass(cls) -> None:
        with Session(cls.admin) as s, s.begin():
            s.execute(delete(Household).where(Household.id == cls.household))
        cls.app.state.engine.dispose()
        cls.admin.dispose()

    async def call(self, name: str, args: dict[str, Any], token: str = "owner") -> dict[str, Any]:
        status, body = await post_mcp(self.app, self.tokens[token], tool_call(name, args))
        self.assertEqual(status, 200)
        result = body["result"]
        self.assertFalse(result.get("isError"), result["content"][0]["text"])
        return result["structuredContent"]

    async def test_orders_end_to_end(self) -> None:
        batch = [
            order("blinkit", "ORDA", at(date(2026, 9, 27), 12, 11), "1092", payment="Paid via UPI", item_total="1082",
                  delivery_address="Flat 1, Example Road", charges={"handling_charge": "9", "product_discount": "-194"},
                  items=[{"name": "Instant Coffee", "unit": "100 g", "qty": 1, "line_price": "769", "unit_price": "769"},
                         {"name": "Potato Chips", "unit": "54 g", "qty": 1, "line_price": "25"}]),
            order("blinkit", "ORDB", at(date(2025, 5, 6), 19, 3), "1265"),  # one payment, two orders in the same minute
            order("blinkit", "ORDC", at(date(2025, 5, 6), 19, 3), "466"),
            order("blinkit", "ORDK", at(date(2026, 9, 20), 9, 44), "206", payment="Paid via Card (XXXX XXXX 0002)"),
            order("zomato", "ZD", at(date(2026, 9, 6), 20, 25), "529.58", store="Waffle Place",
                  items=[{"name": "Oreo crunch waffle", "qty": 1, "note": "Oreo biscuit"}]),
            order("zomato", "ZF", at(date(2026, 9, 10), 13), "300"),  # two orders, one debit: never guess
            order("zomato", "ZG", at(date(2026, 9, 10), 20), "300"),
            {**order("blinkit", "ORDH", at(date(2026, 9, 1)), "227"), "status": "cancelled"},
        ]
        out = await self.call("record_orders", {"orders": batch})
        states = {o["order_no"]: o for o in out["orders"]}
        self.assertEqual(out["created"], 8)
        self.assertEqual({k: v["match_state"] for k, v in states.items()},
                         {"ORDA": "matched", "ORDB": "matched", "ORDC": "matched", "ORDK": "assigned", "ZD": "unmatched",
                          "ZF": "ambiguous", "ZG": "ambiguous", "ORDH": "cancelled"})
        self.assertEqual(states["ORDB"]["txn_id"], states["ORDC"]["txn_id"])
        again = await self.call("record_orders", {"orders": batch})
        self.assertEqual((again["created"], again["updated"], again["unchanged"]), (0, 0, 8))

        # The receipt names the meal card's last 4 digits: on it at once, out of totals.
        with member_session(self.app.state.engine, self.owner) as s:
            t = s.get(Txn, states["ORDK"]["txn_id"])
            self.assertEqual((t.account_id, t.bucket, t.sources, t.rule_id, s.get(Category, t.category_id).name),
                             (self.meal, "excluded", ["order"], "order:card", "Groceries"))

        # Zomato says nothing about payment: someone assigns it. Orders a debit paid can't be.
        refused = await post_mcp(self.app, self.tokens["owner"], tool_call("assign_orders", {
            "orders": [{"source": "blinkit", "order_no": "ORDA"}], "account_id": self.meal}))
        self.assertTrue(refused[1]["result"]["isError"])
        put = await self.call("assign_orders", {"orders": [{"source": "zomato", "order_no": "ZD"}], "account_id": self.meal})
        zd = put["orders"][0]["txn_id"]
        self.assertEqual(put["orders"][0]["match_state"], "assigned")
        with member_session(self.app.state.engine, self.owner) as s:
            t = s.get(Txn, zd)
            self.assertEqual((t.account_id, t.amount, t.bucket, t.rule_id, s.get(Category, t.category_id).name, t.review_reason),
                             (self.meal, Decimal("529.58"), "excluded", "order:assigned", "Eating out", None))
        self.assertEqual((await self.call("record_orders", {"orders": [batch[4]]}))["orders"][0]["match_state"], "assigned")

        # Filing the receipt txn by hand keeps it out of spend.
        await self.call("classify", {"action": "set_category", "args": {"txn_id": zd, "category": "Groceries",
                                                                        "scope": "this"}})
        with member_session(self.app.state.engine, self.owner) as s:
            self.assertEqual(s.get(Txn, zd).bucket, "excluded")

        detail = await self.call("get_transaction", {"txn_id": states["ORDA"]["txn_id"]})
        [o] = detail["transaction"]["orders"]
        self.assertEqual((o["delivery_address"], o["charges"]["product_discount"], [i["name"] for i in o["items"]]),
                         ("Flat 1, Example Road", "-194.00", ["Instant Coffee", "Potato Chips"]))

        hits = await self.call("search_order_items", {"q": "coffee"})
        self.assertEqual((hits["total"], hits["totals"]["line_price"], hits["items"][0]["order_no"]), (1, "769.00", "ORDA"))
        by_store = await self.call("search_order_items", {"q": "waffle place"})
        self.assertEqual((by_store["total"], by_store["totals"]["priced"]), (1, 0))

        # The debit turns up late: it takes the order back and the receipt txn goes.
        with Session(self.admin) as s, s.begin():
            s.add(Txn(member_id=self.owner.member_id, account_id=self.bank, occurred_at=date(2026, 9, 7),
                      amount=Decimal("529.58"), direction="debit", kind="spend", payee_key="brand:zomato",
                      dedupe_key=f"{self.tag}late"))
        late = await self.call("record_orders", {"orders": [batch[4]]})
        self.assertEqual(late["orders"][0]["match_state"], "matched")
        with member_session(self.app.state.engine, self.owner) as s:
            self.assertIsNone(s.get(Txn, zd))

        # A changed amount unlinks and matches again; with no debit and no card it waits, unguessed.
        moved = await self.call("record_orders", {"orders": [{**batch[0], "bill_total": "1093"}]})
        self.assertEqual((moved["updated"], moved["orders"][0]["match_state"]), (1, "unmatched"))
        # Taking an assignment off returns the order to what the debits say.
        off = await self.call("assign_orders", {"orders": [{"source": "zomato", "order_no": "ZF"}], "account_id": self.meal})
        back = await self.call("assign_orders", {"orders": [{"source": "zomato", "order_no": "ZF"}], "account_id": None})
        self.assertEqual((off["orders"][0]["match_state"], back["orders"][0]["match_state"]), ("assigned", "ambiguous"))

    async def test_statement_links_an_order_recorded_before_its_debit(self) -> None:
        late = order("zomato", "ZLATE", at(date(2026, 8, 20), 20, 5), "777.77", store="Momo Place")
        self.assertEqual((await self.call("record_orders", {"orders": [late]}))["orders"][0]["match_state"], "unmatched")
        line = Observation(occurred_at=date(2026, 8, 20), amount=Decimal("777.77"), direction="debit",
                           narration="UPI/DR/612345678901/ZOMATO L/YESB/zomato-ord/Zomato", balance_after=Decimal("222.23"),
                           ref_no="612345678901")
        st = Statement(institution="SBI", account_mask="0003", period_start=date(2026, 8, 20), period_end=date(2026, 8, 20),
                       summary=StatementSummary(Decimal("1000.00"), Decimal("222.23"), 1, 0, Decimal("777.77"), Decimal(0)),
                       lines=(line,), parser="test", parser_version="1")
        with member_session(self.app.state.engine, self.owner) as s:
            ingest_statement(s, self.owner, "test", st, filename=None, sha256=self.tag * 8, blob_ref="test")
        with member_session(self.app.state.engine, self.owner) as s:
            o = s.scalars(select(MerchantOrder).where(MerchantOrder.order_no == "ZLATE")).one()
            self.assertEqual((o.match_state, s.get(Txn, o.txn_id).amount), ("matched", Decimal("777.77")))

    async def test_rls_and_owner_lock(self) -> None:
        await self.call("record_orders", {"orders": [order("blinkit", "ORDRLS", at(date(2026, 8, 1)), "10")]})
        with member_session(self.app.state.engine, self.other) as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(MerchantOrder)), 0)
        _, body = await post_mcp(self.app, self.tokens["other"], tool_call("search_order_items", {"q": "x"}))
        self.assertIn("403: Tijori is private to its owner", body["result"]["content"][0]["text"])

    async def test_refuses_bad_batches(self) -> None:
        dup = [order("blinkit", "ORDX", at(date(2026, 8, 2)), "10")] * 2
        _, body = await post_mcp(self.app, self.tokens["owner"], tool_call("record_orders", {"orders": dup}))
        self.assertTrue(body["result"]["isError"])
        naive = [order("blinkit", "ORDY", "2026-08-02T12:00:00", "10")]
        _, body = await post_mcp(self.app, self.tokens["owner"], tool_call("record_orders", {"orders": naive}))
        self.assertTrue(body["result"]["isError"])
