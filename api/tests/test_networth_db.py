"""Net worth by account, against a migrated Postgres. The requirements: any bank's account counts, a card's
outstanding is a liability, what the member sets (which accounts count, liquid, asset class, values by hand) is what
net worth uses, a value a statement prints lands on one account of its own, and another member sees none of it.
Skipped unless TIJORI_TEST_DATABASE_URL (the tijori_app role) and TIJORI_TEST_ADMIN_DATABASE_URL (owner) are set."""

import hashlib
import os
import secrets
import unittest
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session
from test_freshness_db import post_mcp, tool_call

from tijori.app import create_app
from tijori.bootstrap import seed_categories
from tijori.db import MemberContext, member_session
from tijori.models import Account, AccountValuation, Household, McpToken, Member, Txn
from tijori.parsers.base import Observation, Statement, StatementSummary
from tijori.services import members, networth
from tijori.services.common import today_ist
from tijori.services.ingest import ingest_statement
from tijori.settings import Settings

APP_URL = os.environ.get("TIJORI_TEST_DATABASE_URL")
ADMIN_URL = os.environ.get("TIJORI_TEST_ADMIN_DATABASE_URL")
D = Decimal


@unittest.skipUnless(APP_URL and ADMIN_URL, "needs TIJORI_TEST_DATABASE_URL and TIJORI_TEST_ADMIN_DATABASE_URL")
class NetWorthDB(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.admin = create_engine(ADMIN_URL)
        tag = secrets.token_hex(4)
        cls.token = f"tjm_test_nw_{secrets.token_hex(8)}"
        with Session(cls.admin) as s, s.begin():
            h = Household(name=f"test-{tag}")
            s.add(h)
            s.flush()
            seed_categories(s, h.id)
            owner = Member(household_id=h.id, name="Owner", email=f"owner-{tag}@example.com")
            other = Member(household_id=h.id, name="Other", email=f"other-{tag}@example.com")
            s.add_all([owner, other])
            s.flush()
            s.add(McpToken(member_id=owner.id, name="owner", token_hash=hashlib.sha256(cls.token.encode()).hexdigest()))
            cls.household, cls.owner, cls.other = h.id, MemberContext(owner.id, h.id), MemberContext(other.id, h.id)
            cls.owner_email, cls.tag = owner.email, tag
        cls.app = create_app(Settings(env="test", database_url=APP_URL, owner_email=cls.owner_email))
        cls.today = today_ist()
        cls.n = 0

    @classmethod
    def tearDownClass(cls) -> None:
        with Session(cls.admin) as s, s.begin():
            s.execute(delete(Household).where(Household.id == cls.household))
        cls.app.state.engine.dispose()
        cls.admin.dispose()

    def session(self, ctx: MemberContext | None = None) -> Any:
        return member_session(self.app.state.engine, ctx or self.owner)

    def statement(self, institution: str, mask: str, closing: str, *, kind: str = "bank", days_ago: int = 20,
                  components: tuple[tuple[str, Decimal], ...] = ()) -> int:
        """A one-line statement ending `days_ago` days back, closing at `closing` (a card: what is due)."""
        type(self).n += 1
        end = self.today - timedelta(days=days_ago)
        amount = D("100.00")
        if kind == "card":
            line = Observation(occurred_at=end, amount=amount, direction="debit", narration=f"SHOP {self.n}")
            due = D(closing)
            summary = StatementSummary(-(due - amount), -due, 1, 0, amount, D(0))
        else:
            line = Observation(occurred_at=end, amount=amount, direction="credit", narration=f"INTEREST {self.n}",
                               balance_after=D(closing))
            summary = StatementSummary(D(closing) - amount, D(closing), 0, 1, D(0), amount)
        st = Statement(institution=institution, account_mask=mask, period_start=end - timedelta(days=29), period_end=end,
                       summary=summary, lines=(line,), parser="test", parser_version="1", account_kind=kind,  # type: ignore[arg-type]
                       components=components)
        with self.session() as s:
            out = ingest_statement(s, self.owner, "test", st, filename=None,
                                   sha256=hashlib.sha256(f"{self.tag}{self.n}".encode()).hexdigest(), blob_ref="test")
        return out["account"]["id"]

    def txn(self, account_id: int, amount: str, direction: str, days_ago: int) -> None:
        type(self).n += 1
        with self.session() as s:
            s.add(Txn(member_id=self.owner.member_id, account_id=account_id, occurred_at=self.today - timedelta(days=days_ago),
                      amount=D(amount), direction=direction, kind="spend", status="pending", sources=["alert"],
                      dedupe_key=f"{self.tag}-{self.n}"))

    def live(self) -> dict[str, Any]:
        with self.session() as s:
            return networth.live(s, self.owner.member_id, self.today)

    def amount(self, live: dict[str, Any], account_id: int) -> Decimal | None:
        return next((D(c["amount"]) for c in live["components"] if c["account_id"] == account_id), None)

    def test_an_account_at_any_bank_counts(self) -> None:
        aid = self.statement("ICICI", "0004", "50000.00")
        self.assertEqual(self.amount(self.live(), aid), D("50000.00"))

    def test_card_outstanding_is_owed_from_the_purchase_not_the_bill(self) -> None:
        bank = self.statement("AXIS", "0005", "40000.00", days_ago=25)
        card = self.statement("AXIS", "0006", "12000.00", kind="card")
        before = self.live()
        self.assertEqual(self.amount(before, card), D("-12000.00"))
        self.txn(card, "3000.00", "debit", 5)  # a purchase since the statement
        bought = self.live()
        self.assertEqual(self.amount(bought, card), D("-15000.00"))
        self.assertEqual(D(bought["net_worth"]), D(before["net_worth"]) - D("3000.00"))
        self.txn(bank, "12000.00", "debit", 2)  # the bill, paid from the bank: both sides move, net worth doesn't
        self.txn(card, "12000.00", "credit", 2)
        paid = self.live()
        self.assertEqual((self.amount(paid, bank), self.amount(paid, card)), (D("28000.00"), D("-3000.00")))
        self.assertEqual(paid["net_worth"], bought["net_worth"])

    def test_the_members_settings_decide_how_an_account_counts(self) -> None:
        aid = self.statement("KOTAK", "0007", "20000.00")
        base = self.live()
        self.assertEqual(next(c for c in base["components"] if c["account_id"] == aid)["asset_class"], "cash")
        with self.session() as s:
            members.update_account(s, self.owner, "test", aid, {"liquid": False, "asset_class": "other"})
        moved = self.live()
        self.assertEqual(D(moved["liquid"]), D(base["liquid"]) - D("20000.00"))
        self.assertEqual(next(c for c in moved["components"] if c["account_id"] == aid)["asset_class"], "other")
        with self.session() as s:
            out = members.update_account(s, self.owner, "test", aid, {"in_net_worth": False, "liquid": None})
        self.assertTrue(out["liquid"])  # back to the kind's default
        gone = self.live()
        self.assertIsNone(self.amount(gone, aid))
        self.assertEqual(D(gone["net_worth"]), D(base["net_worth"]) - D("20000.00"))

    async def test_values_set_by_hand_for_assets_and_liabilities(self) -> None:
        with self.session() as s:
            house = members.create_account(s, self.owner, "test", institution="Home", kind="property", values={})["id"]
            loan = members.create_account(s, self.owner, "test", institution="Example Bank", kind="loan",
                                          values={"subtype": "home"})["id"]
        status, body = await post_mcp(self.app, self.token, tool_call("edit_net_worth", {
            "action": "set_value", "args": {"account_id": house, "amount": "8000000", "as_of": str(self.today)}}))
        self.assertEqual(status, 200)
        self.assertFalse(body["result"].get("isError"), body["result"]["content"][0]["text"])
        with self.session() as s:
            networth.set_valuation(s, self.owner, "test", loan, D("3000000"), self.today - timedelta(days=40))
        live = self.live()
        self.assertEqual((self.amount(live, house), self.amount(live, loan)), (D("8000000.00"), D("-3000000.00")))
        with self.session() as s:
            networth.delete_valuation(s, self.owner, "test", house, self.today)
        self.assertIsNone(self.amount(self.live(), house))

    def test_a_new_account_valued_today_keeps_history(self) -> None:
        """Seen in a local run: giving a just-added NPS account its first value today cut history to today, so every
        "since 1st" went blank. A value set by hand stands for the past until the member says when it opened."""
        self.statement("IDFC", "0010", "10000.00", days_ago=70)
        before = self.live()["history"][0]["date"]
        with self.session() as s:
            nps = members.create_account(s, self.owner, "test", institution="NPS", kind="retirement",
                                         values={"subtype": "nps"})["id"]
            networth.set_valuation(s, self.owner, "test", nps, D("95000"), self.today)
        live = self.live()
        self.assertEqual(live["history"][0]["date"], before)
        self.assertEqual(self.amount(live, nps), D("95000.00"))

    def test_a_printed_balance_lands_on_one_account(self) -> None:
        ppf = (("ppf", D("150000.00")),)
        self.statement("SBI", "0008", "1000.00", components=ppf)
        self.statement("SBI", "0008", "1000.00", days_ago=10, components=ppf)
        with self.session() as s:
            ids = s.scalars(select(Account.id).where(Account.member_id == self.owner.member_id,
                                                     Account.kind == "retirement", Account.subtype == "ppf")).all()
            self.assertEqual(len(ids), 1)
            n = s.scalar(select(func.count()).select_from(AccountValuation).where(AccountValuation.account_id == ids[0]))
        self.assertEqual(n, 2)  # one value per statement date
        self.assertEqual(self.amount(self.live(), ids[0]), D("150000.00"))

    def test_another_member_sees_and_sets_nothing(self) -> None:
        aid = self.statement("YES", "0009", "5000.00")
        with self.session() as s:
            networth.set_valuation(s, self.owner, "test", aid, D("5100.00"), self.today)
        with self.session(self.other) as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(AccountValuation)), 0)
            self.assertEqual(networth.live(s, self.other.member_id, self.today)["components"], [])
            with self.assertRaises(Exception):
                networth.set_valuation(s, self.other, "test", aid, D("1.00"), self.today)


if __name__ == "__main__":
    unittest.main()
