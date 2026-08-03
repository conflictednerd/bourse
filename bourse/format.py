"""Numbers as text.

Presentation only: the command line and the dashboard share these so that a
figure is written the same way wherever it appears.
"""

from __future__ import annotations

NEGLIGIBLE = 0.005


def settled(value: float) -> float:
    """Treat amounts below half a cent as zero.

    Fractional shares leave remainders of the order of 1e-13, which would
    otherwise be rendered as a negative zero.
    """
    return 0.0 if abs(value) < NEGLIGIBLE else value


def money(value: float) -> str:
    value = settled(value)
    return f"-${abs(value):,.2f}" if value < 0 else f"${value:,.2f}"


def signed_money(value: float) -> str:
    return f"+${value:,.2f}" if settled(value) > 0 else money(value)


def percent(value: float) -> str:
    return f"{settled(value):+,.2f}%"


def shares(value: float) -> str:
    """A share count with trailing zeros removed."""
    return f"{value:,.6f}".rstrip("0").rstrip(".") or "0"
