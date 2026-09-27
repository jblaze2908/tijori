"""Default category taxonomy, seeded per household.

`bucket` is the dashboard grouping (PLAN §8): everyday, oneoff and card make up expense;
invest is money moved into assets; income is money earned; excluded never counts. A category with a
`credit_bucket` (the people categories) counts money sent as spend and money received as income.
"""

from dataclasses import dataclass
from typing import Literal

Kind = Literal["spend", "income", "transfer", "investment", "refund", "fee", "cash"]
Bucket = Literal["everyday", "oneoff", "card", "invest", "income", "excluded"]

KINDS: tuple[Kind, ...] = ("spend", "income", "transfer", "investment", "refund", "fee", "cash")
BUCKETS: tuple[Bucket, ...] = ("everyday", "oneoff", "card", "invest", "income", "excluded")
EXPENSE_BUCKETS: tuple[Bucket, ...] = ("everyday", "oneoff", "card")


@dataclass(frozen=True, slots=True)
class CategoryDef:
    name: str
    kind: Kind
    bucket: Bucket
    description: str
    credit_bucket: Bucket | None = None

    def for_direction(self, direction: str) -> tuple[Bucket, Kind]:
        return bucket_kind(self.bucket, self.kind, self.credit_bucket, direction)


def bucket_kind(bucket: Bucket, kind: Kind, credit_bucket: Bucket | None, direction: str) -> tuple[Bucket, Kind]:
    """The bucket and kind a txn takes from its category; credits differ only when credit_bucket is set."""
    if direction == "credit" and credit_bucket:
        return credit_bucket, "income" if credit_bucket == "income" else kind
    return bucket, kind


DEFAULT_CATEGORIES: tuple[CategoryDef, ...] = (
    # Spend
    CategoryDef("Groceries", "spend", "everyday", "Supermarkets and quick commerce: Blinkit, JioMart, Zepto, BigBasket."),
    CategoryDef("Eating out", "spend", "everyday", "Restaurants, cafes and food delivery: Zomato, Swiggy, Domino's."),
    CategoryDef("Shopping", "spend", "everyday", "Online and retail purchases: Amazon, Flipkart, apparel, electronics."),
    CategoryDef("Bills & subscriptions", "spend", "everyday", "Utilities, recharges, broadband and recurring digital services."),
    CategoryDef("Local shops", "spend", "everyday", "Small in-person payments to neighbourhood merchants via QR."),
    CategoryDef("Travel", "spend", "everyday", "Tolls, fuel, cabs, trains, flights and hotels."),
    CategoryDef("Health", "spend", "everyday", "Doctors, hospitals, pharmacies and diagnostics."),
    CategoryDef("Services", "spend", "everyday", "Paid services: government service centres, repairs, home services."),
    CategoryDef("Bank charges", "fee", "everyday", "Bank fees: mandate bounces, SMS alerts, IMPS and card charges."),
    CategoryDef("Family", "spend", "oneoff", "Money to and from family: sent counts as spend, received as income.", "income"),
    CategoryDef("Friends", "spend", "oneoff", "Money to and from friends: sent counts as spend, received as income.", "income"),
    CategoryDef("Social circle", "spend", "oneoff",
                "People you know who aren't friends or family: sent counts as spend, received as income.", "income"),
    CategoryDef("Entertainment", "spend", "everyday", "Movies, events, games and outings."),
    CategoryDef("Insurance", "spend", "oneoff", "Health, life and vehicle insurance premiums."),
    CategoryDef("Tax", "spend", "oneoff", "Income tax and other direct tax payments."),
    CategoryDef("Cash", "cash", "oneoff", "Cash withdrawn by ATM or self cheque; spent untracked."),
    # Not spend
    CategoryDef("Salary", "income", "income", "Salary and bonus from an employer."),
    CategoryDef("Interest", "income", "income", "Savings account and deposit interest."),
    CategoryDef("Dividends", "income", "income", "Dividends paid by companies you hold."),
    CategoryDef("Other income", "income", "income", "Money received from people or one-off credits."),
    CategoryDef("Refunds", "refund", "income", "Refunds and cashback for earlier purchases."),
    CategoryDef("Reversals", "refund", "excluded", "Failed payments and their reversal credits; net to zero."),
    CategoryDef("Self transfer", "transfer", "excluded", "Moves between your own accounts."),
    CategoryDef("Card bill payment", "transfer", "card", "Credit card bill payments (e.g. via CRED); stands in for card spend until card statements are parsed."),
    CategoryDef("Pass-through", "transfer", "excluded", "Money that arrives and leaves the same day on someone else's behalf."),
    CategoryDef("Loans", "transfer", "excluded", "Money lent or borrowed, and its repayments; tracked per loan, never spend or income."),
    CategoryDef("Investments", "investment", "invest", "SIPs, stock buys, PPF and deposit contributions."),
    CategoryDef("Investment redemptions", "investment", "excluded", "Deposit maturities and investment withdrawals back to the bank."),
)

BY_NAME: dict[str, CategoryDef] = {c.name: c for c in DEFAULT_CATEGORIES}


def kind_of(category: str) -> Kind:
    return BY_NAME[category].kind


def bucket_of(category: str) -> Bucket:
    return BY_NAME[category].bucket
