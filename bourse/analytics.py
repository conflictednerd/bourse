"""Valuation and performance measurement.

Every function here is pure: prices are supplied by the caller as plain
mappings, so the same figures are produced wherever they are displayed and no
part of this module depends on the network or on how results are rendered.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import NamedTuple

from .clock import day as day_of
from .clock import session as session_of
from .clock import today
from .portfolio import DUST, State, replay


class Holding(NamedTuple):
    """A single holding, valued."""

    symbol: str
    name: str
    shares: float
    average_cost: float
    price: float
    value: float
    cost: float  # total paid for the shares still held
    unrealized: float  # profit if sold at the current price
    unrealized_pct: float
    day_change: float
    day_change_pct: float
    weight_pct: float  # share of the portfolio
    priced: bool  # whether a live price was available


class Snapshot(NamedTuple):
    """A portfolio's overall position at a moment in time."""

    total: float  # holdings plus cash
    cash: float
    holdings_value: float
    deposited: float  # deposits less withdrawals
    banked: float  # profit already realized by selling
    gain: float  # total less deposited
    gain_pct: float
    day_change: float
    day_change_pct: float
    holdings: list[Holding]


class Stock(NamedTuple):
    """A complete position history in one company."""

    symbol: str
    name: str
    shares: float
    average_cost: float
    price: float
    value: float
    unrealized: float  # on shares still held
    realized: float  # already booked by selling
    total: float  # realized and unrealized combined
    trades: list[dict]
    priced: bool  # whether a live price was available


class Performance(NamedTuple):
    """A portfolio's result over a period."""

    start: date
    end: date
    start_value: float
    end_value: float
    deposited: float  # net cash added during the period
    gain: float  # market gain, excluding cash added
    return_pct: float  # adjusted for cash flows
    benchmark_pct: float | None  # the index over the same period
    days: list[date]
    values: list[float]


def snapshot(portfolio, quotes: dict, session: date | None = None) -> Snapshot:
    """Value a portfolio against the supplied quotes.

    ``quotes`` maps each symbol to an object exposing ``price``,
    ``previous_close`` and ``name``. A symbol without a quote is valued at its
    average cost and excluded from the day's change, since no movement can be
    established for it.
    """
    session = session or today()

    def price_of(symbol: str) -> float:
        q = quotes.get(symbol)
        return q.price if q else portfolio.average_cost(symbol)

    # Shares carried in from a previous session are valued at the previous
    # close; shares traded during this session are valued at what was paid, so
    # that a purchase made today registers no gain or loss of its own.
    opened = replay([e for e in portfolio.log if session_of(e) < session])

    spent_today: dict[str, float] = {}
    for entry in portfolio.log:
        kind = entry.get("type")
        if kind not in ("buy", "sell") or session_of(entry) != session:
            continue
        symbol = entry["symbol"]
        cash = entry.get("shares", 0.0) * entry.get("price", 0.0)
        spent_today[symbol] = spent_today.get(symbol, 0.0) + (cash if kind == "buy" else -cash)

    # `opening` is the denominator for the day's percentage. Cash deposited
    # during the session was never exposed to the day's movement.
    basis = {}
    opening = opened.cash
    for symbol in set(opened.shares) | set(spent_today):
        q = quotes.get(symbol)
        if q is None:
            opening += opened.shares.get(symbol, 0.0) * price_of(symbol)
            continue
        carried_in = opened.shares.get(symbol, 0.0) * q.previous_close
        basis[symbol] = carried_in + spent_today.get(symbol, 0.0)
        opening += carried_in

    holdings = []
    holdings_value = 0.0
    day_change = 0.0

    for symbol, shares in sorted(portfolio.shares.items()):
        q = quotes.get(symbol)
        price = price_of(symbol)
        cost = portfolio.cost.get(symbol, 0.0)
        value = shares * price
        holdings_value += value
        opened_at = basis.get(symbol, value)
        moved = value - opened_at
        day_change += moved

        holdings.append(
            Holding(
                symbol=symbol,
                name=q.name if q else symbol,
                shares=shares,
                average_cost=portfolio.average_cost(symbol),
                price=price,
                value=value,
                cost=cost,
                unrealized=value - cost,
                unrealized_pct=(value / cost - 1) * 100 if cost else 0.0,
                day_change=moved,
                day_change_pct=(moved / opened_at * 100) if opened_at else 0.0,
                weight_pct=0.0,  # assigned below, once the total is known
                priced=q is not None,
            )
        )

    # A position closed during the session has no row of its own but still
    # contributed to the day's result.
    for symbol, opened_at in basis.items():
        if symbol not in portfolio.shares:
            day_change += 0.0 - opened_at

    total = holdings_value + portfolio.cash
    holdings = [
        h._replace(weight_pct=h.value / total * 100 if total else 0.0) for h in holdings
    ]
    holdings.sort(key=lambda h: h.value, reverse=True)

    return Snapshot(
        total=total,
        cash=portfolio.cash,
        holdings_value=holdings_value,
        deposited=portfolio.deposited,
        banked=sum(portfolio.realized.values()),
        gain=total - portfolio.deposited,
        gain_pct=(total / portfolio.deposited - 1) * 100 if portfolio.deposited else 0.0,
        day_change=day_change,
        day_change_pct=(day_change / opening * 100) if opening else 0.0,
        holdings=holdings,
    )


