"""ICICI Bank credit card statement (the emailed PDF), from `pdftotext -layout` text.

Rows: `DD/MM/YYYY  SerNo  Details  RewardPoints  [Intl amount]  Amount [CR]`. The spend-mix chart
prints labels ("100%", "Others-100%") at the start of some rows, so rows are searched, not anchored.
Only text before the MITC pages is read: those carry worked examples that look like transactions.
"""

import re
from datetime import datetime

from tijori.money import parse_amount
from tijori.parsers.base import Message, Observation, ParseError, Statement
from tijori.parsers.card_common import card_summary
from tijori.parsers.layout import mask_of

_AMT = r"[\d,]+\.\d\d"
_ROW = re.compile(rf"(?P<date>\d\d/\d\d/\d{{4}})\s+(?P<ser>\d{{9,}})\s+(?P<desc>.+?)\s{{2,}}(?P<pts>-?\d+)\s+"
                  rf"(?:(?P<intl>{_AMT})\s+)?(?P<amt>{_AMT})(?P<cr>\s*CR)?\s*$")
_SUMMARY = re.compile(rf"STATEMENT SUMMARY.*?`(?P<due>{_AMT})\s+`(?P<prev>{_AMT})\s+`(?P<dr>{_AMT})\s+"
                      rf"`(?P<cash>{_AMT})\s+`(?P<cr>{_AMT})", re.S)
_LONG = r"[A-Z][a-z]+ \d{1,2}, \d{4}"
_PERIOD = re.compile(rf"Statement period\s*:\s*({_LONG})\s+to\s+({_LONG})")
_DUE = re.compile(rf"PAYMENT DUE DATE.*?({_LONG})", re.S)
_CARD = re.compile(r"\b(\d{4}X{8}\d{4})\b")


def _d(text: str) -> datetime:
    return datetime.strptime(text, "%B %d, %Y")


class IciciCardStatementParser:
    name = "icici_card_statement"
    version = "1.0.0"

    def match(self, msg: Message) -> bool:
        t = msg.text
        return "ICICI Bank Credit Card" in t and "Statement period" in t and "SPENDS OVERVIEW" in t

    def parse(self, msg: Message) -> list[Observation]:
        return list(self.parse_statement(msg).lines)

    def parse_statement(self, msg: Message) -> Statement:
        t = msg.text.split("MOST IMPORTANT TERMS AND CONDITIONS")[0]
        period, summary, card = _PERIOD.search(t), _SUMMARY.search(t), _CARD.search(t)
        if not (period and summary and card):
            raise ParseError("ICICI card statement: period, summary or card number not found")
        lines = [
            Observation(occurred_at=datetime.strptime(m["date"], "%d/%m/%Y").date(), amount=parse_amount(m["amt"]),
                        direction="credit" if m["cr"] else "debit", narration=re.sub(r"\s+", " ", m["desc"]).strip(),
                        ref_no=m["ser"], payload={"intl_amount": m["intl"]} if m["intl"] else {})
            for line in t.splitlines() if (m := _ROW.search(line))
        ]
        prev, due = parse_amount(summary["prev"]), parse_amount(summary["due"])
        s = card_summary(prev, due, lines)
        s = type(s)(opening=s.opening, closing=s.closing, debit_count=s.debit_count, credit_count=s.credit_count,
                    total_debits=parse_amount(summary["dr"]) + parse_amount(summary["cash"]),
                    total_credits=parse_amount(summary["cr"]))
        dd = _DUE.search(t)
        return Statement(
            institution="ICICI", account_mask=mask_of(card[1]), period_start=_d(period[1]).date(),
            period_end=_d(period[2]).date(), summary=s, lines=tuple(lines), parser=self.name,
            parser_version=self.version, account_kind="card", total_due=due,
            due_date=_d(dd[1]).date() if dd else None,
            account_name="ICICI Amazon Pay" if "Amazon Pay" in msg.text else "ICICI credit card",
        )
