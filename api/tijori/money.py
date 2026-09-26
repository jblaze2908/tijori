"""Decimal helpers. Money is never a float anywhere in Tijori."""

from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def parse_amount(text: str) -> Decimal:
    """Parse '1,23,456.78', '₹1,234.00', '-₹105.00', '1,234.56CR' into a 2-place Decimal.

    A trailing DR marks a negative balance; CR is positive.
    """
    s = text.strip().replace("₹", "").replace(",", "").replace(" ", "")
    sign = 1
    upper = s.upper()
    if upper.endswith("CR"):
        s = s[:-2]
    elif upper.endswith("DR"):
        s, sign = s[:-2], -1
    if s.startswith("-"):
        s, sign = s[1:], -sign
    try:
        value = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"not an amount: {text!r}") from exc
    return (value * sign).quantize(CENT)


def fmt(value: Decimal | None) -> str | None:
    """Serialise money for JSON as a fixed 2-place string."""
    return None if value is None else str(value.quantize(CENT))
