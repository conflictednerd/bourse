"""Market data.

Prices are fetched when they are needed and never stored: there is no cache, no
persistent state and no background process. Requests for several symbols are
issued concurrently.

This is the only module that uses the network, and the only one that would need
to change to use a different data provider.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from .portfolio import BourseError

_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 10


class UnknownSymbol(BourseError):
    """No such ticker: delisted, renamed, or never listed.

    Distinguished from transient failures so that batch requests can skip it
    safely. A timeout or rate limit must never be treated as a missing symbol.
    """


class Quote(NamedTuple):
    symbol: str
    price: float  # live price, or the last close when the market is shut
    previous_close: float  # the close of the preceding session
    name: str


def _get(symbol: str, query: str) -> dict:
    url = _URL.format(symbol=symbol.upper()) + "?" + query
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=_HEADERS), timeout=_TIMEOUT
        ) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise UnknownSymbol(f"No such symbol: {symbol.upper()}.") from None
        raise BourseError(
            f"The market data service returned an error ({error.code}). Try again shortly."
        ) from None
    except json.JSONDecodeError:
        raise BourseError(
            "The market data service sent something unreadable. Try again shortly."
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise BourseError(
            "Can't reach the market data service, so current prices are unavailable. "
            "Your trade history and past profits still work offline."
        ) from None

    results = (body.get("chart") or {}).get("result")
    if not results:
        raise UnknownSymbol(f"No price data available for {symbol.upper()}.")
    return results[0]


def _closes(result: dict) -> list[tuple[date, float]]:
    """Daily closes from a chart response, oldest first, omitting gaps."""
    stamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    values = quote.get("close") or []
    return [
        (datetime.fromtimestamp(t, timezone.utc).date(), v)
        for t, v in zip(stamps, values)
        if v is not None
    ]


def quote(symbol: str) -> Quote:
    """The current price and previous close for one symbol.

    A five-day window is requested so that the previous close survives weekends
    and holidays. The provider's own ``chartPreviousClose`` field is not used:
    it reports the close preceding the requested window, not the prior session.
    """
    result = _get(symbol, "range=5d&interval=1d")
    meta = result.get("meta", {})
    closes = _closes(result)

    price = meta.get("regularMarketPrice")
    if price is None:
        if not closes:
            raise BourseError(f"No price data available for {symbol.upper()}.")
        price = closes[-1][1]

    if len(closes) >= 2:
        previous = closes[-2][1]
    else:
        previous = meta.get("chartPreviousClose", price)

    return Quote(
        symbol=meta.get("symbol", symbol.upper()),
        price=float(price),
        previous_close=float(previous),
        name=meta.get("shortName") or meta.get("longName") or symbol.upper(),
    )


def _gather(symbols: list[str], fetch) -> dict:
    """Fetch several symbols concurrently, skipping ones that no longer exist.

    Only :class:`UnknownSymbol` is tolerated. Any other failure propagates,
    since presenting a partial result as a complete one would understate the
    portfolio.
    """
    symbols = sorted({s.upper() for s in symbols})
    if not symbols:
        return {}

    def one(symbol: str):
        try:
            return symbol, fetch(symbol)
        except UnknownSymbol:
            return symbol, None

    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as pool:
        return {s: value for s, value in pool.map(one, symbols) if value is not None}


def quotes(symbols: list[str]) -> dict[str, Quote]:
    """Current prices for several symbols."""
    return _gather(symbols, quote)


def price(symbol: str) -> float:
    """The current price of one share."""
    return quote(symbol).price


def prices(symbols: list[str]) -> dict[str, float]:
    """The current price of one share of each symbol."""
    return {s: q.price for s, q in quotes(symbols).items()}


def price_on(symbol: str, day: date | str) -> float:
    """The closing price on a past day, or the most recent close before it."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    history = series([symbol], day - timedelta(days=10), day).get(symbol.upper(), {})
    earlier = [d for d in history if d <= day]
    if not earlier:
        raise BourseError(f"No price for {symbol.upper()} around {day}.")
    return history[max(earlier)]


def series(symbols: list[str], start: date, end: date) -> dict[str, dict[date, float]]:
    """Daily closes between two dates, as ``{symbol: {day: close}}``."""
    # Padded by a day at each end to cover timezone edges at the boundaries.
    period1 = int(
        datetime.combine(
            start - timedelta(days=1), datetime.min.time(), timezone.utc
        ).timestamp()
    )
    period2 = int(
        datetime.combine(end + timedelta(days=1), datetime.min.time(), timezone.utc).timestamp()
    )
    query = f"period1={period1}&period2={period2}&interval=1d"

    return _gather(symbols, lambda symbol: dict(_closes(_get(symbol, query))))
