"""The command line.

Commands parse their arguments, ask the core for figures and hand them to
:mod:`bourse.ui` to be drawn. No calculation happens here.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.padding import Padding
from rich.prompt import Confirm
from rich.text import Text
from typer.core import TyperGroup

from . import analytics, market, ui
from . import portfolio as store
from .format import money
from .format import shares as show_shares
from .portfolio import BourseError

console = Console()


class _Bourse(TyperGroup):
    """Report :class:`BourseError` as a plain message rather than a traceback."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except BourseError as error:
            console.print(f"\n  [red]{error}[/red]\n")
            raise SystemExit(1) from None


app = typer.Typer(
    cls=_Bourse,
    add_completion=False,
    help="Paper-trade the S&P 500. Real prices, imaginary money.",
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def _global(
    ctx: typer.Context,
    portfolio: str = typer.Option(
        None, "-p", "--portfolio", help="Which portfolio to act on.", show_default=False
    ),
):
    ctx.obj = portfolio
    if ctx.invoked_subcommand is None:
        _guide()


def _name(ctx: typer.Context) -> str:
    name = ctx.obj or store.current()
    if not name:
        raise BourseError(
            "No portfolio selected. Create one with 'bourse new mine', "
            "or pick an existing one with 'bourse use <name>'."
        )
    return name


def _load(ctx: typer.Context):
    return store.load(_name(ctx))


def _confirm(name: str, headline: Text, details: list[Text], yes: bool) -> bool:
    """Describe a pending action and ask for confirmation."""
    console.print()
    console.print(
        Text("  [", style="dim")
        + Text(name, style="bold")
        + Text("]  ", style="dim")
        + headline
    )
    for line in details:
        console.print(Text("       ") + line)
    console.print()
    if yes:
        return True
    answer = Confirm.ask("  Confirm?", default=False, console=console)
    console.print()
    return answer


def _guide() -> None:
    console.print()
    console.print("  [bold]bourse[/bold] [dim]— paper-trade the S&P 500.[/dim]")
    console.print("  [dim]Amounts are dollars unless you ask for shares.[/dim]\n")
    console.print(Padding(ui.guide(), (0, 2)))
    console.print()


def _done(message: str) -> None:
    console.print(f"  [green]✓[/green] {message}\n")


def _cancelled() -> None:
    console.print("  [dim]Cancelled. Nothing changed.[/dim]\n")


# -- portfolios -----------------------------------------------------------


@app.command()
def new(
    name: str = typer.Argument(..., help="A name for the new portfolio."),
    cash: float = typer.Option(10_000, "--cash", help="Opening balance."),
):
    """Start a new portfolio."""
    store.create(name, cash)
    store.use(name)
    console.print()
    _done(f"Created [bold]{name}[/bold] with {money(cash)} and switched to it.")


@app.command("ls")
def list_portfolios():
    """List your portfolios."""
    names = store.names()
    if not names:
        console.print("\n  [dim]No portfolios yet. Try: bourse new mine[/dim]\n")
        return
    active = store.current()
    console.print()
    for name in names:
        marker = "[green]●[/green]" if name == active else " "
        console.print(f"  {marker} {name}")
    console.print()


@app.command()
def use(name: str = typer.Argument(..., help="The portfolio to switch to.")):
    """Select the portfolio that commands act on."""
    store.load(name)
    store.use(name)
    console.print()
    _done(f"Now using [bold]{name}[/bold].")
    console.print(f"  [dim]For one shell only, use: export BOURSE_PORTFOLIO={name}[/dim]\n")


# -- money in and out -----------------------------------------------------


@app.command()
def deposit(
    ctx: typer.Context,
    amount: float = typer.Argument(..., help="How much to add."),
    note: str = typer.Option("", "--note", help="What it was for."),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip the confirmation."),
):
    """Add cash to a portfolio."""
    p = _load(ctx)
    headline = Text(f"DEPOSIT {money(amount)}", style="cyan")
    details = [Text(f"Cash  {money(p.cash)} → {money(p.cash + amount)}", style="dim")]
    if not _confirm(p.name, headline, details, yes):
        return _cancelled()
    p.deposit(amount, note)
    _done(f"Deposited {money(amount)}. Cash is now {money(p.cash)}.")


@app.command()
def withdraw(
    ctx: typer.Context,
    amount: float = typer.Argument(..., help="How much to take out."),
    note: str = typer.Option("", "--note", help="What it was for."),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip the confirmation."),
):
    """Take cash out of a portfolio."""
    p = _load(ctx)
    p.require_cash(amount, "withdraw")
    headline = Text(f"WITHDRAW {money(amount)}", style="yellow")
    details = [Text(f"Cash  {money(p.cash)} → {money(p.cash - amount)}", style="dim")]
    if not _confirm(p.name, headline, details, yes):
        return _cancelled()
    p.withdraw(amount, note)
    _done(f"Withdrew {money(amount)}. Cash is now {money(p.cash)}.")


# -- trading --------------------------------------------------------------


@app.command()
def buy(
    ctx: typer.Context,
    symbol: str = typer.Argument(..., help="Ticker, e.g. AAPL."),
    amount: float = typer.Argument(None, help="How many dollars to spend."),
    shares: float = typer.Option(None, "--shares", help="Buy a share count instead."),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip the confirmation."),
):
    """Buy a stock. [dim]bourse buy AAPL 500[/dim] spends $500 on Apple."""
    p = _load(ctx)
    if amount is None and shares is None:
        raise BourseError(
            "Say how much to buy: 'bourse buy AAPL 500' for $500, "
            "or 'bourse buy AAPL --shares 3' for three shares."
        )
    if amount is not None and shares is not None:
        raise BourseError(
            f"Buy {money(amount)} worth or {show_shares(shares)} shares, not both."
        )

    q = market.quote(symbol)
    count = shares if shares is not None else amount / q.price
    outlay = count * q.price
    p.require_cash(outlay)

    headline = Text(f"BUY {money(outlay)} of {q.symbol}", style=ui.GAIN)
    details = [
        Text(f"{q.name} at {money(q.price)} → {show_shares(count)} shares", style="dim"),
        Text(f"Cash  {money(p.cash)} → {money(p.cash - outlay)}", style="dim"),
    ]
    if not _confirm(p.name, headline, details, yes):
        return _cancelled()

    p.buy(q.symbol, shares=count, price=q.price)
    _done(
        f"Bought {show_shares(count)} shares of {q.symbol} for {money(outlay)}. "
        f"Cash is now {money(p.cash)}."
    )


@app.command()
def sell(
    ctx: typer.Context,
    symbol: str = typer.Argument(..., help="Ticker, e.g. AAPL."),
    amount: float = typer.Argument(None, help="How many dollars' worth to sell."),
    shares: float = typer.Option(None, "--shares", help="Sell a share count instead."),
    every: bool = typer.Option(False, "--all", help="Sell the whole position."),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip the confirmation."),
):
    """Sell a stock. [dim]bourse sell AAPL --all[/dim] closes the position."""
    p = _load(ctx)
    symbol = symbol.upper()
    p.require_shares(symbol, 0.0)
    held = p.shares[symbol]
    if amount is None and shares is None and not every:
        raise BourseError(
            "Say how much to sell: 'bourse sell AAPL 500' for $500 worth, "
            "'--shares 3' for three shares, or '--all' for the lot."
        )
    if sum(x is not None for x in (amount, shares)) + bool(every) > 1:
        raise BourseError(
            "Say how much to sell just one way: a dollar amount, --shares, or --all."
        )

    q = market.quote(symbol)
    if every:
        count = held
    elif shares is not None:
        count = shares
    else:
        count = amount / q.price
    if not every:
        p.require_shares(symbol, count)
    proceeds = count * q.price
    average = p.average_cost(symbol)
    profit = (q.price - average) * count

    headline = Text(
        f"SELL {show_shares(count)} {q.symbol} for {money(proceeds)}", style=ui.LOSS
    )
    details = [
        Text(f"{q.name} at {money(q.price)}, bought at {money(average)}", style="dim"),
        Text("Banks ", style="dim") + ui.delta(profit),
        Text(f"Cash  {money(p.cash)} → {money(p.cash + proceeds)}", style="dim"),
    ]
    if not _confirm(p.name, headline, details, yes):
        return _cancelled()

    p.sell(q.symbol, shares=None if every else count, all=every, price=q.price)
    _done(
        f"Sold {show_shares(count)} shares of {q.symbol} for {money(proceeds)}. "
        f"Cash is now {money(p.cash)}."
    )


# -- looking ---------------------------------------------------------------


@app.command()
def show(ctx: typer.Context):
    """Show a portfolio's current position."""
    p = _load(ctx)
    offline = ""
    try:
        quotes = market.quotes(list(p.shares)) if p.shares else {}
    except BourseError as error:
        # Without prices the log still establishes holdings, cost and realized
        # profit, so the command degrades rather than failing.
        quotes, offline = {}, str(error)
    snap = analytics.snapshot(p, quotes)

    console.print()
    console.print(Text(f"  {p.name}", style="bold"))
    if offline:
        console.print(f"  [yellow]{offline}[/yellow]")
    console.print()
    console.print(Padding(ui.summary(snap, p.name, offline=bool(offline)), (0, 2)))
    console.print()

    if snap.holdings:
        console.print(Padding(ui.holdings(snap, offline=bool(offline)), (0, 2)))
    else:
        console.print("  [dim]Nothing held yet. Try: bourse buy AAPL 500[/dim]")
    console.print()
    console.print(f"  [dim]bourse analyze {p.name}  for charts and history[/dim]\n")


@app.command()
def quote(symbol: str = typer.Argument(..., help="Ticker to look up.")):
    """Look up the current price of a stock."""
    q = market.quote(symbol)
    change = q.price - q.previous_close
    percent = (q.price / q.previous_close - 1) * 100 if q.previous_close else 0.0

    console.print()
    console.print(Text(f"  {q.symbol}  ", style=ui.SYMBOL) + Text(q.name, style="dim"))
    console.print(Text(f"  {money(q.price)}   ") + ui.delta(change, percent))
    console.print(f"  [dim]previous close {money(q.previous_close)}[/dim]\n")


@app.command()
def search(text: str = typer.Argument(..., help="Part of a company name or ticker.")):
    """Search the S&P 500 by ticker or company name."""
    from .symbols import SP500

    needle = text.lower()
    hits = [(s, n) for s, n in SP500.items() if needle in s.lower() or needle in n.lower()]

    console.print()
    if not hits:
        console.print(f"  [dim]Nothing in the S&P 500 matches '{text}'.[/dim]\n")
        return
    for sym, name in hits[:25]:
        console.print(f"  [bold cyan]{sym:<6}[/bold cyan] [dim]{name}[/dim]")
    if len(hits) > 25:
        console.print(f"  [dim]... and {len(hits) - 25} more[/dim]")
    console.print()


@app.command()
def analyze(
    ctx: typer.Context,
    name: str = typer.Argument(None, help="Portfolio to open. Defaults to the current one."),
):
    """Open the dashboard."""
    from .tui import Dashboard

    Dashboard(name or _name(ctx)).run()


@app.command("help")
def explain(
    ctx: typer.Context,
    command: str = typer.Argument(None, help="A command to explain in full."),
):
    """List the commands, or explain one of them in full."""
    if command is None:
        return _guide()
    found = ctx.parent.command.get_command(ctx, command)
    if found is None:
        raise BourseError(f"There is no '{command}' command. Run 'bourse help' for the list.")
    # Asking the command to print its own help keeps this in step with its real
    # arguments, so a command can never be documented as something it is not.
    found.main(args=["--help"], prog_name=f"bourse {command}", standalone_mode=False)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
