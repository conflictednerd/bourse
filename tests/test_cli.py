"""Command behaviour, with market data stubbed out."""

import pytest
from typer.testing import CliRunner

from bourse import market
from bourse.cli import app
from bourse.portfolio import BourseError, create, load, use

runner = CliRunner()


def make(name: str, cash: float = 1_000):
    """Create a portfolio and select it, as ``bourse new`` does."""
    portfolio = create(name, cash)
    use(name)
    return portfolio


@pytest.fixture
def _prices(monkeypatch):
    book = {"AAPL": 100.0, "MSFT": 200.0}

    def fake_quote(symbol):
        symbol = symbol.upper()
        if symbol not in book:
            raise BourseError(f"No such symbol: {symbol}.")
        return market.Quote(symbol, book[symbol], book[symbol] * 0.9, f"{symbol} Inc.")

    monkeypatch.setattr(market, "quote", fake_quote)
    monkeypatch.setattr(market, "quotes", lambda syms: {s: fake_quote(s) for s in syms})
    return book


def test_new_then_buy_then_show(_prices):
    assert runner.invoke(app, ["new", "mine", "--cash", "1000"]).exit_code == 0
    result = runner.invoke(app, ["buy", "AAPL", "400", "-y"])
    assert result.exit_code == 0
    assert load("mine").shares["AAPL"] == pytest.approx(4)

    out = runner.invoke(app, ["show"]).output
    assert "mine" in out and "AAPL" in out


def test_a_declined_confirmation_changes_nothing(_prices):
    make("mine")
    result = runner.invoke(app, ["buy", "AAPL", "400"], input="n\n")
    assert result.exit_code == 0
    assert "Cancelled" in result.output
    assert load("mine").shares == {}


def test_confirmation_names_the_portfolio(_prices):
    make("mine")
    result = runner.invoke(app, ["buy", "AAPL", "400"], input="n\n")
    assert "[mine]" in result.output


def test_buying_more_than_you_can_afford_is_refused_before_confirming(_prices):
    make("mine", 100)
    result = runner.invoke(app, ["buy", "AAPL", "5000", "-y"])
    assert result.exit_code == 1
    assert "can't spend" in result.output
    assert "Confirm" not in result.output
    assert load("mine").log[-1]["type"] == "deposit"


def test_sell_all_closes_the_position(_prices):
    make("mine")
    runner.invoke(app, ["buy", "AAPL", "400", "-y"])
    result = runner.invoke(app, ["sell", "AAPL", "--all", "-y"])
    assert result.exit_code == 0
    assert load("mine").shares == {}


def test_portfolio_flag_beats_the_current_one(_prices):
    make("mine")
    create("other", cash=1_000)
    runner.invoke(app, ["use", "mine"])
    runner.invoke(app, ["-p", "other", "buy", "AAPL", "100", "-y"])
    assert load("other").shares["AAPL"] == pytest.approx(1)
    assert load("mine").shares == {}


def test_commands_need_a_portfolio_to_act_on():
    result = runner.invoke(app, ["show"])
    assert result.exit_code == 1
    assert "No portfolio selected" in result.output


def test_show_still_works_offline(monkeypatch, _prices):
    """Without prices, holdings and cost basis are still reported."""
    make("mine")
    runner.invoke(app, ["buy", "AAPL", "400", "-y"])

    def dead(*args, **kwargs):
        raise BourseError("Can't reach the market data service.")

    monkeypatch.setattr(market, "quotes", dead)

    result = runner.invoke(app, ["show"])
    assert result.exit_code == 0
    assert "Can't reach" in result.output
    assert "Holdings at cost" in result.output
    assert "AAPL" in result.output
    # No invented prices: the value columns are struck through with a dash.
    assert "—" in result.output


def test_unknown_symbol_is_reported_plainly(_prices):
    make("mine")
    result = runner.invoke(app, ["quote", "NOPE"])
    assert result.exit_code == 1
    assert "No such symbol" in result.output


def test_search_finds_a_company_by_name():
    out = runner.invoke(app, ["search", "apple"]).output
    assert "AAPL" in out


def test_conflicting_sizes_are_refused_rather_than_one_being_ignored(_prices):
    """A dollar amount and a share count are mutually exclusive."""
    make("mine", 5_000)
    result = runner.invoke(app, ["buy", "AAPL", "500", "--shares", "3", "-y"])
    assert result.exit_code == 1
    assert "not both" in result.output
    assert load("mine").shares == {}


def test_conflicting_sell_sizes_are_refused(_prices):
    make("mine", 5_000)
    runner.invoke(app, ["buy", "AAPL", "500", "-y"])
    result = runner.invoke(app, ["sell", "AAPL", "100", "--all", "-y"])
    assert result.exit_code == 1
    assert "just one way" in result.output
    assert load("mine").shares["AAPL"] == pytest.approx(5)


def test_bare_bourse_shows_the_guide():
    """Someone who types the name alone should be told what they can do."""
    out = runner.invoke(app, []).output
    assert "bourse buy <symbol> <dollars>" in out
    assert "bourse help <command>" in out


def test_the_guide_lists_every_command():
    """A command missing from the guide is a command nobody will find."""
    from typer.main import get_command

    out = runner.invoke(app, ["help"]).output
    for name in get_command(app).commands:
        assert f"bourse {name}" in out, f"'{name}' is missing from the guide"


def test_help_for_one_command_shows_its_real_options():
    out = runner.invoke(app, ["help", "sell"]).output
    assert "--shares" in out and "--all" in out


def test_help_for_an_unknown_command_says_so():
    result = runner.invoke(app, ["help", "bogus"])
    assert result.exit_code == 1
    assert "no 'bogus' command" in result.output
