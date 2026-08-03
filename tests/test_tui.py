"""The dashboard, driven headless against stubbed prices.

Covers the failures that only appear once widgets are laid out and drawn:
missing identifiers, invalid styling, and charts given no data.
"""

from datetime import date, timedelta

import pytest
from textual.widgets import Static

from bourse import market
from bourse.portfolio import create
from bourse.tui import Dashboard


@pytest.fixture
def _offline(monkeypatch):
    """Stub market data with a steadily rising series."""
    today = date.today()
    days = [today - timedelta(days=n) for n in range(40, -1, -1)]

    def fake_quotes(symbols):
        return {s: market.Quote(s, 120.0, 118.0, f"{s} Inc.") for s in symbols}

    def fake_series(symbols, start, end):
        return {
            s: {d: 100.0 + i for i, d in enumerate(days) if start <= d <= end} for s in symbols
        }

    monkeypatch.setattr(market, "quotes", fake_quotes)
    monkeypatch.setattr(market, "series", fake_series)


def _portfolio_with_history():
    old = (date.today() - timedelta(days=30)).isoformat()
    p = create("demo", cash=10_000)
    p.log[:] = [
        {"at": f"{old}T15:00:00Z", "type": "deposit", "amount": 10_000},
        {"at": f"{old}T15:00:01Z", "type": "buy", "symbol": "AAPL", "shares": 20, "price": 100},
        {"at": f"{old}T15:00:02Z", "type": "buy", "symbol": "MSFT", "shares": 10, "price": 200},
        {
            "at": f"{old}T15:00:03Z",
            "type": "sell",
            "symbol": "MSFT",
            "shares": 10,
            "price": 220,
        },
    ]
    p.save()
    return p


async def test_dashboard_draws_every_panel(_offline):
    _portfolio_with_history()
    app = Dashboard("demo")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.data is not None
        assert not app.data.error
        # A closed position and an open one both survive to the holdings table.
        assert app.data.snapshot.holdings[0].symbol == "AAPL"
        assert app.data.portfolio.realized["MSFT"] == pytest.approx(200)

        for tab in ("tab-overview", "tab-holdings", "tab-history", "tab-performance"):
            app.query_one("TabbedContent").active = tab
            await pilot.pause()


async def test_chart_does_not_run_underneath_the_footer(_offline):
    """The chart's bottom row carries its date labels and must stay visible."""
    _portfolio_with_history()
    app = Dashboard("demo")
    async with app.run_test(size=(118, 32)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        chart = app.query_one("#value-chart")
        footer = app.query_one("Footer")
        assert chart.region.bottom <= footer.region.y, (
            f"chart occupies rows up to {chart.region.bottom}, "
            f"but the footer starts at {footer.region.y}"
        )


async def test_period_buttons_redraw_performance(_offline):
    _portfolio_with_history()
    app = Dashboard("demo")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        app.query_one("TabbedContent").active = "tab-performance"
        await pilot.pause()

        for label in ("1W", "1M", "All"):
            await pilot.click(f"#period-{label}")
            await pilot.pause()
            assert app.period == label


async def test_dashboard_still_opens_with_no_network(monkeypatch):
    """Without prices, the dashboard still opens and reports realized profit."""
    from bourse.portfolio import BourseError

    def dead(*args, **kwargs):
        raise BourseError("Can't reach the market data service.")

    monkeypatch.setattr(market, "quotes", dead)
    monkeypatch.setattr(market, "series", dead)
    _portfolio_with_history()

    app = Dashboard("demo")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Can't reach" in app.data.error
        # The banked profit on Microsoft is in the log, so it survives.
        assert app.data.portfolio.realized["MSFT"] == pytest.approx(200)


async def test_brand_new_empty_portfolio_does_not_crash(_offline):
    create("fresh", cash=1_000)
    app = Dashboard("fresh")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.data.snapshot.total == pytest.approx(1_000)


async def test_a_portfolio_deleted_while_open_says_so(_offline, isolated_home):
    """A portfolio removed while the dashboard is open reports an error."""
    _portfolio_with_history()
    app = Dashboard("demo")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()

        (isolated_home / "demo.json").unlink()
        await pilot.press("r")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        summary = app.query_one("#summary", Static).render()
        assert "No portfolio called 'demo'" in str(summary)
