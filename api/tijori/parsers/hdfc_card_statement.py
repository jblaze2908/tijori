"""HDFC Bank credit card statement (the emailed PDF), from `pdftotext -layout` text.

Rows: `DD/MM/YYYY| HH:MM  [EMI]  DESCRIPTION  [+ points]  [USD 10.80]  [+] C 1,234.56  l`, where
"C" is how the rupee glyph extracts and a leading "+" marks a credit. A long description wraps onto
the lines around the row (payments and GST lines), so those are joined back.
"""

import re
from datetime import datetime
from decimal import Decimal

from tijori.money import parse_amount
from tijori.parsers.base import Message, Observation, ParseError, Statement
from tijori.parsers.card_common import card_summary
from tijori.parsers.layout import mask_of

_R = r"[C₹`]"
_AMT = r"[\d,]+\.\d\d"
_ROW = re.compile(
    rf"^\s*(?P<date>\d\d/\d\d/\d{{4}})\s*\|\s*(?P<time>\d\d:\d\d)\s+(?:EMI\s+)?(?P<desc>.*?)\s*"
    rf"(?:[+-]\s*\d+\s+)?(?:[A-Z]{{3}}\s+{_AMT}\s+)?(?P<cr>\+\s*)?{_R}\s*(?P<amt>{_AMT})\s+l?\s*$"
)
_SUMMARY = re.compile(rf"{_R}(?P<prev>{_AMT})\s+_\s+{_R}(?P<cr>{_AMT})\s+\+\s+{_R}(?P<dr>{_AMT})\s+\+\s+"
                      rf"{_R}(?P<fin>{_AMT})\s+=\s+{_R}(?P<due>{_AMT})")
_DATE = r"\d{1,2} [A-Z][a-z]{2}, \d{4}"
_PERIOD = re.compile(rf"Billing Period\s+({_DATE})\s*-\s*({_DATE})")
_DUE = re.compile(rf"DUE DATE.*?{_R}{_AMT}\s+({_DATE})", re.S)
_CARD = re.compile(r"Credit Card No\.\s+(\d{6}X+\d{4})")
_PRODUCT = re.compile(r"^\s*(\w[\w ]*?) Credit Card Statement", re.M)
_NOISE = re.compile(r"CKYC ID|DATE & TIME|TRANSACTION DESCRIPTION|Domestic Transactions|International Transactions|"
                    r"^\s*Page \d|Offers on your card|HSN Code|Credit Card Statement")


def _d(text: str) -> datetime:
    return datetime.strptime(text, "%d %b, %Y")


class HdfcCardStatementParser:
    name = "hdfc_card_statement"
    version = "1.0.0"

    def match(self, msg: Message) -> bool:
        t = msg.text
        return "HDFC Bank Credit Cards" in t and "Billing Period" in t and "TOTAL AMOUNT DUE" in t

    def parse(self, msg: Message) -> list[Observation]:
        return list(self.parse_statement(msg).lines)

    def parse_statement(self, msg: Message) -> Statement:
        t = msg.text
        period, summary, card = _PERIOD.search(t), _SUMMARY.search(t), _CARD.search(t)
        if not (period and summary and card):
            raise ParseError("HDFC card statement: period, summary or card number not found")
        rows = t.splitlines()
        lines: list[Observation] = []
        for i, line in enumerate(rows):
            m = _ROW.match(line)
            if not m:
                continue
            desc = re.sub(r"\s+", " ", m["desc"]).strip()
            if not desc:  # wrapped: the text sits on the lines just above and below the row
                above = next((r.strip() for r in reversed(rows[max(0, i - 3):i])
                              if r.strip() and not _ROW.match(r) and not _NOISE.search(r)), "")
                below = rows[i + 1].strip() if i + 1 < len(rows) and not _ROW.match(rows[i + 1]) else ""
                desc = re.sub(r"\s+", " ", f"{above} {below if below.endswith(')') else ''}").strip()
            ts = datetime.strptime(f"{m['date']} {m['time']}", "%d/%m/%Y %H:%M")
            lines.append(Observation(occurred_at=ts.date(), amount=parse_amount(m["amt"]),
                                     direction="credit" if m["cr"] else "debit", narration=desc,
                                     payload={"time": m["time"]}))
        if not lines:
            raise ParseError("HDFC card statement: no transaction rows found")
        prev, due = parse_amount(summary["prev"]), parse_amount(summary["due"])
        exact_due = prev - parse_amount(summary["cr"]) + parse_amount(summary["dr"]) + parse_amount(summary["fin"])
        s = card_summary(prev, exact_due, lines)
        # The printed debit/credit totals are the check; finance charges are rows of their own.
        s = type(s)(opening=s.opening, closing=s.closing, debit_count=s.debit_count, credit_count=s.credit_count,
                    total_debits=parse_amount(summary["dr"]) + parse_amount(summary["fin"]),
                    total_credits=parse_amount(summary["cr"]))
        dd = _DUE.search(t)
        product = _PRODUCT.search(t)
        return Statement(
            institution="HDFC", account_mask=mask_of(card[1]), period_start=_d(period[1]).date(),
            period_end=_d(period[2]).date(), summary=s, lines=tuple(lines), parser=self.name,
            parser_version=self.version, account_kind="card", total_due=due,
            due_date=_d(dd[1]).date() if dd else None,
            account_name=f"HDFC {product[1].strip()}" if product else "HDFC credit card",
        )
