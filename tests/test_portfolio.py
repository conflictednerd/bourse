"""Accounting and storage, exercised with explicit prices."""

import json

import pytest

from bourse.portfolio import BourseError, Portfolio, create, load, replay


def test_deposit_is_cash():
    p = Portfolio("t")
    p.deposit(10_000)
    assert p.cash == 10_000
    assert p.deposited == 10_000


def test_buying_dollars_converts_to_fractional_shares():
    p = Portfolio("t")
    p.deposit(10_000)
    p.buy("AAPL", 500, price=200)
    assert p.shares["AAPL"] == 2.5
    assert p.cash == 9_500
    assert p.average_cost("AAPL") == 200


def test_average_cost_across_two_buys_then_a_partial_sell():
    p = Portfolio("t")
    p.deposit(10_000)
    p.buy("AAPL", shares=10, price=100)  # $1,000
    p.buy("AAPL", shares=10, price=200)  # $2,000 -> 20 shares, $150 average
    assert p.average_cost("AAPL") == 150

    p.sell("AAPL", shares=5, price=250)
    # Selling doesn't move the average; it books (250 - 150) * 5.
    assert p.average_cost("AAPL") == 150
    assert p.shares["AAPL"] == 15
    assert p.realized["AAPL"] == pytest.approx(500)
    assert p.cash == pytest.approx(10_000 - 1_000 - 2_000 + 1_250)


def test_selling_everything_leaves_no_dust():
    p = Portfolio("t")
    p.deposit(1_000)
    p.buy("AAPL", 333.33, price=77.77)  # deliberately awkward numbers
    p.sell("AAPL", all=True, price=80)
    assert "AAPL" not in p.shares
    assert "AAPL" not in p.cost


def test_loss_is_booked_as_negative_realized():
    p = Portfolio("t")
    p.deposit(1_000)
    p.buy("AAPL", shares=5, price=100)
    p.sell("AAPL", all=True, price=80)
    assert p.realized["AAPL"] == pytest.approx(-100)


def test_cannot_spend_more_cash_than_you_have():
    p = Portfolio("t")
    p.deposit(100)
    with pytest.raises(BourseError, match="can't spend"):
        p.buy("AAPL", 500, price=200)
    assert p.log[-1]["type"] == "deposit"  # nothing was written


def test_cannot_sell_what_you_dont_own():
    p = Portfolio("t")
    p.deposit(1_000)
    with pytest.raises(BourseError, match="don't own"):
        p.sell("AAPL", all=True, price=100)


def test_cannot_sell_more_shares_than_held():
    p = Portfolio("t")
    p.deposit(1_000)
    p.buy("AAPL", shares=2, price=100)
    with pytest.raises(BourseError, match="not 5"):
        p.sell("AAPL", shares=5, price=100)
    assert p.shares["AAPL"] == 2


def test_cannot_withdraw_more_than_cash():
    p = Portfolio("t")
    p.deposit(100)
    with pytest.raises(BourseError, match="can't withdraw"):
        p.withdraw(500)


def test_amount_and_shares_are_mutually_exclusive():
    p = Portfolio("t")
    p.deposit(1_000)
    with pytest.raises(BourseError, match="not both"):
        p.buy("AAPL", 100, shares=1, price=100)


def test_symbols_are_case_insensitive():
    p = Portfolio("t")
    p.deposit(1_000)
    p.buy("aapl", 100, price=100)
    assert "AAPL" in p.shares


# -- storage --------------------------------------------------------------


def test_every_action_writes_immediately():
    p = create("t", cash=1_000)
    p.buy("AAPL", 100, price=50)
    # Nothing was explicitly saved, yet a fresh read sees both entries.
    assert [e["type"] for e in load("t").log] == ["deposit", "buy"]


def test_round_trip_preserves_state():
    p = create("t", cash=10_000)
    p.buy("MSFT", 2_500, price=333.33)
    p.sell("MSFT", shares=1, price=400)
    again = load("t")
    assert again.cash == pytest.approx(p.cash)
    assert again.shares == pytest.approx(p.shares)
    assert again.realized == pytest.approx(p.realized)


def test_unknown_fields_are_ignored_so_old_files_keep_working(tmp_path):
    (tmp_path / "t.json").write_text(
        json.dumps(
            {
                "name": "t",
                "created": "2026-01-01T00:00:00Z",
                "future_field": "written by a later version",
                "log": [
                    {
                        "at": "2026-01-01T00:00:00Z",
                        "type": "deposit",
                        "amount": 100,
                        "settled": True,
                        "currency": "USD",
                    },
                ],
            }
        )
    )
    assert load("t").cash == 100


def test_unknown_entry_type_refuses_rather_than_miscounting(tmp_path):
    (tmp_path / "t.json").write_text(
        json.dumps(
            {
                "name": "t",
                "log": [
                    {"at": "2026-01-01T00:00:00Z", "type": "deposit", "amount": 100},
                    {"at": "2026-01-02T00:00:00Z", "type": "dividend", "amount": 5},
                ],
            }
        )
    )
    with pytest.raises(BourseError, match="Upgrade bourse"):
        load("t")


def test_missing_portfolio_says_what_does_exist():
    create("main")
    with pytest.raises(BourseError, match="You have: main"):
        load("nope")


def test_replay_is_pure_and_takes_plain_dicts():
    state = replay(
        [
            {"type": "deposit", "amount": 1_000},
            {"type": "buy", "symbol": "X", "shares": 4, "price": 100},
        ]
    )
    assert state.cash == 600
    assert state.shares == {"X": 4}
