"""Payee memory (PLAN §7.4 step 3.2): learned from confirmed categories, never from guesses."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

MIN_AGREEING = 2


@dataclass(frozen=True, slots=True)
class MemoryVerdict:
    status: Literal["auto", "conflict", "seen_once"]
    category: str | None
    options: tuple[tuple[str, int], ...]


def memory_key(direction: str, payee_key: str) -> str:
    """Direction-scoped: paying someone and being paid by them are different categories."""
    return f"{direction}|{payee_key}"


class PayeeMemory:
    def __init__(self, min_agreeing: int = MIN_AGREEING) -> None:
        self._counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.min_agreeing = min_agreeing

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[str, str]], min_agreeing: int = MIN_AGREEING) -> "PayeeMemory":
        mem = cls(min_agreeing)
        for key, category in pairs:
            mem.learn(key, category)
        return mem

    @classmethod
    def from_counts(cls, rows: Iterable[tuple[str, str, int]], min_agreeing: int = MIN_AGREEING) -> "PayeeMemory":
        """Build from a `GROUP BY payee_key, category` query: one DB round-trip per batch."""
        mem = cls(min_agreeing)
        for key, category, n in rows:
            mem._counts[key][category] += n
        return mem

    def learn(self, payee_key: str, category: str) -> None:
        self._counts[payee_key][category] += 1

    def history(self, payee_key: str) -> tuple[tuple[str, int], ...]:
        return tuple(self._counts[payee_key].most_common()) if payee_key in self._counts else ()

    def lookup(self, payee_key: str) -> MemoryVerdict | None:
        counts = self._counts.get(payee_key)
        if not counts:
            return None
        options = tuple(counts.most_common())
        if len(counts) > 1:
            return MemoryVerdict("conflict", None, options)
        category, n = options[0]
        return MemoryVerdict("auto" if n >= self.min_agreeing else "seen_once", category, options)
