"""SBI savings e-account statement, read from `pdftotext -layout` text.

Row shape: `DD/MM/YYYY DD/MM/YYYY <description> <ref|-> <debit|-> <credit|-> <balance>`.
The transaction-type cell (WDL TFR, DEP TFR, CEMTEX DEP ...) prints on the line just above
its date row; description continuations print below it.
"""

import re
from dataclasses import dataclass, field

from tijori.money import parse_amount
from tijori.parsers.base import (
    Message,
    Observation,
    ParseError,
    Statement,
    StatementSummary,
)
from tijori.parsers.layout import join_wrapped, mask_of, parse_date

_AMT = r"[\d,]+\.\d\d"
_ROW = re.compile(
    rf"^\s*(?P<date>\d\d/\d\d/\d{{4}})\s+(?P<value>\d\d/\d\d/\d{{4}})\s+(?P<desc>.*?)\s{{2,}}"
    rf"(?P<ref>\S+)\s+(?P<dr>{_AMT}|-)\s+(?P<cr>{_AMT}|-)\s+(?P<bal>{_AMT}(?:CR|DR)?)\s*$"
)
_PERIOD = re.compile(r"Statement From\s*:\s*(\d\d-\d\d-\d{4})\s+to\s+(\d\d-\d\d-\d{4})", re.I)
_ACCOUNT = re.compile(r"Account Number\s*:\s*(\d{6,})")
_SUMMARY_VALUES = re.compile(
    rf"^\s*(?P<open>{_AMT}(?:CR|DR)?)\s+(?P<dr_n>\d+)\s+(?P<cr_n>\d+)\s+(?P<dr>{_AMT})\s+"
    rf"(?P<cr>{_AMT})\s+(?P<close>{_AMT}(?:CR|DR)?)\s*$"
)
# Branch terminal id and branch name follow each row; they identify a location, not the payee.
_TERMINAL = re.compile(r"^\d{10,}\s+AT\s+\d{3,6}$")
_NOISE = re.compile(r"^(?:Page no\.\s*\d+|Balance)$", re.I)
_RRN = re.compile(r"(?:UPI/(?:DR|CR)|IMPS)/(\d{6,})/")
# SBI wraps the description cell at ~27 characters.
_WRAP_WIDTH = 27


@dataclass(slots=True)
class _Row:
    match: re.Match[str]
    txn_type: str | None
    parts: list[str] = field(default_factory=list)
    skip_branch: bool = False


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


class SbiStatementParser:
    name = "sbi_statement"
    version = "1.0.0"

    def match(self, msg: Message) -> bool:
        t = msg.text
        return "State Bank of India" in t and "Brought Forward" in t and "Statement From" in t

    def parse(self, msg: Message) -> list[Observation]:
        return list(self.parse_statement(msg).lines)

    def parse_statement(self, msg: Message) -> Statement:
        lines = msg.text.splitlines()
        period_idx = [i for i, l in enumerate(lines) if _PERIOD.search(l)]
        summary_idx = next((i for i, l in enumerate(lines) if "Statement Summary" in l), None)
        if not period_idx or summary_idx is None:
            raise ParseError("SBI statement: period header or Statement Summary not found")
        start = period_idx[-1]
        period = _PERIOD.search(lines[start])
        assert period is not None
        summary = self._summary(lines[summary_idx:])
        rows = self._rows(lines[start + 1 : summary_idx])
        observations = tuple(self._observation(r) for r in rows)
        account = _ACCOUNT.search(msg.text)
        return Statement(
            institution="SBI",
            account_mask=mask_of(account.group(1)) if account else None,
            period_start=parse_date(period.group(1), "%d-%m-%Y"),
            period_end=parse_date(period.group(2), "%d-%m-%Y"),
            summary=summary,
            lines=observations,
            parser=self.name,
            parser_version=self.version,
        )

    def _rows(self, body: list[str]) -> list[_Row]:
        rows: list[_Row] = []
        pending_type: str | None = None
        cur: _Row | None = None
        for idx, raw in enumerate(body):
            m = _ROW.match(raw)
            if m:
                cur = _Row(match=m, txn_type=pending_type)
                rows.append(cur)
                pending_type = None
                continue
            s = _squash(raw)
            if not s or _NOISE.match(s):
                continue
            nxt = body[idx + 1] if idx + 1 < len(body) else ""
            if _ROW.match(nxt):
                pending_type = s
                continue
            if cur is None:
                continue
            if _TERMINAL.match(s):
                cur.skip_branch = True
                continue
            if cur.skip_branch:
                cur.skip_branch = False
                continue
            cur.parts.append(s)
        return rows

    def _observation(self, row: _Row) -> Observation:
        m = row.match
        dr, cr = m.group("dr"), m.group("cr")
        if (dr == "-") == (cr == "-"):
            raise ParseError(f"SBI row {m.group('date')}: expected exactly one of debit/credit")
        desc = join_wrapped([_squash(m.group("desc")), *row.parts], _WRAP_WIDTH)
        narration = f"{row.txn_type} {desc}" if row.txn_type else desc
        ref = m.group("ref")
        rrn = _RRN.search(desc)
        return Observation(
            occurred_at=parse_date(m.group("date"), "%d/%m/%Y"),
            value_date=parse_date(m.group("value"), "%d/%m/%Y"),
            amount=parse_amount(dr if dr != "-" else cr),
            direction="debit" if dr != "-" else "credit",
            narration=narration,
            balance_after=parse_amount(m.group("bal")),
            ref_no=ref if ref != "-" else (rrn.group(1) if rrn else None),
            payload={"txn_type": row.txn_type},
        )

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
        raise ParseError("SBI statement: Statement Summary values line not found")
