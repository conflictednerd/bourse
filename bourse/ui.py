"""Rendering.

Tables and panels shared by the command line and the dashboard. Everything here
takes finished analytics results and returns renderables; nothing is computed.
"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text

from .clock import moment
from .format import money, percent, settled, shares, signed_money

GAIN = "green"
LOSS = "red"
FLAT = "dim"
LABEL = "dim"
SYMBOL = "bold cyan"
DASH = Text("—", style=FLAT)  #: shown where a figure is genuinely unknown


def tone(value: float) -> str:
    """The colour for a gain, a loss, or neither."""
    value = settled(value)
    return GAIN if value > 0 else LOSS if value < 0 else FLAT


def delta(amount: float, pct: float | None = None) -> Text:
    """A change, coloured by direction, optionally with its percentage."""
    text = signed_money(amount)
    if pct is not None:
        text += f"  ({percent(pct)})"
    return Text(text, style=tone(amount))


def when(stamp: str) -> str:
    """A recorded timestamp in local time, for display."""
    try:
        return moment(stamp).astimezone().strftime("%d %b %Y  %H:%M")
    except ValueError:
        return stamp


def summary(snapshot, name: str, offline: bool = False) -> Table:
    """A portfolio's headline figures."""
    grid = Table.grid(padding=(0, 3))
    grid.add_column(justify="left")
    grid.add_column(justify="right")
    grid.add_column(justify="left")
    grid.add_column(justify="right")

    if offline:
        # Without prices, only figures the log alone establishes are shown.
        grid.add_row(
            Text("Cash", style=LABEL),
            Text(money(snapshot.cash), style="bold"),
            Text("Deposited", style=LABEL),
            Text(money(snapshot.deposited), style=FLAT),
        )
        grid.add_row(
            Text("Holdings at cost", style=LABEL),
            Text(money(snapshot.holdings_value)),
            Text("Banked so far", style=LABEL),
            delta(snapshot.banked),
        )
        return grid

    grid.add_row(
        Text("Total value", style=LABEL),
        Text(money(snapshot.total), style="bold"),
        Text("Today", style=LABEL),
        delta(snapshot.day_change, snapshot.day_change_pct),
    )
    grid.add_row(
        Text("Cash", style=LABEL),
        Text(money(snapshot.cash)),
        Text("All time", style=LABEL),
        delta(snapshot.gain, snapshot.gain_pct),
    )
    grid.add_row(
        Text("Holdings", style=LABEL),
        Text(money(snapshot.holdings_value)),
        Text("Deposited", style=LABEL),
        Text(money(snapshot.deposited), style=FLAT),
    )
    return grid


def holdings(snapshot, offline: bool = False) -> Table:
    """One row per holding. Columns requiring a price are blank without one."""
    table = Table(
        box=None,
        pad_edge=False,
        header_style=LABEL,
        expand=False,
        padding=(0, 1),
    )
    table.add_column("SYMBOL", style=SYMBOL, no_wrap=True)
    table.add_column("NAME", style=FLAT, max_width=18, no_wrap=True, overflow="ellipsis")
    table.add_column("SHARES", justify="right", no_wrap=True)
    table.add_column("COST", justify="right", no_wrap=True)
    table.add_column("PRICE", justify="right", no_wrap=True)
    table.add_column("VALUE", justify="right", no_wrap=True)
    table.add_column("WT", justify="right", style=FLAT, no_wrap=True)
    table.add_column("TODAY", justify="right", no_wrap=True)
    table.add_column("GAIN", justify="right", no_wrap=True)
    table.add_column("", justify="right", no_wrap=True)

    for holding in snapshot.holdings:
        unknown = offline or not holding.priced
        table.add_row(
            holding.symbol,
            holding.name,
            shares(holding.shares),
            money(holding.average_cost),
            DASH if unknown else Text(money(holding.price)),
            DASH if unknown else Text(money(holding.value)),
            DASH if unknown else Text(f"{holding.weight_pct:.0f}%"),
            DASH
            if unknown
            else Text(percent(holding.day_change_pct), style=tone(holding.day_change)),
            DASH
            if unknown
            else Text(signed_money(holding.unrealized), style=tone(holding.unrealized)),
            DASH
            if unknown
            else Text(percent(holding.unrealized_pct), style=tone(holding.unrealized)),
        )
    return table


