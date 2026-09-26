"""Readers for the 2026-09-26 prototype outputs: labelled txns (data.json) and the net-worth sheet."""

import csv
import io
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from tijori.money import parse_amount
from tijori.parsers import Message, Observation, Statement, route
from tijori.parsers.layout import parse_date

# Prototype label → taxonomy category. Keys are the prototype's category strings.
LEGACY_CATEGORY_MAP: dict[str, str] = {
    "Groceries (Blinkit/JioMart)": "Groceries",
    "Local UPI (shops/people)": "Local shops",
    "Eating out/delivery": "Eating out",
    "Invest: Groww SIP/stocks": "Investments",
    "Invest: PPF": "Investments",
    "Bills & subscriptions": "Bills & subscriptions",
    "Self transfer": "Self transfer",
    "Credit card bills (CRED)": "Card bill payment",
    "Shopping": "Shopping",
    "Dividends": "Dividends",
    "From friends/others": "Other income",
    "Salary": "Salary",
    "Bank charges": "Bank charges",
    "Refunds": "Refunds",
    "Travel": "Travel",
    "Pass-through (jewellery)": "Pass-through",
    "Interest": "Interest",
    "Insurance": "Insurance",
    "Reversal": "Reversals",
    "To family": "Family",
    "FD maturity": "Investment redemptions",
    "Groww credit": "Investment redemptions",
    "Services (CSC)": "Services",
    "Cash withdrawal": "Cash",
    "Income tax": "Tax",
    "Health": "Health",
}
# The prototype suffixed one label with a person's name; match on the prefix instead.
LEGACY_PREFIX_MAP: dict[str, str] = {"From family": "Other income"}

SHEET_COMPONENTS: dict[str, str] = {
    "SBI": "sbi",
    "HDFC Bank": "hdfc",
    "HDFC FD": "fd",
    "Stocks": "stocks",
    "Mutual Funds": "mf",
    "PPF": "ppf",
    "EPF": "epf",
    "Gold": "gold",
    "Other": "other",
}


def map_legacy_category(label: str) -> str:
    if label in LEGACY_CATEGORY_MAP:
        return LEGACY_CATEGORY_MAP[label]
    for prefix, category in LEGACY_PREFIX_MAP.items():
        if label.startswith(prefix):
            return category
    raise ValueError(f"no taxonomy mapping for legacy category {label!r}")


@dataclass(frozen=True, slots=True)
class LegacyTxn:
    occurred_at: date
    bank: str
    who: str
    amount: Decimal
    direction: str  # debit | credit
    label: str  # prototype category string
    bucket: str

    @property
    def category(self) -> str:
        return map_legacy_category(self.label)


@dataclass(frozen=True, slots=True)
class SheetRow:
    month: date
    components: dict[str, Decimal]
    net_worth: Decimal
    liquid: Decimal | None
    remark: str | None
    commentary: str | None


def load_labels(path: Path) -> list[LegacyTxn]:
    data = json.loads(path.read_text())
    out = []
    for t in data["txns"]:
        out.append(
            LegacyTxn(
                occurred_at=date.fromisoformat(t["date"]),
                bank=t["bank"],
                who=t["who"],
                amount=parse_amount(str(t["amt"])),
                direction={"out": "debit", "in": "credit"}[t["dir"]],
                label=t["cat"],
                bucket=t["bucket"],
            )
        )
    return out


def _money_or_none(cell: str) -> Decimal | None:
    cell = cell.strip()
    return None if cell in ("", "-") else parse_amount(cell)


def load_sheet(path: Path) -> list[SheetRow]:
    return load_sheet_text(path.read_text(encoding="utf-8-sig"))


def load_sheet_text(text: str) -> list[SheetRow]:
    """Parse the exported Google Sheet CSV. A currency banner row may precede the header."""
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header_idx = next(i for i, r in enumerate(rows) if r and r[0].strip() == "Month")
    header = [h.strip() for h in rows[header_idx]]
    col = {name: i for i, name in enumerate(header) if name}
    out = []
    for r in rows[header_idx + 1 :]:
        if not r or not r[0].strip():
            continue
        cells = r + [""] * (len(header) - len(r))
        nw = _money_or_none(cells[col["Net Worth"]])
        if nw is None:
            continue  # future months are pre-filled with blank rows
        components = {
            key: v for label, key in SHEET_COMPONENTS.items()
            if label in col and (v := _money_or_none(cells[col[label]])) is not None
        }
        out.append(
            SheetRow(
                month=parse_date(cells[col["Month"]].strip(), "%d/%m/%Y"),
                components=components,
                net_worth=nw,
                liquid=_money_or_none(cells[col["Liquid Cash"]]) if "Liquid Cash" in col else None,
                remark=(cells[col["Remarks"]].strip() or None) if "Remarks" in col else None,
                commentary=(cells[col["My Understanding"]].strip() or None) if "My Understanding" in col else None,
            )
        )
    return out


def load_statement(path: Path) -> Statement:
    msg = Message(text=path.read_text(), filename=path.name)
    parser = route(msg)
    if parser is None or not hasattr(parser, "parse_statement"):
        raise ValueError(f"{path.name}: no statement parser matches")
    return parser.parse_statement(msg)  # type: ignore[attr-defined]


def join_statement_lines(
    labels: Iterable[LegacyTxn], statements: Iterable[Statement]
) -> list[tuple[LegacyTxn, Observation | None]]:
    """Attach the raw statement line to each label by (bank, date, amount, direction), in order.

    The prototype labels carry no narration; the statement lines do.
    """
    pool: dict[tuple[str, date, Decimal, str], list[Observation]] = defaultdict(list)
    for st in statements:
        for o in st.lines:
            pool[(st.institution, o.occurred_at, o.amount, o.direction)].append(o)
    out = []
    for t in labels:
        bucket = pool.get((t.bank, t.occurred_at, t.amount, t.direction))
        out.append((t, bucket.pop(0) if bucket else None))
    return out
