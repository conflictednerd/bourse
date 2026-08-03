"""Valuation and performance, exercised with literal prices."""

from datetime import date
from typing import NamedTuple

import pytest

from bourse import analytics
from bourse.portfolio import Portfolio


class FakeQuote(NamedTuple):
    """The three attributes analytics reads from a quote."""

    price: float
    previous_close: float
    name: str = "Some Company"


def test_snapshot_adds_up():
    p = Portfolio("t")
    p.deposit(10_000)
    p.buy("AAPL", shares=10, price=100)  # $1,000
    p.buy("MSFT", shares=5, price=200)  # $1,000

    s = analytics.snapshot(
        p,
        {
            "AAPL": FakeQuote(price=110, previous_close=100),
            "MSFT": FakeQuote(price=180, previous_close=200),
        },
    )

    assert s.cash == 8_000
    assert s.holdings_value == pytest.approx(10 * 110 + 5 * 180)  # 2,000
    assert s.total == pytest.approx(10_000)
    assert s.deposited == 10_000
    assert s.gain == pytest.approx(0)  # up 100 on Apple, down 100 on Microsoft
    assert s.day_change == pytest.approx(10 * 10 + 5 * -20)  # +100 - 100 = 0

    apple = next(h for h in s.holdings if h.symbol == "AAPL")
    assert apple.unrealized == pytest.approx(100)
    assert apple.unrealized_pct == pytest.approx(10)
    assert apple.weight_pct == pytest.approx(11.0)


def test_buying_today_is_not_a_loss():
    """A purchase made during the session registers no gain or loss of its own."""
    p = Portfolio("t")
    p.deposit(10_000)
    p.buy("AAPL", 3_000, price=300)  # bought now, at 300

    # The stock is down heavily on the day -- but not on our watch.
    s = analytics.snapshot(p, {"AAPL": FakeQuote(price=300, previous_close=330)})
    assert s.day_change == pytest.approx(0)
    assert s.day_change_pct == pytest.approx(0)


def test_day_change_mixes_old_and_new_shares_correctly():
    p = Portfolio(
        "t",
        log=[
            {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 10_000},
            {
                "at": "2026-01-01T15:00:01Z",
                "type": "buy",
                "symbol": "X",
                "shares": 10,
                "price": 290,
            },
            # ...and five more during today's session
            {
                "at": "2026-01-02T15:00:00Z",
                "type": "buy",
                "symbol": "X",
                "shares": 5,
                "price": 305,
            },
        ],
    )
    s = analytics.snapshot(
        p, {"X": FakeQuote(price=310, previous_close=300)}, session=date(2026, 1, 2)
    )
    # Ten shares carried in at 300 gained 10 each; five bought at 305 gained 5 each.
    assert s.day_change == pytest.approx(10 * 10 + 5 * 5)


def test_a_stock_bought_and_sold_within_the_day_still_counts():
    p = Portfolio(
        "t",
        log=[
            {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 10_000},
            {
                "at": "2026-01-01T15:00:01Z",
                "type": "buy",
                "symbol": "X",
                "shares": 10,
                "price": 290,
            },
            {
                "at": "2026-01-02T15:00:00Z",
                "type": "sell",
                "symbol": "X",
                "shares": 10,
                "price": 310,
            },
        ],
    )
    # Held at yesterday's close of 300, sold today at 310: up 10 a share, even
    # though there is no holding left to show it on.
    s = analytics.snapshot(
        p, {"X": FakeQuote(price=310, previous_close=300)}, session=date(2026, 1, 2)
    )
    assert s.day_change == pytest.approx(100)


def test_snapshot_holdings_are_ordered_by_size():
    p = Portfolio("t")
    p.deposit(10_000)
    p.buy("AAA", shares=1, price=100)
    p.buy("BBB", shares=1, price=900)
    s = analytics.snapshot(
        p,
        {
            "AAA": FakeQuote(100, 100),
            "BBB": FakeQuote(900, 900),
        },
    )
    assert [h.symbol for h in s.holdings] == ["BBB", "AAA"]


def test_stock_combines_what_you_hold_and_what_you_sold():
    p = Portfolio("t")
    p.deposit(10_000)
    p.buy("AAPL", shares=10, price=100)
    p.sell("AAPL", shares=5, price=150)  # banks 250

    detail = analytics.stock(p, "AAPL", {"AAPL": FakeQuote(price=200, previous_close=150)})
    assert detail.shares == 5
    assert detail.realized == pytest.approx(250)
    assert detail.unrealized == pytest.approx(5 * 200 - 5 * 100)  # 500
    assert detail.total == pytest.approx(750)
    assert len(detail.trades) == 2


def test_stock_still_reports_a_position_you_have_closed():
    p = Portfolio("t")
    p.deposit(10_000)
    p.buy("AAPL", shares=10, price=100)
    p.sell("AAPL", all=True, price=120)

    detail = analytics.stock(p, "AAPL", {})
    assert detail.shares == 0
    assert detail.unrealized == 0
    assert detail.realized == pytest.approx(200)
    assert detail.total == pytest.approx(200)


