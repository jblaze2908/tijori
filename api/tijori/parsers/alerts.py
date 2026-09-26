"""Transaction alert emails (PLAN §7.2): one sighting each, matched later to statement lines.

Each rule is one sentence shape a bank sends. Only INR amounts are taken: a USD card alert is left
to the statement, which prints the rupee charge. Alerts carry the account's last 4 digits in the
payload (`institution`, `account_kind`, `mask`) so the resolver can find or create the account.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

from tijori.money import parse_amount
from tijori.parsers.base import Direction, Message, Observation

_AMT = r"(?:Rs\.?|INR|₹)\s*(?:INR\s*)?(?P<amt>[\d,]+(?:\.\d{1,2})?)"


@dataclass(frozen=True, slots=True)
class _Rule:
    name: str
    senders: tuple[str, ...]
    pattern: re.Pattern[str]
    institution: str
    kind: str  # bank | card
    direction: Direction
    date_fmt: str
    narration: Callable[[re.Match[str]], str]


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip(" .")


_HDFC = ("alerts@hdfcbank.bank.in", "alerts@hdfcbank.net")
_SBI = ("cbsalerts.sbi@alerts.sbi.co.in", "cbsalerts.sbi@alerts.sbi.bank.in")
_ICICI = ("credit_cards@icicibank.com", "credit_cards@icici.bank.in")

RULES: tuple[_Rule, ...] = (
    _Rule("hdfc_upi_debit", _HDFC, re.compile(
        rf"{_AMT} (?:is|has been) debited from (?:your )?account (?:ending )?(?P<mask>\d{{4}}) (?:towards|to) VPA "
        r"(?P<vpa>\S+?)\s*(?:\((?P<name>[^)]*)\)|(?P<name2>[A-Z][A-Z .]+?))? on (?P<date>\d\d-\d\d-\d\d)\b.*?"
        r"reference (?:no\.|number)(?: is)?:?\s*(?P<ref>\d+)", re.S),
        "HDFC", "bank", "debit", "%d-%m-%y",
        lambda m: f"UPI-{_clean(m['name'] or m['name2'] or '')}-{m['vpa']}-{m['ref']}"),
    _Rule("hdfc_upi_credit", _HDFC, re.compile(
        rf"{_AMT} (?:is|has been) credited to (?:your )?account (?:ending )?(?:XX)?(?P<mask>\d{{4}}) (?:from|by) VPA "
        r"(?P<vpa>\S+?)\s*(?:\((?P<name>[^)]*)\)|(?P<name2>[A-Z][A-Z .]+?))? on (?P<date>\d\d-\d\d-\d\d)\b.*?"
        r"reference (?:no\.|number)(?: is)?:?\s*(?P<ref>\d+)", re.S),
        "HDFC", "bank", "credit", "%d-%m-%y",
        lambda m: f"UPI-{_clean(m['name'] or m['name2'] or '')}-{m['vpa']}-{m['ref']}"),
    _Rule("hdfc_account_debit", _HDFC, re.compile(
        rf"{_AMT} is deducted from your account ending XX(?P<mask>\d{{4}}) and added to (?P<to>.+?) account on "
        r"(?P<date>\d\d-[A-Z]{3}-\d{4})"), "HDFC", "bank", "debit", "%d-%b-%Y", lambda m: _clean(m["to"])),
    _Rule("hdfc_deposit", _HDFC, re.compile(
        rf"Amount received:\s*{_AMT}\s*Account:\s*XX(?P<mask>\d{{4}})\s*Date:\s*(?P<date>\d\d-[A-Z]{{3}}-\d{{4}})\s*"
        r"Reference Details:\s*(?P<ref>.+?)\s*Available Balance", re.S), "HDFC", "bank", "credit", "%d-%b-%Y",
        lambda m: _clean(m["ref"])),
    _Rule("hdfc_card_debit", _HDFC, re.compile(
        rf"{_AMT} (?:has been|is) debited from your HDFC Bank Credit Card ending (?P<mask>\d{{4}}) towards "
        r"(?P<merchant>.+?) on (?P<date>\d{1,2} [A-Z][a-z]{2}, \d{4})", re.S), "HDFC", "card", "debit", "%d %b, %Y",
        lambda m: _clean(m["merchant"])),
    _Rule("hdfc_card_use", _HDFC, re.compile(
        rf"Thank you for using HDFC Bank Card XX(?P<mask>\d{{4}}) for {_AMT} at (?P<merchant>.+?) on "
        r"(?P<date>\d\d-\d\d-\d{4})"), "HDFC", "card", "debit", "%d-%m-%Y", lambda m: _clean(m["merchant"])),
    _Rule("hdfc_card_reversal", _HDFC, re.compile(
        rf"reversal of {_AMT} has been initiated to your HDFC Bank Credit Card ending (?P<mask>\d{{4}}) From Merchant:\s*"
        r"(?P<merchant>.+?)\s*Date Time:\s*(?P<date>\d{1,2} [A-Z][a-z]{2}, \d{4})", re.S), "HDFC", "card", "credit",
        "%d %b, %Y", lambda m: _clean(m["merchant"])),
    _Rule("sbi_cbs", _SBI, re.compile(
        r"Your A/C X+\d*(?P<mask>\d{4}) has a (?P<dir>debit|credit) by (?P<how>.+?) of Rs\.? ?(?P<amt>[\d,]+(?:\.\d\d)?) on "
        r"(?P<date>\d\d/\d\d/\d\d)"), "SBI", "bank", "debit", "%d/%m/%y", lambda m: _clean(m["how"]).upper()),
    _Rule("icici_card_txn", _ICICI, re.compile(
        r"ICICI Bank Credit Card XX(?P<mask>\d{4}) has been used for a transaction of INR (?P<amt>[\d,]+\.\d\d) on "
        r"(?P<date>[A-Z][a-z]{2} \d\d, \d{4}) at [\d:]+\. Info: (?P<merchant>.+?)\.\s", re.S), "ICICI", "card", "debit",
        "%b %d, %Y", lambda m: _clean(m["merchant"])),
    _Rule("icici_card_payment", _ICICI, re.compile(
        r"received payment of INR (?P<amt>[\d,]+\.\d\d) on your ICICI Bank Credit Card account \d{4} X{4} X{4} "
        r"(?P<mask>\d{4}) on (?P<date>\d\d-[A-Z][a-z]{2}-\d{4})"), "ICICI", "card", "credit", "%d-%b-%Y",
        lambda m: "PAYMENT RECEIVED"),
)


def _sender(msg: Message) -> str:
    return (msg.sender or "").lower()


def _flat(msg: Message) -> str:
    """Bank HTML breaks sentences across table cells; the rules read one line of single spaces."""
    return re.sub(r"\s+", " ", msg.text)


class AlertParser:
    """Routes by sender, then tries each of that sender's sentence rules on the body text."""

    name = "bank_alerts"
    version = "1.0.0"

    def match(self, msg: Message) -> bool:
        text = _flat(msg)
        return any(r.pattern.search(text) for r in RULES if _sender(msg) in r.senders)

    def parse(self, msg: Message) -> list[Observation]:
        text = _flat(msg)
        for r in RULES:
            if _sender(msg) not in r.senders:
                continue
            m = r.pattern.search(text)
            if not m:
                continue
            direction: Direction = ("credit" if m.groupdict().get("dir") == "credit" else "debit") if r.name == "sbi_cbs" \
                else r.direction
            day: date = datetime.strptime(m["date"].title() if "%b" in r.date_fmt else m["date"], r.date_fmt).date()
            return [Observation(
                occurred_at=day, amount=parse_amount(m["amt"]), direction=direction, narration=r.narration(m),
                ref_no=m.groupdict().get("ref") if r.name.startswith("hdfc_upi") else None, confidence=0.9,
                payload={"rule": r.name, "institution": r.institution, "account_kind": r.kind, "mask": m["mask"],
                         "vpa": m.groupdict().get("vpa")})]
        return []
