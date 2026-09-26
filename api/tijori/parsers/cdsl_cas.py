"""CDSL consolidated account statement (monthly e-CAS), from `pdftotext -layout` text.

Two holding tables: demat balances (`ISIN [security] BAL -- -- -- FREE PRICE VALUE`) and mutual fund
folios held outside demat (`… ISIN FOLIO UNITS NAV COST VALUE …`). Units and prices are as of the
statement's "as on DD-MM-YYYY". Equity ISINs (INE…) add up to the `stocks` component, fund ISINs
(INF…) to `mf`.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from tijori.parsers.base import Message, ParseError
from tijori.parsers.cams_statement import HoldingLine

_N = r"[\d,]+\.\d+"
_DEMAT = re.compile(rf"^\s*(?P<isin>IN[EF][0-9A-Z]{{9}})\b(?P<name>.*?)\s(?P<bal>{_N})\s+--\s+--\s+--\s+(?P<free>{_N})\s+"
                    rf"(?P<price>{_N})\s+(?P<value>[\d,]+\.\d\d)\s*$", re.M)
_FOLIO = re.compile(rf"^(?P<name>.*?)\s*(?P<isin>INF[0-9A-Z]{{9}})\s+(?P<folio>\S+)\s+(?P<units>{_N})\s+(?P<nav>{_N})\s+"
                    rf"(?P<cost>[\d,]+\.\d\d)\s+(?P<value>[\d,]+\.\d\d)", re.M)
_AS_OF = re.compile(r"Total Portfolio Value.*?as on (\d\d-\d\d-\d{4})")


def _d(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


@dataclass(frozen=True, slots=True)
class Cas:
    as_of: date
    lines: list[HoldingLine]
    components: tuple[tuple[str, Decimal], ...]


class CdslCasParser:
    name = "cdsl_cas"
    version = "1.0.0"

    def match(self, msg: Message) -> bool:
        return "CDSL" in msg.text and "Total Portfolio Value" in msg.text and "ISIN" in msg.text

    def parse(self, msg: Message) -> Cas:
        t = msg.text
        m = _AS_OF.search(t)
        if not m:
            raise ParseError("CDSL CAS: as-on date not found")
        as_of = datetime.strptime(m[1], "%d-%m-%Y").date()
        lines: list[HoldingLine] = []
        stocks = mf = Decimal(0)
        for r in _DEMAT.finditer(t):
            name = re.sub(r"\s+", " ", r["name"]).strip(" #") or r["isin"]
            lines.append(HoldingLine(r["isin"], name[:160], _d(r["free"]), _d(r["price"]), as_of))
            if r["isin"].startswith("INE"):
                stocks += _d(r["value"])
            else:
                mf += _d(r["value"])
        for r in _FOLIO.finditer(t.split("Scheme Name", 1)[-1]):
            name = re.sub(r"\s+", " ", r["name"]).strip() or r["isin"]
            lines.append(HoldingLine(r["isin"], f"{name} · folio {r['folio']}"[:160], _d(r["units"]), _d(r["nav"]), as_of))
            mf += _d(r["value"])
        if not lines:
            raise ParseError("CDSL CAS: no holdings found")
        return Cas(as_of, lines, (("stocks", stocks), ("mf", mf)))