# -- deposit-adjusted returns ----------------------------------------------

DAYS = [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)]
CLOSES = {"X": {DAYS[0]: 100.0, DAYS[1]: 110.0, DAYS[2]: 110.0, DAYS[3]: 121.0}}


def _log_with_a_midway_deposit():
    """Buy 10 shares at 100, then double your money in cash on day 3."""
    return [
        {"at": "2026-01-01T00:00:00Z", "type": "deposit", "amount": 1_000},
        {
            "at": "2026-01-01T00:00:01Z",
            "type": "buy",
            "symbol": "X",
            "shares": 10,
            "price": 100,
        },
        {"at": "2026-01-03T00:00:00Z", "type": "deposit", "amount": 1_100},
    ]


def test_value_series_tracks_the_portfolio_day_by_day():
    values = analytics.value_series(_log_with_a_midway_deposit(), CLOSES, DAYS)
    #                day1   day2   day3 (deposit landed)  day4
    assert values == pytest.approx([1_000, 1_100, 2_200, 2_310])


def test_return_excludes_deposits():
    perf = analytics.performance(_log_with_a_midway_deposit(), CLOSES, DAYS)

    # The naive sum would be (2310 - 1000) / 1000 = +131%, which is nonsense:
    # most of that "gain" was money carried in by hand.
    assert perf.end_value == pytest.approx(2_310)
    assert perf.deposited == pytest.approx(1_100)

    # Worked by hand: the stock rose 10% while it was the whole portfolio,
    # then 10% again while it was only half of it.
    #   1.10  *  (2310 / 2200)  =  1.10 * 1.05  =  1.155
    assert perf.return_pct == pytest.approx(15.5)

    # And the money actually made: 100 on day 2, 110 on day 4.
    assert perf.gain == pytest.approx(210)


def test_return_with_no_deposits_is_just_the_change():
    log = [
        {"at": "2026-01-01T00:00:00Z", "type": "deposit", "amount": 1_000},
        {
            "at": "2026-01-01T00:00:01Z",
            "type": "buy",
            "symbol": "X",
            "shares": 10,
            "price": 100,
        },
    ]
    perf = analytics.performance(log, CLOSES, DAYS)
    assert perf.return_pct == pytest.approx(21.0)  # 100 -> 121
    assert perf.gain == pytest.approx(210)


def test_withdrawal_does_not_read_as_a_loss():
    log = [
        {"at": "2026-01-01T00:00:00Z", "type": "deposit", "amount": 2_000},
        {
            "at": "2026-01-01T00:00:01Z",
            "type": "buy",
            "symbol": "X",
            "shares": 10,
            "price": 100,
        },
        {"at": "2026-01-03T00:00:00Z", "type": "withdraw", "amount": 1_000},
    ]
    perf = analytics.performance(log, CLOSES, DAYS)
    assert perf.deposited == pytest.approx(-1_000)
    # Value fell from 2,000 to 2,210 - 1,000, but the stock only ever went up.
    #   before the withdrawal: 2,000 -> 2,100  = 1.05
    #   after it:              1,100 -> 1,210  = 1.10
    #   linked:                1.05 * 1.10 - 1 = 15.5%
    # `> 0` would also accept 0.001% or 10,000%, so pin the number.
    assert perf.return_pct == pytest.approx(15.5)
    assert perf.gain == pytest.approx(210)


def test_benchmark_is_the_plain_change_over_the_window():
    perf = analytics.performance(
        _log_with_a_midway_deposit(),
        CLOSES,
        DAYS,
        benchmark={DAYS[0]: 500.0, DAYS[3]: 550.0},
    )
    assert perf.benchmark_pct == pytest.approx(10.0)


def test_missing_price_on_a_holiday_carries_the_last_one_forward():
    closes = {"X": {DAYS[0]: 100.0, DAYS[3]: 121.0}}  # days 2 and 3 absent
    values = analytics.value_series(_log_with_a_midway_deposit(), closes, DAYS)
    assert values == pytest.approx([1_000, 1_000, 2_100, 2_310])


def test_trading_days_come_from_the_price_data():
    closes = {"X": {date(2026, 1, 1): 1.0, date(2026, 1, 5): 1.0}, "Y": {date(2026, 1, 2): 1.0}}
    assert analytics.trading_days(closes, date(2026, 1, 1), date(2026, 1, 3)) == [
        date(2026, 1, 1),
        date(2026, 1, 2),
    ]


# -- cash flows, sessions and gaps in the price data -----------------------


