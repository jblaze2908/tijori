"""CAMS mutual fund account statement (per folio, sent after each transaction), `pdftotext -layout`.

Only the Account Summary is read: scheme, NAV date, NAV and units held, with the scheme's ISIN from its
header line ("… - INF879O01027" or "ISIN:INF277K01Z77"). Units are as of the NAV date printed.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from tijori.parsers.base import Message, ParseError

_ROW = re.compile(r"^(?P<scheme>\S.*?)\s{2,}(?P<date>\d\d-[A-Z][a-z]{2}-\d{4})\s+(?P<nav>[\d,]+\.\d+)\s+(?P<units>[\d,]+\.\d+)\s+"
                  r"(?P<cost>[\d,]+\.\d\d)\s+(?P<value>[\d,]+\.\d\d)", re.M)
_ISIN = re.compile(r"\b(INF[0-9A-Z]{9})\b")


@dataclass(frozen=True, slots=True)
class HoldingLine:
    isin: str | None
    name: str
    units: Decimal
    nav: Decimal
    as_of: date


class CamsStatementParser:
    name = "cams_statement"
    version = "1.0.0"

    def match(self, msg: Message) -> bool:
        return "Account Summary" in msg.text and "camsonline" in msg.text.lower() and "Folio" in msg.text

    def parse_holdings(self, msg: Message) -> list[HoldingLine]:
        t = msg.text
        summary = t.split("Account Summary", 1)[1]
        isins = _ISIN.findall(t)
        out = []
        for m in _ROW.finditer(summary.split("Registered Bank", 1)[0]):
            name = re.sub(r"\s+", " ", m["scheme"]).strip()
            # The scheme's header line later in the text carries its ISIN; with one scheme, take the only one.
            isin = next((i for i in dict.fromkeys(isins) if _near(t, i, name)), isins[0] if len(set(isins)) == 1 else None)
            out.append(HoldingLine(isin, name[:160], Decimal(m["units"].replace(",", "")), Decimal(m["nav"].replace(",", "")),
                                   datetime.strptime(m["date"], "%d-%b-%Y").date()))
        if not out:
            raise ParseError("CAMS statement: no scheme in the account summary")
        return out


def _near(text: str, isin: str, name: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z]{4,}", name)[:3]]
    i = text.find(isin)
    line = text[max(0, text.rfind("\n", 0, i)):i].lower()
    return bool(words) and all(w.lower() in line for w in words[:2])
