"""Helpers for `pdftotext -layout` output, where one narration cell wraps over several lines."""

import re
from datetime import date, datetime

_GLUE_CHARS = "/*-@._"


def join_wrapped(parts: list[str], hard_width: int) -> str:
    """Re-join a wrapped cell. A line at least `hard_width` long was cut mid-token, so no space.

    Punctuation at either side of the break is also glued: banks wrap at field separators.
    """
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return ""
    out = parts[0]
    for prev, nxt in zip(parts, parts[1:]):
        glued = len(prev) >= hard_width or prev[-1] in _GLUE_CHARS or nxt[0] in _GLUE_CHARS
        out += ("" if glued else " ") + nxt
    return out


def parse_date(text: str, fmt: str) -> date:
    return datetime.strptime(text, fmt).date()


def mask_of(account_number: str) -> str:
    """Keep only the last 4 digits; full account numbers never leave the parser."""
    digits = re.sub(r"\D", "", account_number)
    return digits[-4:]