def test_a_weekend_deposit_does_not_wipe_out_the_return():
    """A cash flow on a non-trading day is valued at the preceding close."""
    friday, monday = date(2026, 1, 2), date(2026, 1, 5)
    log = [
        {"at": "2026-01-02T15:00:00Z", "type": "deposit", "amount": 1_000},
        {
            "at": "2026-01-02T15:00:01Z",
            "type": "buy",
            "symbol": "X",
            "shares": 10,
            "price": 100,
        },
        {"at": "2026-01-03T15:00:00Z", "type": "deposit", "amount": 1_000},  # Saturday
    ]
    closes = {"X": {friday: 100.0, monday: 110.0}}

    perf = analytics.performance(log, closes, [friday, monday])
    # Friday 1,000; deposit makes 2,000; Monday 10 * 110 + 1,000 = 2,100.
    assert perf.values == pytest.approx([1_000, 2_100])
    assert perf.return_pct == pytest.approx(5.0)


def test_a_deposit_spent_the_same_day_is_not_counted_twice():
    """A day's movement is credited to one sub-period, not to both."""
    day1, day2 = date(2026, 1, 1), date(2026, 1, 2)
    log = [
        {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 1_000},
        {
            "at": "2026-01-01T15:00:01Z",
            "type": "buy",
            "symbol": "X",
            "shares": 10,
            "price": 100,
        },
        {"at": "2026-01-02T14:30:00Z", "type": "deposit", "amount": 1_000},
        {
            "at": "2026-01-02T14:30:01Z",
            "type": "buy",
            "symbol": "X",
            "shares": 10,
            "price": 100,
        },
    ]
    closes = {"X": {day1: 100.0, day2: 110.0}}

    perf = analytics.performance(log, closes, [day1, day2])
    # 20 shares bought at 100 closing at 110 is a 10% day, not 15.2%.
    assert perf.end_value == pytest.approx(2_200)
    assert perf.return_pct == pytest.approx(10.0)


def test_a_window_starting_on_a_closed_day_still_knows_the_price():
    """A period may begin on a day the market was shut."""
    day1, day2, day3 = date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)
    log = [
        {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 1_000},
        {
            "at": "2026-01-01T15:00:01Z",
            "type": "buy",
            "symbol": "X",
            "shares": 10,
            "price": 100,
        },
    ]
    closes = {"X": {day1: 100.0, day3: 110.0}}  # nothing on day 2

    perf = analytics.performance(log, closes, [day2, day3])
    assert perf.values == pytest.approx([1_000, 1_100])
    assert perf.return_pct == pytest.approx(10.0)


def test_a_holding_with_no_quote_reports_no_movement_rather_than_a_windfall():
    """A holding without a quote contributes no movement to the day."""
    p = Portfolio(
        "t",
        log=[
            {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 1_000},
            {
                "at": "2026-01-01T15:00:01Z",
                "type": "buy",
                "symbol": "X",
                "shares": 10,
                "price": 100,
            },
        ],
    )
    s = analytics.snapshot(p, {}, session=date(2026, 1, 2))
    assert s.total == pytest.approx(1_000)
    assert s.day_change == pytest.approx(0)
    assert s.day_change_pct == pytest.approx(0)


def test_money_paid_in_today_does_not_dilute_todays_percentage():
    """Cash deposited during the session is excluded from the day's base."""
    p = Portfolio(
        "t",
        log=[
            {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 1_000},
            {
                "at": "2026-01-01T15:00:01Z",
                "type": "buy",
                "symbol": "X",
                "shares": 10,
                "price": 100,
            },
            {"at": "2026-01-02T20:59:00Z", "type": "deposit", "amount": 9_000},
        ],
    )
    s = analytics.snapshot(
        p, {"X": FakeQuote(price=110, previous_close=100)}, session=date(2026, 1, 2)
    )
    # The portfolio opened at 1,000 and made 100. That is 10%, not 1%.
    assert s.total == pytest.approx(10_100)
    assert s.day_change == pytest.approx(100)
    assert s.day_change_pct == pytest.approx(10.0)


def test_a_price_is_still_current_after_a_symbol_is_sold_and_bought_back():
    """Prices observed while a position was closed remain current."""
    days = [date(2026, 1, n) for n in range(1, 5)]
    log = [
        {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 2_000},
        {
            "at": "2026-01-01T15:00:01Z",
            "type": "buy",
            "symbol": "X",
            "shares": 10,
            "price": 100,
        },
        {
            "at": "2026-01-02T15:00:00Z",
            "type": "sell",
            "symbol": "X",
            "shares": 10,
            "price": 110,
        },
        {"at": "2026-01-03T15:00:00Z", "type": "buy", "symbol": "X", "shares": 5, "price": 120},
    ]
    closes = {"X": {days[0]: 100.0, days[1]: 110.0, days[3]: 130.0}}  # day 3 missing

    # Day 3: cash 1,500 plus 5 shares at the most recent known close, 110.
    assert analytics.value_series(log, closes, days) == pytest.approx(
        [2_000, 2_100, 2_050, 2_150]
    )
