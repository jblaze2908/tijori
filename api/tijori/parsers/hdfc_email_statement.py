"""HDFC Bank "Combined Email Statement" (monthly PDF by mail), from `pdftotext -layout` text.

Rows: `DD/MM/YYYY  NARRATION  WITHDRAWAL  DEPOSIT  CLOSING` with the narration continuing on the
indented lines below, ending in `Value Dt DD/MM/YYYY Ref N`. One statement per savings account; the
SUMMARY block after each account's rows carries the totals and counts.
"""

import re
from datetime import datetime

from tijori.money import parse_amount
from tijori.parsers.base import Message, Observation, ParseError, Statement, StatementSummary
from tijori.parsers.layout import join_wrapped, mask_of

_AMT = r"[\d,]+\.\d\d"
_ROW = re.compile(rf"^\s*(?P<date>\d\d/\d\d/\d{{4}})\s+(?P<narr>.+?)\s{{2,}}(?P<wd>{_AMT})\s+(?P<dep>{_AMT})\s+(?P<bal>{_AMT})\s*$")
_ACCOUNT = re.compile(r"Account Number\s*:\s*(\d{6,})")
_PERIOD = re.compile(r"Statement From\s*:\s*(\d\d/\d\d/\d{4})\s+To\s+(\d\d/\d\d/\d{4})")
_OPENING = re.compile(rf"Opening Balance\s*:\s*({_AMT})")
_SUMMARY = re.compile(rf"Opening Balance\s+Debit Amount\s+Credit Amount\s+Closing Balance\s*\n\s*({_AMT})\s+({_AMT})\s+"
                      rf"({_AMT})\s+({_AMT})\s*\n.*?Debit Count\s+Credit Count\s*\n\s*(\d+)\s+(\d+)", re.S)
_VALUE_REF = re.compile(r"\s*Value Dt\s*(\d\d/\d\d/\d{4})\s*Ref\s*(\S+)\s*$")
_WRAP = 36  # HDFC cuts narration lines near this width; shorter lines ended at a word
_FD = re.compile(r"TERM DEPOSITS\s+([\d,]+\.\d\d)\s+CR")  # Account Relationship Summary
_CONT = re.compile(r"^\s{8,}\S")  # narration continues on an indented line
_STOP = re.compile(r"^\s*(Page \d+ of \d+|SUMMARY|Opening Balance|Statement From|Account Number)")


def _d(s: str):
    return datetime.strptime(s, "%d/%m/%Y").date()


class HdfcEmailStatementParser:
    name = "hdfc_email_statement"
    version = "1.0.0"

    def match(self, msg: Message) -> bool:
        t = msg.text
        return "Savings Account Details" in t and "Debit Count" in t and "Statement From" in t and "Customer ID" in t

    def parse(self, msg: Message) -> list[Observation]:
        return [o for st in self.parse_statements(msg) for o in st.lines]

    def parse_statement(self, msg: Message) -> Statement:
        return self.parse_statements(msg)[0]

    def parse_statements(self, msg: Message) -> list[Statement]:
        text = msg.text
        # Each account's block runs from its first header to its SUMMARY; headers repeat per page.
        out: list[Statement] = []
        for acct in dict.fromkeys(_ACCOUNT.findall(text)):
            start = text.find(acct)
            summ = _SUMMARY.search(text, start)
            period = _PERIOD.search(text, start)
            if not (summ and period):
                raise ParseError("HDFC email statement: summary or period not found")
            block = text[start:summ.start()]
            lines: list[Observation] = []
            cur: dict | None = None
            for raw in block.splitlines():
                m = _ROW.match(raw)
                if m:
                    if cur:
                        lines.append(_obs(cur))
                    cur = {"m": m, "parts": [m["narr"]]}
                elif cur and _CONT.match(raw) and not _STOP.match(raw):
                    cur["parts"].append(raw.strip())
                elif cur and _STOP.match(raw):
                    lines.append(_obs(cur)); cur = None
            if cur:
                lines.append(_obs(cur))
            opening, dr, cr, closing = (parse_amount(summ[i]) for i in range(1, 5))
            out.append(Statement(
                institution="HDFC", account_mask=mask_of(acct), period_start=_d(period[1]), period_end=_d(period[2]),
                summary=StatementSummary(opening=opening, closing=closing, debit_count=int(summ[5]), credit_count=int(summ[6]),
                                         total_debits=dr, total_credits=cr),
                lines=tuple(lines), parser=self.name, parser_version=self.version, account_kind="bank",
                account_name="HDFC savings"))
        if not out:
            raise ParseError("HDFC email statement: no account found")
        fd = _FD.search(text)
        if fd:
            first = out[0]
            out[0] = Statement(**{**{f: getattr(first, f) for f in first.__dataclass_fields__},
                                  "components": (("fd", parse_amount(fd[1])),)})
        return out


def _obs(cur: dict) -> Observation:
    m = cur["m"]
    narr = re.sub(r"\s+", " ", join_wrapped(cur["parts"], _WRAP)).strip()
    value = ref = None
    vr = _VALUE_REF.search(narr)
    if vr:
        value, ref = _d(vr[1]), vr[2]
        narr = narr[:vr.start()].strip()
    wd, dep = parse_amount(m["wd"]), parse_amount(m["dep"])
    return Observation(occurred_at=_d(m["date"]), amount=wd or dep, direction="debit" if wd else "credit",
                       narration=narr, balance_after=parse_amount(m["bal"]), value_date=value, ref_no=ref)