def positions(details: list) -> Table:
    """Every company traded, open or closed.

    Shows the current value of each position, the profit already realized on it,
    and the two combined.
    """
    table = Table(box=None, pad_edge=False, header_style=LABEL, padding=(0, 1))
    table.add_column("SYMBOL", style=SYMBOL, no_wrap=True)
    table.add_column("NAME", style=FLAT, max_width=20, no_wrap=True, overflow="ellipsis")
    table.add_column("SHARES", justify="right", no_wrap=True)
    table.add_column("AVG COST", justify="right", no_wrap=True)
    table.add_column("PRICE", justify="right", no_wrap=True)
    table.add_column("VALUE", justify="right", no_wrap=True)
    table.add_column("UNREALIZED", justify="right", no_wrap=True)
    table.add_column("BANKED", justify="right", no_wrap=True)
    table.add_column("TOTAL", justify="right", no_wrap=True)

    for detail in details:
        closed = detail.shares <= 0
        unknown = closed or not detail.priced
        table.add_row(
            detail.symbol,
            detail.name,
            Text("closed", style=FLAT) if closed else Text(shares(detail.shares)),
            DASH if closed else Text(money(detail.average_cost)),
            DASH if not detail.priced else Text(money(detail.price)),
            DASH if unknown else Text(money(detail.value)),
            DASH
            if unknown
            else Text(signed_money(detail.unrealized), style=tone(detail.unrealized)),
            Text(signed_money(detail.realized), style=tone(detail.realized))
            if detail.realized
            else DASH,
            Text(signed_money(detail.total), style=f"bold {tone(detail.total)}"),
        )
    return table


def trades(log: list[dict], limit: int | None = None) -> Table:
    """A portfolio's log, most recent first."""
    table = Table(box=None, pad_edge=False, header_style=LABEL, padding=(0, 2))
    table.add_column("WHEN", style=FLAT, no_wrap=True)
    table.add_column("ACTION", no_wrap=True)
    table.add_column("SYMBOL", style=SYMBOL, no_wrap=True)
    table.add_column("SHARES", justify="right")
    table.add_column("PRICE", justify="right")
    table.add_column("AMOUNT", justify="right")
    table.add_column("BY", style=FLAT, no_wrap=True)

    entries = list(reversed(log))
    if limit:
        entries = entries[:limit]

    styles = {"buy": GAIN, "sell": LOSS, "deposit": "cyan", "withdraw": "yellow"}
    for entry in entries:
        kind = entry.get("type", "?")
        if kind in ("buy", "sell"):
            n = entry.get("shares", 0.0)
            price = entry.get("price", 0.0)
            table.add_row(
                when(entry.get("at", "")),
                Text(kind.upper(), style=styles.get(kind, "")),
                entry.get("symbol", ""),
                shares(n),
                money(price),
                money(n * price),
                entry.get("by", ""),
            )
        else:
            table.add_row(
                when(entry.get("at", "")),
                Text(kind.upper(), style=styles.get(kind, "")),
                "",
                "",
                "",
                money(entry.get("amount", 0.0)),
                entry.get("note", ""),
            )
    return table


