"""Parser registry. Routing picks the first registered parser whose `match()` accepts the message."""

from tijori.parsers.base import (
    Message,
    Observation,
    ParseError,
    Parser,
    Statement,
    StatementParser,
    StatementSummary,
)
from tijori.parsers.hdfc_savings_statement import HdfcSavingsStatementParser
from tijori.parsers.reconcile import Reconciliation, reconcile
from tijori.parsers.sbi_statement import SbiStatementParser

_REGISTRY: list[Parser] = []


def register(parser: Parser) -> Parser:
    if any(p.name == parser.name for p in _REGISTRY):
        raise ValueError(f"parser {parser.name!r} already registered")
    _REGISTRY.append(parser)
    return parser


def registered() -> tuple[Parser, ...]:
    return tuple(_REGISTRY)


def route(msg: Message) -> Parser | None:
    """None means "parser needed": the caller queues the raw message for a new parser."""
    return next((p for p in _REGISTRY if p.match(msg)), None)


register(SbiStatementParser())
register(HdfcSavingsStatementParser())

__all__ = [
    "Message",
    "Observation",
    "ParseError",
    "Parser",
    "Reconciliation",
    "Statement",
    "StatementParser",
    "StatementSummary",
    "reconcile",
    "register",
    "registered",
    "route",
]
