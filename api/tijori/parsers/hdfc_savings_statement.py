"""HDFC savings account statement, read from `pdftotext -layout` text.

Row shape: `DD/MM/YY <narration> <ref> DD/MM/YY <amount> <closing balance>`. Withdrawal and
deposit share one printed column position after extraction, so the sign comes from the
balance delta, seeded by the STATEMENT SUMMARY opening balance.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from tijori.money import parse_amount
from tijori.parsers.base import (
    Direction,
    Message,
    Observation,
    ParseError,
    Statement,
    StatementSummary,
)
from tijori.parsers.layout import mask_of, parse_date

_AMT = r"-?[\d,]+\.\d\d"
_ROW = re.compile(
    rf"^\s*(?P<date>\d\d/\d\d/\d\d)\s+(?P<narr>.*?)\s{{2,}}(?P<ref>\S{{6,}})\s+"
    rf"(?P<value>\d\d/\d\d/\d\d)\s+(?P<amt>{_AMT})\s+(?P<bal>{_AMT})\s*$"
)
_PERIOD = re.compile(r"Statement From\s*:\s*(\d\d/\d\d/\d{4})\s+To\s*:\s*(\d\d/\d\d/\d{4})", re.I)
_ACCOUNT = re.compile(r"Account No\s*:\s*(\d{6,})")
_COLUMN_HEADER = re.compile(r"^\s*Date\s+Narration\s+Chq")
_SUMMARY_VALUES = re.compile(
    rf"^\s*(?P<open>{_AMT})\s+(?P<dr_n>\d+)\s+(?P<cr_n>\d+)\s+(?P<dr>{_AMT})\s+"
    rf"(?P<cr>{_AMT})\s+(?P<close>{_AMT})\s*$"
)
_PAGE_FOOTER = "HDFC BANK LIMITED"
# HDFC hard-wraps narration near 40 chars in a proportional font; only a short first line
# is a word wrap (the next token did not fit), everything else was cut mid-token.
_FIRST_LINE_WORD_WRAP = 32


@dataclass(slots=True)
class _Row:
    match: re.Match[str]
    parts: list[str] = field(default_factory=list)


def _join(parts: list[str]) -> str:
    parts = [re.sub(r"\s+", " ", p.strip()) for p in parts if p.strip()]
    if not parts:
        return ""
    out = parts[0]
    for i, nxt in enumerate(parts[1:]):
        prev = parts[i]
        word_wrap = (
            i == 0 and len(prev) < _FIRST_LINE_WORD_WRAP and prev[-1].isalnum() and nxt[0].isalnum()
        )
        out += (" " if word_wrap else "") + nxt
    return out


class HdfcSavingsStatementParser:
    name = "hdfc_savings_statement"
    version = "1.0.0"

    def match(self, msg: Message) -> bool:
        t = msg.text
        return (
            _PAGE_FOOTER in t
            and "STATEMENT SUMMARY" in t
            and "Withdrawal Amt." in t
            and "Deposit Amt." in t
        )

    def parse(self, msg: Message) -> list[Observation]:
        return list(self.parse_statement(msg).lines)

    def parse_statement(self, msg: Message) -> Statement:
        lines = msg.text.splitlines()
        summary_idx = next((i for i, l in enumerate(lines) if "STATEMENT SUMMARY" in l), None)
        period = next((m for l in lines if (m := _PERIOD.search(l))), None)
        if summary_idx is None or period is None:
            raise ParseError("HDFC statement: period header or STATEMENT SUMMARY not found")
        summary = self._summary(lines[summary_idx:])
        rows = self._rows(lines[:summary_idx])
        account = _ACCOUNT.search(msg.text)
        return Statement(
            institution="HDFC",
            account_mask=mask_of(account.group(1)) if account else None,
            period_start=parse_date(period.group(1), "%d/%m/%Y"),
            period_end=parse_date(period.group(2), "%d/%m/%Y"),
            summary=summary,
            lines=tuple(self._observations(rows, summary.opening)),
            parser=self.name,
            parser_version=self.version,
        )

    @staticmethod
    def _rows(body: list[str]) -> list[_Row]:
        rows: list[_Row] = []
        cur: _Row | None = None
        in_body = in_footer = False
        for raw in body:
            if raw.strip().startswith(_PAGE_FOOTER):
                in_footer = True
                continue
            if _PERIOD.search(raw):
                # Each page repeats the address block and ends it with the period line.
                in_body, in_footer = True, False
                continue
            if in_footer or not in_body or _COLUMN_HEADER.match(raw):
                continue
            m = _ROW.match(raw)
            if m:
                cur = _Row(match=m)
                rows.append(cur)
            elif cur is not None and raw.strip():
                cur.parts.append(raw)
        return rows

    @staticmethod
    def _observations(rows: list[_Row], opening: Decimal) -> list[Observation]:
        out: list[Observation] = []
        prev = opening
        for row in rows:
            m = row.match
            amount = parse_amount(m.group("amt"))
            balance = parse_amount(m.group("bal"))
            delta = balance - prev
            direction: Direction
            confidence = 1.0
            payload: dict[str, object] = {}
            if delta == -amount:
                direction = "debit"
            elif delta == amount:
                direction = "credit"
            else:
                # Chain break: keep the row, flag it; reconciliation reports the break.
                direction = "debit" if delta < 0 else "credit"
                confidence = 0.5
                payload["sign_unverified"] = True
            out.append(
                Observation(
                    occurred_at=parse_date(m.group("date"), "%d/%m/%y"),
                    value_date=parse_date(m.group("value"), "%d/%m/%y"),
                    amount=amount,
                    direction=direction,
                    narration=_join([m.group("narr"), *row.parts]),
                    balance_after=balance,
                    ref_no=m.group("ref").lstrip("0") or None,
                    confidence=confidence,
                    payload=payload,
                )
            )
            prev = balance
        return out

    @staticmethod
    def _summary(tail: list[str]) -> StatementSummary:
        for raw in tail:
            m = _SUMMARY_VALUES.match(raw)
            if m:
                return StatementSummary(
                    opening=parse_amount(m.group("open")),
                    closing=parse_amount(m.group("close")),
                    debit_count=int(m.group("dr_n")),
                    credit_count=int(m.group("cr_n")),
                    total_debits=parse_amount(m.group("dr")),
                    total_credits=parse_amount(m.group("cr")),
                )
        raise ParseError("HDFC statement: STATEMENT SUMMARY values line not found")
