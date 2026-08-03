"""Paper-trade the S&P 500.

The whole programmatic interface, for writing strategies:

    import bourse

    p = bourse.load("main")

    if bourse.price("AAPL") < bourse.price_on("AAPL", "2026-07-01") * 0.95:
        p.buy("AAPL", 500)          # $500 worth, written to disk immediately

    p.sell("MSFT", all=True)

Your strategy keeps its own state and decides its own schedule. bourse answers
price questions and executes trades; what you do between those is your business.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .market import price, price_on, prices, quote, quotes
from .portfolio import BourseError, Portfolio, create, names
from .portfolio import load as _load

__all__ = [
    "load",
    "create",
    "names",
    "Portfolio",
    "BourseError",
    "price",
    "price_on",
    "prices",
    "quote",
    "quotes",
]


def _script() -> str | None:
    """The running script's name, so its trades are marked as its own."""
    name = Path(sys.argv[0]).name
    return None if name in ("", "-c", "bourse") else name


def load(name: str) -> Portfolio:
    """Open a portfolio by name. Trades you place are tagged with this script."""
    return _load(name, by=_script())
