"""SBI e-account statement (monthly PDF by mail), from `pdftotext -layout` text.

One statement per savings account where you are the primary holder (secondary-holder accounts are
someone else's spend). Rows: `DD-MM-YY  REFERENCE  REF/CHQ  CREDIT  DEBIT  BALANCE` with "-" for an
empty cell, between "Balance on DD-MM-YY: X" and "Your Closing Balance on DD-MM-YY: X".
"""

import re
from datetime import datetime

from tijori.money import parse_amount
from tijori.parsers.base import Message, Observation, ParseError, Statement, StatementSummary

_N = r"(?:[\d,]+\.\d\d|-|0)"  # older layouts print "-" for an empty cell, newer ones "0"
_ROW = re.compile(rf"^\s*(?P<date>\d\d-\d\d-\d\d)\s+(?P<ref>.+?)\s{{2,}}(?P<chq>\S+)\s+(?P<cr>{_N})\s+(?P<dr>{_N})\s+(?P<bal>[\d,]+\.\d\d)\s*$")
_PRIMARY = re.compile(r"^\s*P\s+(?:[A-Z ]+?\s+)?X+(\d{4})\s+OPEN", re.M)  # newer layouts add "SINGLE"
_SECTION = re.compile(r"^\s*X+(\d{4})\s*$", re.M)
_OPEN = re.compile(r"Balance on (\d\d-\d\d-\d\d):\s+([\d,]+\.\d\d)")
_UPI_REF = re.compile(r"UPI/(?:DR|CR)/(\d{9,})/")  # the netbanking parser keys UPI lines by this
_PPF = re.compile(r"^\s*PPF\s+X+\d{4}\s+\d\d-\d\d-\d\d\s+([\d,]+\.\d\d)", re.M)
_CLOSE = re.compile(r"Your Closing Balance on (\d\d-\d\d-\d\d):\s+([\d,]+\.\d\d)")


def _d(s: str):
    return datetime.strptime(s, "%d-%m-%y").date()


class SbiEstatementParser:
    name = "sbi_estatement"
    version = "1.0.0"

    def match(self, msg: Message) -> bool:
        t = msg.text
        return "sbi.co.in" in t and "TRANSACTION OVERVIEW" in t and "Your Closing Balance on" in t

    def parse(self, msg: Message) -> list[Observation]:
        return [o for st in self.parse_statements(msg) for o in st.lines]

    def parse_statement(self, msg: Message) -> Statement:
        return self.parse_statements(msg)[0]

    def parse_statements(self, msg: Message) -> list[Statement]:
        t = msg.text
        primary = set(_PRIMARY.findall(t))
        out: list[Statement] = []
        for sec in _SECTION.finditer(t):
            mask = sec[1]
            if mask not in primary:
                continue
            nxt = _SECTION.search(t, sec.end())
            end = nxt.start() if nxt else len(t)
            op = _OPEN.search(t, sec.end(), end); cl = _CLOSE.search(t, sec.end(), end)
            if not (op and cl):
                continue  # no transactions this month: SBI prints no balance lines for the account
            lines: list[Observation] = []
            for raw in t[op.end():cl.start()].splitlines():
                m = _ROW.match(raw)
                if not m:
                    continue
                cr = parse_amount(m["cr"]) if m["cr"] not in ("-", "0") else None
                dr = parse_amount(m["dr"]) if m["dr"] not in ("-", "0") else None
                narr = re.sub(r"\s+", " ", m["ref"]).strip()
                upi = _UPI_REF.search(narr)
                lines.append(Observation(occurred_at=_d(m["date"]), amount=dr or cr, direction="debit" if dr else "credit",
                                         narration=narr, balance_after=parse_amount(m["bal"]),
                                         ref_no=m["chq"] if m["chq"] != "-" else (upi[1] if upi else None)))
            debits = [o.amount for o in lines if o.direction == "debit"]
            credits = [o.amount for o in lines if o.direction == "credit"]
            # SBI prints no footer totals; the balance chain from opening to closing is the check.
            out.append(Statement(
                institution="SBI", account_mask=mask, period_start=_d(op[1]), period_end=_d(cl[1]),
                summary=StatementSummary(opening=parse_amount(op[2]), closing=parse_amount(cl[2]), debit_count=len(debits),
                                         credit_count=len(credits), total_debits=sum(debits), total_credits=sum(credits)),
                lines=tuple(lines), parser=self.name, parser_version=self.version, account_kind="bank",
                account_name="SBI savings"))
        if not out:
            raise ParseError("SBI e-statement: no primary savings account found")
        ppf = _PPF.search(t)
        if ppf:
            first = out[0]
            out[0] = Statement(**{**{f: getattr(first, f) for f in first.__dataclass_fields__},
                                  "components": (("ppf", parse_amount(ppf[1])),)})
        return out
