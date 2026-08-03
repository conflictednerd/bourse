"""Storage, concurrency and market-data failure handling."""

import json
import subprocess
import sys
import textwrap
from datetime import date

import pytest

from bourse import analytics, market
from bourse.portfolio import DUST, BourseError, Portfolio, create, load

# -- portfolio names ------------------------------------------------------


@pytest.mark.parametrize("name", ["../escaped", "a/b", "", ".", "..", "sub/../x"])
def test_a_name_that_would_escape_the_bourse_directory_is_refused(name, isolated_home):
    with pytest.raises(BourseError, match="can't be used as a portfolio name"):
        create(name, 100)
    # Nothing is written, inside the bourse directory or outside it.
    assert list(isolated_home.iterdir()) == []


def test_ordinary_names_still_work(isolated_home):
    for name in ["mine", "swing-trades", "test_2", "Ünicode"]:
        create(name, 100)
    assert sorted(p.stem for p in isolated_home.glob("*.json")) == [
        "mine",
        "swing-trades",
        "test_2",
        "Ünicode",
    ]


def test_a_copied_portfolio_does_not_write_back_into_the_original(isolated_home):
    """Copying a portfolio file must produce an independent portfolio.

    The copy still records the original's name, so identity has to come from the
    filename or trades would be written back into the original.
    """
    original = create("longterm", 50_000)
    original.buy("AAPL", 10_000, price=100)
    (isolated_home / "whatif.json").write_text((isolated_home / "longterm.json").read_text())

    branch = load("whatif")
    assert branch.name == "whatif"
    branch.buy("MSFT", 5_000, price=200)

    assert [e.get("symbol") for e in load("whatif").log] == [None, "AAPL", "MSFT"]
    assert [e.get("symbol") for e in load("longterm").log] == [None, "AAPL"]


# -- concurrent access ----------------------------------------------------


def test_concurrent_writers_do_not_erase_each_others_trades(isolated_home):
    """Simultaneous writers must not overwrite one another's entries.

    Each process loads the same log and then appends to it, which is what a
    scheduled strategy and an interactive command do when they overlap.
    """
    create("shared", 100_000)

    worker = textwrap.dedent("""
        import sys, time
        from bourse.portfolio import load
        p = load("shared")
        time.sleep(0.3)          # every process now holds the same starting log
        for _ in range(5):
            p.buy("S" + sys.argv[1], shares=1, price=10)
    """)
    script = isolated_home / "worker.py"
    script.write_text(worker)

    env = {"BOURSE_HOME": str(isolated_home), "PATH": "/usr/bin:/bin"}
    procs = [
        subprocess.Popen([sys.executable, str(script), tag], env=env) for tag in ("A", "B", "C")
    ]
    for proc in procs:
        proc.wait(timeout=60)

    p = load("shared")
    assert len([e for e in p.log if e["type"] == "buy"]) == 15
    assert p.shares == {"SA": 5, "SB": 5, "SC": 5}


# -- unavailable prices ---------------------------------------------------


def test_one_dead_symbol_does_not_blind_the_whole_batch(monkeypatch):
    """One unpriceable holding must not prevent the rest being valued.

    Constituents are delisted or renamed several times a year.
    """

    def fake_quote(symbol):
        if symbol == "DEAD":
            raise market.UnknownSymbol("No such symbol: DEAD.")
        return market.Quote(symbol, 100.0, 90.0, f"{symbol} Inc.")

    monkeypatch.setattr(market, "quote", fake_quote)
    got = market.quotes(["AAPL", "DEAD", "MSFT"])
    assert sorted(got) == ["AAPL", "MSFT"]


def test_a_rate_limit_is_not_mistaken_for_a_delisting(monkeypatch):
    """Transient failures must surface rather than being skipped.

    Omitting a symbol is only safe when it genuinely no longer exists;
    otherwise a partial result would be presented as a complete one.
    """

    def flaky(symbol):
        if symbol == "MSFT":
            raise BourseError("The market data service returned an error (429).")
        return market.Quote(symbol, 100.0, 90.0, f"{symbol} Inc.")

    monkeypatch.setattr(market, "quote", flaky)
    with pytest.raises(BourseError, match="429"):
        market.quotes(["AAPL", "MSFT"])


