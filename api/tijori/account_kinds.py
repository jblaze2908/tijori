"""What each kind of account is, for net worth. Every place money sits is an account; its kind gives the defaults
(asset or liability, asset class, whether it counts as liquid) and the member can override any of them per account.
Nothing here names a bank or a person: an account's institution is data the member enters or a statement names."""

from dataclasses import dataclass
from typing import Literal

Side = Literal["asset", "liability"]


@dataclass(frozen=True, slots=True)
class Kind:
    label: str
    side: Side
    asset_class: str
    liquid: bool
    carry_back: bool  # before its first value it held that value: a store of value, not something that grows on a feed
    subtypes: tuple[str, ...] = ()


KINDS: dict[str, Kind] = {
    "bank": Kind("Bank account", "asset", "cash", True, False),
    "wallet": Kind("Wallet", "asset", "cash", True, False),
    "cash": Kind("Cash", "asset", "cash", True, True),
    # Not liquid: liquid is what you could spend today, and a card's outstanding moves with every swipe. Net worth
    # still subtracts it, and `live` reports it on its own (cards_owed) and as its own column month by month.
    "card": Kind("Credit card", "liability", "credit", False, False),
    "deposit": Kind("Deposit", "asset", "deposits", True, False, ("fd", "rd")),
    "holding": Kind("Investments", "asset", "equity", False, False, ("stocks", "mf")),
    "retirement": Kind("Retirement", "asset", "retirement", False, False, ("epf", "ppf", "nps")),
    "gold": Kind("Gold", "asset", "gold", False, True),
    "property": Kind("Property", "asset", "property", False, True),
    "loan": Kind("Loan", "liability", "loans", False, False, ("home", "car", "personal", "education", "gold")),
    "other_asset": Kind("Other asset", "asset", "other", False, True),
    "other_liability": Kind("Other liability", "liability", "other", False, True),
}
ACCOUNT_KINDS = tuple(KINDS)
ASSET_CLASSES = ("cash", "deposits", "equity", "retirement", "gold", "property", "credit", "loans", "other")
SUBTYPE_LABELS = {
    "fd": "Fixed deposit", "rd": "Recurring deposit", "stocks": "Stocks & ETFs", "mf": "Mutual funds",
    "epf": "EPF", "ppf": "PPF", "nps": "NPS", "home": "Home loan", "car": "Car loan", "personal": "Personal loan",
    "education": "Education loan", "gold": "Gold loan",
}
# A value a statement prints for something other than its own account (a PPF balance on a savings statement,
# a demat total on a CAS): the kind and subtype of the account it belongs to.
COMPONENT_ACCOUNTS: dict[str, tuple[str, str]] = {
    "ppf": ("retirement", "ppf"), "fd": ("deposit", "fd"), "stocks": ("holding", "stocks"), "mf": ("holding", "mf"),
}


def side(kind: str) -> Side:
    return KINDS[kind].side


def liquid(kind: str, override: bool | None) -> bool:
    return KINDS[kind].liquid if override is None else override


def asset_class(kind: str, override: str | None) -> str:
    return override or KINDS[kind].asset_class


def subtype_ok(kind: str, subtype: str | None) -> bool:
    return subtype is None or subtype in KINDS[kind].subtypes
