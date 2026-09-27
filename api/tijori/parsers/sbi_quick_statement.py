"""SBI Quick statement (ESTMT by SMS: up to 6 months by mail), from `pdftotext -layout` text.

Rows: `DD/MM/YYYY  DD/MM/YYYY  <description>  <amount>  <balance>`, the description wrapping onto the
lines below. Debit and credit print in separate columns, but pdftotext drifts a debit toward the credit
column, so the balance chain decides direction; only the first row, with no balance before it, is read
from its column. There are no footer totals: a row the chain can't explain is a ParseError.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from tijori.money import parse_amount
from tijori.parsers.base import Direction, Message, Observation, ParseError, Statement, StatementSummary
from tijori.parsers.layout import join_wrapped, mask_of, parse_date

_AMT = r"[\d,]+\.\d\d"
_ROW = re.compile(rf"^\s*(?P<date>\d\d/\d\d/\d{{4}})\s+(?P<value>\d\d/\d\d/\d{{4}})\s+(?:(?P<desc>.*?)\s+)?"
                  rf"(?P<amt>{_AMT})\s+(?P<bal>-?{_AMT})\s*$")
_HEADER = re.compile(r"Txn Date\s+Value Date\s+Description\s+Debit\s+Credit\s+Balance")
_PERIOD = re.compile(r"\(A/c-(\d{6,})\) between (\d\d-[A-Za-z]{3}-\d{4}) to (\d\d-[A-Za-z]{3}-\d{4})")
_PAGE_END = re.compile(r"computer generated statement|End of Statement", re.I)
_RRN = re.compile(r"(?:UPI/(?:DR|CR)|IMPS)/(\d{6,})/")
# The cell wraps by rendered width; a first line this long was cut mid-token.
_WRAP_WIDTH = 35


@dataclass(slots=True)
class _Row:
    match: re.Match[str]
    header: str
    parts: list[str] = field(default_factory=list)


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def _by_column(row: _Row) -> Direction:
    """Credits sit next to the balance; debits leave the credit column's width empty before it."""
    h = row.header
    credit_gap = h.index("Balance") - h.index("Credit")
    return "credit" if row.match.start("bal") - row.match.end("amt") < credit_gap else "debit"


class SbiQuickStatementParser:
    name = "sbi_quick_statement"
    version = "1.0.0"

    def match(self, msg: Message) -> bool:
        t = msg.text
        return "SBI Quick" in t and "Txn Date" in t and _PERIOD.search(t) is not None

    def parse(self, msg: Message) -> list[Observation]:
        return list(self.parse_statement(msg).lines)

    def parse_statement(self, msg: Message) -> Statement:
        period = _PERIOD.search(msg.text)
        assert period is not None
        rows = self._rows(msg.text.splitlines())
        if not rows:
            raise ParseError("SBI Quick statement: no transaction rows")
        lines: list[Observation] = []
        prev: Decimal | None = None
        for r in rows:
            amt, bal = parse_amount(r.match["amt"]), parse_amount(r.match["bal"])
            if prev is None:
                direction = _by_column(r)
            elif prev - amt == bal:
                direction = "debit"
            elif prev + amt == bal:
                direction = "credit"
            else:
                raise ParseError(f"SBI Quick row {r.match['date']}: balance chain broken")
            prev = bal
            desc = join_wrapped([_squash(r.match["desc"] or ""), *r.parts], _WRAP_WIDTH)
            rrn = _RRN.search(desc)
            lines.append(Observation(
                occurred_at=parse_date(r.match["date"], "%d/%m/%Y"), value_date=parse_date(r.match["value"], "%d/%m/%Y"),
                amount=amt, direction=direction, narration=desc, balance_after=bal, ref_no=rrn[1] if rrn else None))
        first = lines[0]
        opening = first.balance_after + (first.amount if first.direction == "debit" else -first.amount)  # type: ignore[operator]
        debits = [o.amount for o in lines if o.direction == "debit"]
        credits = [o.amount for o in lines if o.direction == "credit"]
        return Statement(
            institution="SBI", account_mask=mask_of(period[1]),
            period_start=parse_date(period[2], "%d-%b-%Y"), period_end=parse_date(period[3], "%d-%b-%Y"),
            summary=StatementSummary(opening=opening, closing=lines[-1].balance_after,  # type: ignore[arg-type]
                                     debit_count=len(debits), credit_count=len(credits),
                                     total_debits=sum(debits, Decimal(0)), total_credits=sum(credits, Decimal(0))),
            lines=tuple(lines), parser=self.name, parser_version=self.version, account_name="SBI savings")

    @staticmethod
    def _rows(text_lines: list[str]) -> list[_Row]:
        rows: list[_Row] = []
        header: str | None = None
        cur: _Row | None = None
        for raw in text_lines:
            if _HEADER.search(raw):
                header, cur = raw, None
                continue
            if header is None:
                continue  # name and address block above the first table
            m = _ROW.match(raw)
            if m:
                cur = _Row(m, header)
                rows.append(cur)
                continue
            s = _squash(raw)
            if _PAGE_END.search(s) or _PERIOD.search(s):
                cur = None
            elif s and cur is not None:
                cur.parts.append(s)
        return rows
