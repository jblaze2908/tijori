"""Parser plug-in contract (PLAN §7.1–7.2)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

Direction = Literal["debit", "credit"]


class ParseError(ValueError):
    """The message matched a parser but its content is not in the expected layout."""


@dataclass(frozen=True, slots=True)
class Message:
    """Input to a parser: an email body, an SMS, or `pdftotext -layout` output of an attachment."""

    text: str
    sender: str | None = None
    subject: str | None = None
    filename: str | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    """One sighting of a transaction in one source. The resolver merges sightings into txns."""

    occurred_at: date
    amount: Decimal
    direction: Direction
    narration: str
    balance_after: Decimal | None = None
    value_date: date | None = None
    ref_no: str | None = None
    confidence: float = 1.0
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StatementSummary:
    """Footer totals as printed by the bank; reconciliation checks the lines against these."""

    opening: Decimal
    closing: Decimal
    debit_count: int
    credit_count: int
    total_debits: Decimal
    total_credits: Decimal


@dataclass(frozen=True, slots=True)
class Statement:
    institution: str
    account_mask: str | None
    period_start: date
    period_end: date
    summary: StatementSummary
    lines: tuple[Observation, ...]
    parser: str
    parser_version: str


@runtime_checkable
class Parser(Protocol):
    name: str
    version: str

    def match(self, msg: Message) -> bool: ...

    def parse(self, msg: Message) -> list[Observation]: ...


@runtime_checkable
class StatementParser(Parser, Protocol):
    def parse_statement(self, msg: Message) -> Statement: ...
