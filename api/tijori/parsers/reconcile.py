"""Statement reconciliation (PLAN §7.5): the lines must explain the footer to the paisa."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tijori.money import ZERO
from tijori.parsers.base import Statement


@dataclass(frozen=True, slots=True)
class ChainBreak:
    index: int
    occurred_at: date
    expected_balance: Decimal
    printed_balance: Decimal


@dataclass(frozen=True, slots=True)
class Reconciliation:
    line_count: int
    debit_count: int
    credit_count: int
    total_debits: Decimal
    total_credits: Decimal
    chain_breaks: tuple[ChainBreak, ...]
    # opening + credits - debits - closing, from the lines; ₹0 when the statement balances.
    closing_diff: Decimal
    footer_mismatches: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.chain_breaks and self.closing_diff == ZERO and not self.footer_mismatches


def reconcile(stmt: Statement) -> Reconciliation:
    s = stmt.summary
    debits = [o.amount for o in stmt.lines if o.direction == "debit"]
    credits = [o.amount for o in stmt.lines if o.direction == "credit"]
    total_dr, total_cr = sum(debits, ZERO), sum(credits, ZERO)

    breaks: list[ChainBreak] = []
    running = s.opening
    for i, o in enumerate(stmt.lines):
        running = running - o.amount if o.direction == "debit" else running + o.amount
        if o.balance_after is not None:
            if o.balance_after != running:
                breaks.append(ChainBreak(i, o.occurred_at, running, o.balance_after))
            running = o.balance_after  # resync so one bad row reports once

    mismatches: list[str] = []
    for label, got, want in (
        ("debit_count", len(debits), s.debit_count),
        ("credit_count", len(credits), s.credit_count),
        ("total_debits", total_dr, s.total_debits),
        ("total_credits", total_cr, s.total_credits),
    ):
        if got != want:
            mismatches.append(f"{label}: lines give {got}, footer says {want}")
    last = stmt.lines[-1].balance_after if stmt.lines else s.opening
    if last is not None and last != s.closing:
        mismatches.append(f"closing: last line balance {last}, footer says {s.closing}")

    return Reconciliation(
        line_count=len(stmt.lines),
        debit_count=len(debits),
        credit_count=len(credits),
        total_debits=total_dr,
        total_credits=total_cr,
        chain_breaks=tuple(breaks),
        closing_diff=s.opening + total_cr - total_dr - s.closing,
        footer_mismatches=tuple(mismatches),
    )