def stock(portfolio, symbol: str, quotes: dict) -> Stock:
    """Summarise every position ever taken in one company, open or closed."""
    symbol = symbol.upper()
    q = quotes.get(symbol)
    shares = portfolio.shares.get(symbol, 0.0)
    average = portfolio.average_cost(symbol)
    price = q.price if q else average
    value = shares * price
    unrealized = value - portfolio.cost.get(symbol, 0.0)
    realized = portfolio.realized.get(symbol, 0.0)

    return Stock(
        symbol=symbol,
        name=q.name if q else symbol,
        shares=shares,
        average_cost=average,
        price=price,
        value=value,
        unrealized=unrealized,
        realized=realized,
        total=unrealized + realized,
        trades=[e for e in portfolio.log if e.get("symbol") == symbol],
        priced=q is not None or shares <= 0,
    )


def traded_symbols(log: list[dict]) -> list[str]:
    """Every symbol appearing in a log, whether still held or not."""
    return sorted({e["symbol"] for e in log if "symbol" in e})


def prices_on(closes: dict[str, dict[date, float]], days: list[date]) -> list[dict[str, float]]:
    """The latest close known on each day, for every symbol.

    ``days`` must be in ascending order. Prices are carried forward across days
    with no close of their own, so that weekends, holidays and gaps in the data
    do not value a holding at nothing.
    """
    latest: dict[str, float] = {}
    pending = {symbol: (sorted(history), history, 0) for symbol, history in closes.items()}
    rows = []
    for day in days:
        for symbol, (dates, history, cursor) in pending.items():
            while cursor < len(dates) and dates[cursor] <= day:
                latest[symbol] = history[dates[cursor]]
                cursor += 1
            pending[symbol] = (dates, history, cursor)
        rows.append(dict(latest))
    return rows


def close_on(history: dict[date, float], day: date) -> float | None:
    """One symbol's close on ``day``, or the most recent close before it."""
    known = [d for d in history if d <= day]
    return history[max(known)] if known else None


def worth(state: State, prices: dict[str, float]) -> float:
    """Cash plus holdings, valued at the given prices.

    A holding with no price available is valued at its average cost, so that it
    contributes its known worth rather than dropping out of the total.
    """
    total = state.cash
    for symbol, shares in state.shares.items():
        price = prices.get(symbol)
        if price is None:
            price = state.cost.get(symbol, 0.0) / shares if shares else 0.0
        total += shares * price
    return total


def value_series(
    log: list[dict], closes: dict[str, dict[date, float]], days: list[date]
) -> list[float]:
    """The portfolio's value at the close of each day."""
    values = []
    cursor = 0  # entries are in order, so walk them alongside the days
    for day, prices in zip(days, prices_on(closes, days)):
        while cursor < len(log) and day_of(log[cursor]) <= day:
            cursor += 1
        values.append(worth(replay(log[:cursor]), prices))
    return values


def performance(
    log: list[dict],
    closes: dict[str, dict[date, float]],
    days: list[date],
    benchmark: dict[date, float] | None = None,
) -> Performance:
    """Measure performance over a period, excluding the effect of cash flows.

    The period is divided at each deposit and withdrawal and the resulting
    sub-period returns are chained, so that adding or removing cash does not
    register as a gain or a loss.
    """
    if not days:
        raise ValueError("performance needs at least one day")

    start, end = days[0], days[-1]
    values = value_series(log, closes, days)
    start_value, end_value = values[0], values[-1]

    flows = [
        (i, day_of(e), e["amount"] if e["type"] == "deposit" else -e["amount"])
        for i, e in enumerate(log)
        if e.get("type") in ("deposit", "withdraw") and start < day_of(e) <= end
    ]
    deposited = sum(amount for _, _, amount in flows)

    # Holdings are valued at the close preceding each cash flow. Using the
    # flow day's own close would attribute that day's movement to both the
    # sub-period ending at the flow and the one beginning after it.
    eve = [day - timedelta(days=1) for _, day, _ in flows]
    factor = 1.0
    previous = start_value
    for (position, _, amount), prices in zip(flows, prices_on(closes, eve)):
        before = worth(replay(log[:position]), prices)
        if previous > DUST:
            factor *= before / previous
        previous = before + amount
    if previous > DUST:
        factor *= end_value / previous

    benchmark_pct = None
    if benchmark:
        first, last = close_on(benchmark, start), close_on(benchmark, end)
        if first and last:
            benchmark_pct = (last / first - 1) * 100

    return Performance(
        start=start,
        end=end,
        start_value=start_value,
        end_value=end_value,
        deposited=deposited,
        gain=end_value - start_value - deposited,
        return_pct=(factor - 1) * 100,
        benchmark_pct=benchmark_pct,
        days=days,
        values=values,
    )


def trading_days(closes: dict[str, dict[date, float]], start: date, end: date) -> list[date]:
    """The trading days within a range, inferred from the price data."""
    days = {d for history in closes.values() for d in history if start <= d <= end}
    return sorted(days)