def test_a_total_failure_is_still_reported(monkeypatch):
    """A wholly failed batch reports the underlying error."""

    def dead(symbol):
        raise BourseError("Can't reach the market data service.")

    monkeypatch.setattr(market, "quote", dead)
    with pytest.raises(BourseError, match="Can't reach"):
        market.quotes(["AAPL", "MSFT"])


def test_an_unpriceable_holding_is_shown_as_unknown_not_as_cost():
    """A holding without a quote is marked unpriced rather than valued at cost."""
    p = Portfolio(
        "t",
        log=[
            {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 10_000},
            {
                "at": "2026-01-01T15:00:01Z",
                "type": "buy",
                "symbol": "DEAD",
                "shares": 10,
                "price": 50,
            },
        ],
    )
    snap = analytics.snapshot(p, {})  # no quote available for DEAD
    assert snap.holdings[0].priced is False


# -- damaged files ---------------------------------------------------------


@pytest.mark.parametrize("text", ['{"name":"x","log":[{"at":"2026', "not json", ""])
def test_a_damaged_file_says_so_instead_of_raising_a_traceback(text, isolated_home):
    (isolated_home / "broken.json").write_text(text)
    with pytest.raises(BourseError, match="damaged"):
        load("broken")


# -- accounting invariants ------------------------------------------------


def test_the_books_balance_after_any_sequence_of_events():
    """Cash plus cost basis must always equal deposits plus realized profit.

    This identity holds after every valid action; a breach means money has been
    created or destroyed.
    """
    import random

    random.seed(7)
    for trial in range(60):
        p = Portfolio(f"f{trial}")
        p.deposit(round(random.uniform(1_000, 50_000), 2))
        for _ in range(random.randint(1, 25)):
            action, sym = random.random(), random.choice(["AAA", "BBB", "CCC"])
            price = round(random.uniform(0.5, 900), 4)
            try:
                if action < 0.45:
                    p.buy(sym, round(random.uniform(1, p.cash + 1), 2), price=price)
                elif action < 0.8:
                    held = p.shares.get(sym, 0)
                    if held > DUST:
                        p.sell(
                            sym, shares=round(held * random.uniform(0.01, 1), 8), price=price
                        )
                elif action < 0.9:
                    p.deposit(round(random.uniform(1, 5_000), 2))
                elif p.cash > 1:
                    p.withdraw(round(random.uniform(1, p.cash), 2))
            except BourseError:
                pass  # a rejected order is valid behaviour

            assert p.cash + sum(p.cost.values()) == pytest.approx(
                p.deposited + sum(p.realized.values()), abs=1e-6
            )
            assert p.cash >= -1e-6
            assert all(n >= -DUST for n in p.shares.values())


def test_re_buying_a_symbol_after_closing_it_starts_a_fresh_average():
    p = Portfolio("t")
    p.deposit(10_000)
    p.buy("AAA", shares=10, price=100)
    p.sell("AAA", all=True, price=150)  # banks 500
    p.buy("AAA", shares=5, price=200)
    assert p.average_cost("AAA") == pytest.approx(200)
    assert p.realized["AAA"] == pytest.approx(500)


# -- returns --------------------------------------------------------------


def _days(n=10):
    from datetime import date

    return [date(2026, 1, d) for d in range(1, n + 1)]


def test_a_flat_market_returns_zero_however_much_money_moves_in_and_out():
    """With no price movement, cash flows alone cannot produce a return."""
    days = _days()
    flat = {"AAA": {d: 100.0 for d in days}}
    log = [
        {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 1_000},
        {
            "at": "2026-01-01T15:00:01Z",
            "type": "buy",
            "symbol": "AAA",
            "shares": 5,
            "price": 100,
        },
    ]
    for i, day in enumerate(days[2:8], start=3):
        log.append(
            {
                "at": f"2026-01-{day.day:02d}T15:00:00Z",
                "type": "deposit" if i % 2 else "withdraw",
                "amount": 200,
            }
        )

    perf = analytics.performance(log, flat, days)
    assert perf.return_pct == pytest.approx(0, abs=1e-9)
    assert perf.gain == pytest.approx(0, abs=1e-9)