def stock(detail) -> Table:
    """One company: the position held and the profit already realized."""
    grid = Table.grid(padding=(0, 3))
    grid.add_column(justify="left")
    grid.add_column(justify="right")

    if detail.shares > 0:
        grid.add_row(Text("Shares held", style=LABEL), Text(shares(detail.shares)))
        grid.add_row(Text("Average cost", style=LABEL), Text(money(detail.average_cost)))
        grid.add_row(Text("Price now", style=LABEL), Text(money(detail.price)))
        grid.add_row(Text("Market value", style=LABEL), Text(money(detail.value), style="bold"))
        grid.add_row(Text("Unrealized", style=LABEL), delta(detail.unrealized))
    else:
        grid.add_row(Text("Shares held", style=LABEL), Text("none", style=FLAT))

    if detail.realized:
        grid.add_row(Text("Already banked", style=LABEL), delta(detail.realized))
    grid.add_row(
        Text("Total on this stock", style="bold"),
        Text(signed_money(detail.total), style=f"bold {tone(detail.total)}"),
    )
    return grid


#: The command reference, grouped by what each command is for. Adding a command
#: means adding a line here; :mod:`tests.test_cli` checks none is left out.
GUIDE = [
    (
        "Portfolios",
        [
            ("bourse new <name> [--cash 10000]", "start a portfolio"),
            ("bourse ls", "list your portfolios"),
            ("bourse use <name>", "choose the one commands act on"),
        ],
    ),
    (
        "Trading",
        [
            ("bourse buy <symbol> <dollars>", "spend that many dollars"),
            ("bourse buy <symbol> --shares <n>", "buy a share count instead"),
            ("bourse sell <symbol> <dollars>", "sell that much of it"),
            ("bourse sell <symbol> --all", "close the position"),
        ],
    ),
    (
        "Cash",
        [
            ("bourse deposit <amount>", "add cash"),
            ("bourse withdraw <amount>", "take cash out"),
        ],
    ),
    (
        "Looking",
        [
            ("bourse show", "cash, holdings and today's change"),
            ("bourse analyze [name]", "dashboard: charts, holdings, history"),
            ("bourse quote <symbol>", "look up a price"),
            ("bourse search <text>", "find a ticker by company name"),
        ],
    ),
    (
        "On any command",
        [
            ("-p, --portfolio <name>", "act on another portfolio just this once"),
            ("-y, --yes", "skip the confirmation prompt"),
            ("--note <text>", "record why, on a deposit or withdrawal"),
        ],
    ),
]


def guide() -> Table:
    """Every command and its syntax."""
    grid = Table.grid(padding=(0, 3))
    grid.add_column()
    grid.add_column()

    for heading, lines in GUIDE:
        grid.add_row(Text(heading, style="bold"), "")
        for syntax, meaning in lines:
            grid.add_row(Text(f"  {syntax}", style=SYMBOL), Text(meaning, style=LABEL))
        grid.add_row("", "")

    grid.add_row(
        Text("  bourse help <command>", style=SYMBOL),
        Text("every option for one command", style=LABEL),
    )
    return grid


def performance(perf) -> Table:
    """A period's result."""
    grid = Table.grid(padding=(0, 3))
    grid.add_column(justify="left")
    grid.add_column(justify="right")
    grid.add_column(justify="left")
    grid.add_column(justify="right")

    grid.add_row(
        Text("Gain", style=LABEL),
        Text(signed_money(perf.gain), style=f"bold {tone(perf.gain)}"),
        Text("Return", style=LABEL),
        Text(percent(perf.return_pct), style=f"bold {tone(perf.return_pct)}"),
    )
    benchmark = (
        Text(percent(perf.benchmark_pct), style=tone(perf.benchmark_pct))
        if perf.benchmark_pct is not None
        else Text("n/a", style=FLAT)
    )
    grid.add_row(
        Text("Value now", style=LABEL),
        Text(money(perf.end_value)),
        Text("S&P 500", style=LABEL),
        benchmark,
    )
    grid.add_row(
        Text("Value then", style=LABEL),
        Text(money(perf.start_value), style=FLAT),
        Text("Deposited since", style=LABEL),
        Text(money(perf.deposited), style=FLAT),
    )
    return grid
