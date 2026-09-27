"""CDSL consolidated account statement (monthly e-CAS), from `pdftotext -layout` text.

Two holding tables: demat balances (`ISIN [security] BAL -- -- -- FREE PRICE VALUE`) and mutual fund
folios held outside demat (`… ISIN FOLIO UNITS NAV COST VALUE …`). Units and prices are as of the
statement's "as on DD-MM-YYYY". Equity ISINs (INE…) and ETFs held in demat add up to the `stocks`
component (as brokers show them), other fund ISINs (INF…) to `mf`. The holdings must add up to the
statement's Total Portfolio Value, so a row the patterns miss fails the parse instead of vanishing.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from tijori.parsers.base import Message, ParseError
from tijori.parsers.cams_statement import HoldingLine

_N = r"[\d,]+\.\d+"
# Current, frozen, pledged, pledge-setup and free balances, then price and value. A locked-in holding has no free
# balance ("--"); its current balance is what's held.
_DEMAT = re.compile(rf"^\s*(?P<isin>IN[EF][0-9A-Z]{{9}})\b(?P<name>.*?)\s(?P<bal>{_N})\s+(?:(?:{_N}|--)\s+){{3}}(?P<free>{_N}|--)\s+"
                    rf"(?P<price>{_N})\s+(?P<value>[\d,]+\.\d\d)\s*$", re.M)
# A long folio number wraps at a soft hyphen ("477306687\xad" / "416/0"), and the plan column can sit before the units.
_FOLIO = re.compile(rf"^(?P<name>.*?)\s*(?P<isin>INF[0-9A-Z]{{9}})\s+(?P<folio>\S+)\s+(?:(?:DIRECT|REGULAR|ARN-\S*)\s+)?"
                    rf"(?P<units>{_N})\s+(?P<nav>{_N})\s+(?P<cost>[\d,]+\.\d\d)\s+(?P<value>[\d,]+\.\d\d)", re.M)
_NUMERIC = re.compile(r"IN[EF][0-9A-Z]{9}|\d+\.\d{3}|\bISIN\b|Security")
_AS_OF = re.compile(r"Total Portfolio Value.*?as on (\d\d-\d\d-\d{4})")
_TOTAL = re.compile(r"Total Portfolio Value\s+([\d,]+\.\d\d)\s*$", re.M)
_ETF = re.compile(r"\bETF\b|BEES", re.I)


_CONT = re.compile(r"^(SUBDIVISION|SHARES|EQUITY SHARES|EACH|OF RS|OF RE)", re.I)  # the previous row's tail


def _clean(line: str) -> str:
    return re.split(r"\s*#|\s+-\s+", line.strip())[0].strip()


def _name_above(rows: list[str], i: int) -> str:
    """The name printed above a demat row; a long one wraps onto two lines ("TATA MOTORS PASSENGER" /
    "VEHICLES LIMITED"), so a bare suffix line pulls in the one before it, unless that is the previous
    row's tail."""
    above = rows[i - 1].strip() if i else ""
    if not above or _NUMERIC.search(above):
        return ""
    name = _clean(above)
    if len(name.split()) <= 2 and i >= 2:
        prev = rows[i - 2].strip()
        if prev and not _NUMERIC.search(prev) and not _CONT.match(prev):
            name = f"{_clean(prev)} {name}".strip()
    return name


def _d(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


@dataclass(frozen=True, slots=True)
class Cas:
    as_of: date
    lines: list[HoldingLine]
    components: tuple[tuple[str, Decimal], ...]


class CdslCasParser:
    name = "cdsl_cas"
    version = "1.1.0"

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
        rows = t.splitlines()
        for i, line in enumerate(rows):
            r = _DEMAT.match(line)
            if not r:
                continue
            # The security's name sits on the line above its row ("AXIS BANK LIMITED # NEW"), cut at "#" or " - ".
            company = _name_above(rows, i)
            name = company or re.sub(r"\s+", " ", r["name"]).strip(" #") or r["isin"]
            units = _d(r["bal"] if r["free"] == "--" else r["free"])
            lines.append(HoldingLine(r["isin"], name[:160], units, _d(r["price"]), as_of))
            # A fund's scheme name wraps below its row ("INDIA ETF NIFTY 50 BEES").
            below = rows[i + 1] if i + 1 < len(rows) and not _NUMERIC.search(rows[i + 1]) else ""
            if r["isin"].startswith("INE") or _ETF.search(f"{line} {below}"):
                stocks += _d(r["value"])
            else:
                mf += _d(r["value"])
        for r in _FOLIO.finditer(t.split("Scheme Name", 1)[-1]):
            name = re.sub(r"\s+", " ", r["name"]).strip() or r["isin"]
            folio = r["folio"].replace("\xad", "")
            lines.append(HoldingLine(r["isin"], f"{name} · folio {folio}"[:160], _d(r["units"]), _d(r["nav"]), as_of))
            mf += _d(r["value"])
        if not lines:
            raise ParseError("CDSL CAS: no holdings found")
        if (total := _TOTAL.search(t)) and abs(stocks + mf - _d(total[1])) > 1:
            raise ParseError(f"CDSL CAS: holdings add up to {stocks + mf}, the statement's total is {total[1]}")
        return Cas(as_of, lines, (("stocks", stocks), ("mf", mf)))