def test_a_fully_invested_portfolio_returns_exactly_what_the_stock_did():
    days = _days()
    rising = {"AAA": {d: 100.0 * (1.02**i) for i, d in enumerate(days)}}
    log = [
        {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 1_000},
        {
            "at": "2026-01-01T15:00:01Z",
            "type": "buy",
            "symbol": "AAA",
            "shares": 10,
            "price": 100,
        },
    ]
    perf = analytics.performance(log, rising, days)
    stock_did = (rising["AAA"][days[-1]] / rising["AAA"][days[0]] - 1) * 100
    assert perf.return_pct == pytest.approx(stock_did)


# -- storage --------------------------------------------------------------


def test_state_does_not_drift_across_repeated_save_and_load(isolated_home):
    p = create("rt", cash=12_345.67)
    p.buy("AAA", 1_234.56, price=78.91)
    p.sell("AAA", shares=p.shares["AAA"] / 3, price=80.12)
    before = (p.cash, dict(p.shares), dict(p.cost), dict(p.realized))

    for _ in range(20):
        p = load("rt")
        p.save()

    assert (p.cash, dict(p.shares), dict(p.cost), dict(p.realized)) == before


def test_an_out_of_order_log_is_sorted_on_load(isolated_home):
    """Entries are ordered on load, since valuation walks the log forwards.

    The file is intended to be readable and editable by hand.
    """
    (isolated_home / "jumbled.json").write_text(
        json.dumps(
            {
                "name": "jumbled",
                "log": [
                    {
                        "at": "2026-01-03T15:00:00Z",
                        "type": "sell",
                        "symbol": "X",
                        "shares": 5,
                        "price": 120,
                    },
                    {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 1_000},
                    {
                        "at": "2026-01-02T15:00:00Z",
                        "type": "buy",
                        "symbol": "X",
                        "shares": 5,
                        "price": 100,
                    },
                ],
            }
        )
    )
    p = load("jumbled")
    assert [e["type"] for e in p.log] == ["deposit", "buy", "sell"]
    assert p.cash == pytest.approx(1_000 - 500 + 600)
    assert p.realized["X"] == pytest.approx(100)


def test_selling_out_today_with_no_quote_is_not_a_windfall():
    """Closing a position without a quote reports no gain.

    Nothing remains to price, so no movement can be established for it.
    """
    p = Portfolio(
        "t",
        log=[
            {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 10_000},
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
                "price": 105,
            },
        ],
    )
    s = analytics.snapshot(p, {}, session=date(2026, 1, 2))
    assert s.day_change == pytest.approx(0)
    assert s.total == pytest.approx(10_050)


def test_a_holding_with_no_price_history_is_held_at_cost_not_dropped():
    """A holding with no price history is valued at cost, not omitted."""
    days = [date(2026, 1, 1), date(2026, 1, 2)]
    log = [
        {"at": "2026-01-01T15:00:00Z", "type": "deposit", "amount": 2_000},
        {
            "at": "2026-01-01T15:00:01Z",
            "type": "buy",
            "symbol": "GOOD",
            "shares": 10,
            "price": 100,
        },
        {
            "at": "2026-01-01T15:00:02Z",
            "type": "buy",
            "symbol": "DEAD",
            "shares": 10,
            "price": 100,
        },
    ]
    closes = {"GOOD": {days[0]: 100.0, days[1]: 100.0}}  # nothing at all for DEAD
    assert analytics.value_series(log, closes, days) == pytest.approx([2_000, 2_000])


def test_a_portfolio_cannot_start_with_negative_cash():
    with pytest.raises(BourseError, match="less than nothing"):
        create("bad", -500)
