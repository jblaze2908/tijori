"""Shared shape for credit-card statements. A card's balance is money owed, so it is carried as a
negative number: purchases (debits) push it down and payments (credits) bring it back, which lets
`reconcile` apply its bank rule unchanged. Card lines print no running balance."""

from decimal import Decimal

from tijori.parsers.base import Observation, StatementSummary


def card_summary(previous_due: Decimal, total_due: Decimal, lines: list[Observation]) -> StatementSummary:
    """Counts are taken from the lines (card statements don't print them); the printed debit and
    credit totals are what reconciliation checks, via `total_due` = previous + debits − credits."""
    debits = [o.amount for o in lines if o.direction == "debit"]
    credits = [o.amount for o in lines if o.direction == "credit"]
    return StatementSummary(opening=-previous_due, closing=-total_due, debit_count=len(debits),
                            credit_count=len(credits), total_debits=sum(debits, Decimal(0)),
                            total_credits=sum(credits, Decimal(0)))
