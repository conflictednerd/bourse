"""The dashboard.

Charts, holdings, history and performance. Figures come from
:mod:`bourse.analytics` and are rendered by :mod:`bourse.ui`; this module
arranges them on screen and decides what to fetch.

A new panel is one ``TabPane`` in :meth:`Dashboard.compose` and one
corresponding ``_draw_`` method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from rich.console import Group
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Static, TabbedContent, TabPane
from textual_plotext import PlotextPlot

from . import analytics, clock, market, ui
from . import portfolio as store
from .portfolio import BourseError

BENCHMARK = "SPY"  #: an S&P 500 tracker, for comparison against the index

PERIODS = [("1W", 7), ("1M", 30), ("3M", 91), ("YTD", None), ("1Y", 365), ("All", None)]


@dataclass
class Loaded:
    """Everything the screen needs, fetched once when the dashboard opens."""

    portfolio: object
    snapshot: object
    quotes: dict = field(default_factory=dict)
    closes: dict = field(default_factory=dict)
    benchmark: dict = field(default_factory=dict)
    first_day: date = None
    error: str = ""


class Dashboard(App):
    TITLE = "bourse"
    CSS = """
    Screen { background: $surface; }
    #summary, #perf-stats { padding: 1 2; height: auto; }
    .panel { padding: 1 2; height: auto; }
    PlotextPlot { height: 1fr; min-height: 12; }
    #periods { height: auto; padding: 0 2; }
    #periods Button { min-width: 8; margin-right: 1; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "reload", "Refresh"),
    ]

    def __init__(self, name: str):
        super().__init__()
        self.portfolio_name = name
        self.sub_title = name
        self.data: Loaded | None = None
        self.period = "All"

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="tab-overview"):
            with TabPane("Overview", id="tab-overview"):
                yield Static(id="summary")
                yield PlotextPlot(id="value-chart")
            with TabPane("Holdings", id="tab-holdings"):
                yield VerticalScroll(Static(id="positions", classes="panel"))
            with TabPane("History", id="tab-history"):
                yield VerticalScroll(Static(id="history", classes="panel"))
            with TabPane("Performance", id="tab-performance"):
                with Horizontal(id="periods"):
                    for label, _ in PERIODS:
                        yield Button(label, id=f"period-{label}", variant="default")
                yield Static(id="perf-stats")
                yield PlotextPlot(id="perf-chart")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#summary", Static).update("  Loading prices…")
        self._fetch()

    def action_reload(self) -> None:
        self.notify("Fetching fresh prices…", timeout=3)
        self._fetch()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("period-"):
            self.period = event.button.id.removeprefix("period-")
            self._draw_performance()

    # -- fetching ---------------------------------------------------------

    def _fetch(self) -> None:
        self.run_worker(self._load, thread=True, exclusive=True)

    def _load(self) -> None:
        """Fetch everything the dashboard needs, off the UI thread."""
        try:
            portfolio = store.load(self.portfolio_name)
        except BourseError as error:
            self.call_from_thread(self._fail, str(error))
            return

        symbols = analytics.traded_symbols(portfolio.log)
        log = portfolio.log
        first_day = clock.day(log[0]) if log else clock.today()

        data = Loaded(portfolio=portfolio, snapshot=None, first_day=first_day)
        try:
            data.quotes = market.quotes(symbols) if symbols else {}
            closes = market.series(symbols + [BENCHMARK], first_day, clock.today())
            data.closes = {s: c for s, c in closes.items() if s != BENCHMARK}
            data.benchmark = closes.get(BENCHMARK, {})
        except BourseError as error:
            # Only live prices are lost; the log still supplies the history.
            data.error = str(error)
        data.snapshot = analytics.snapshot(portfolio, data.quotes)

        self.call_from_thread(self._draw, data)

    # -- drawing ----------------------------------------------------------

    def _fail(self, message: str) -> None:
        self.query_one("#summary", Static).update(Text(message, style="red"))
        self.notify(message, severity="error", timeout=10)

    def _draw(self, data: Loaded) -> None:
        self.data = data
        summary = ui.summary(data.snapshot, data.portfolio.name, offline=bool(data.error))
        if data.error:
            self.notify(data.error, severity="warning", timeout=10)
            summary = Group(Text(data.error, style="yellow"), Text(), summary)
        self.query_one("#summary", Static).update(summary)
        self._draw_positions()
        self.query_one("#history", Static).update(ui.trades(data.portfolio.log))
        self._draw_value_chart()
        self._draw_performance()

    def _draw_positions(self) -> None:
        portfolio = self.data.portfolio
        details = [
            analytics.stock(portfolio, symbol, self.data.quotes)
            for symbol in analytics.traded_symbols(portfolio.log)
        ]
        details.sort(key=lambda d: (d.shares > 0, d.value, d.total), reverse=True)
        self.query_one("#positions", Static).update(
            ui.positions(details) if details else "Nothing traded yet."
        )

    def _window(self) -> list[date]:
        """The trading days covered by the selected period."""
        today = clock.today()
        if self.period == "All":
            start = self.data.first_day
        elif self.period == "YTD":
            start = date(today.year, 1, 1)
        else:
            days = dict(PERIODS)[self.period]
            start = today - timedelta(days=days)
        start = max(start, self.data.first_day)
        return analytics.trading_days(self.data.closes, start, today)

    def _draw_value_chart(self) -> None:
        days = analytics.trading_days(self.data.closes, self.data.first_day, clock.today())
        values = (
            analytics.value_series(self.data.portfolio.log, self.data.closes, days)
            if days
            else []
        )
        self._plot("#value-chart", days, values, "Portfolio value")

    def _draw_performance(self) -> None:
        if not self.data:
            return
        stats = self.query_one("#perf-stats", Static)
        days = self._window()
        if len(days) < 2:
            stats.update("Not enough trading days in this period yet.")
            self._plot("#perf-chart", [], [], "")
            return

        perf = analytics.performance(
            self.data.portfolio.log, self.data.closes, days, self.data.benchmark
        )
        stats.update(ui.performance(perf))

        # The index is rebased to the portfolio's starting value so the two
        # lines are directly comparable.
        index = None
        if self.data.benchmark and perf.start_value > 0:
            base = analytics.close_on(self.data.benchmark, days[0])
            if base:
                index = [
                    perf.start_value
                    * (analytics.close_on(self.data.benchmark, d) or base)
                    / base
                    for d in days
                ]
        self._plot(
            "#perf-chart",
            days,
            perf.values,
            f"Portfolio value — {self.period}",
            index=index,
        )

    def _plot(
        self,
        selector: str,
        days: list[date],
        values: list[float],
        title: str,
        index: list[float] | None = None,
    ) -> None:
        widget = self.query_one(selector, PlotextPlot)
        plt = widget.plt
        plt.clear_figure()

        if len(days) < 2:
            plt.title("Not enough history yet")
            widget.refresh()
            return

        steps = list(range(len(days)))
        rising = values[-1] >= values[0]
        if index:
            plt.plot(steps, index, color="gray", label=f"{BENCHMARK} (same money)")
        plt.plot(steps, values, color="green" if rising else "red", label="You")

        marks = max(1, len(days) // 6)
        ticks = steps[::marks]
        plt.xticks(ticks, [days[i].strftime("%d %b") for i in ticks])
        plt.title(title)
        widget.refresh()
