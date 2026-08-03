"""Portfolio storage and the accounting rules that govern it.

A portfolio is an append-only log of deposits, withdrawals and trades. Cash,
holdings, cost basis and realized profit are never stored; they are derived
from the log by :func:`replay`, so no derived figure can disagree with the
record it came from.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import NamedTuple

from .clock import stamp

#: Share counts below this are treated as zero, absorbing floating-point
#: remainders left behind when a position is closed.
DUST = 1e-9


class BourseError(Exception):
    """An invalid request, described in terms the user can act on."""


def home() -> Path:
    """The directory holding every portfolio. Overridden by ``$BOURSE_HOME``."""
    return Path(os.environ.get("BOURSE_HOME") or Path.home() / ".bourse")


class State(NamedTuple):
    """The position implied by a log."""

    cash: float
    shares: dict[str, float]  # symbol -> shares held
    cost: dict[str, float]  # symbol -> total $ paid for the shares still held
    realized: dict[str, float]  # symbol -> profit/loss already booked by selling
    deposited: float  # money you put in, minus money you took out


def replay(log: list[dict]) -> State:
    """Derive the current state from a log.

    Cost basis is tracked as an average: a purchase adds its cost to the pot,
    and a sale removes the average cost of the shares sold, booking the
    difference as realized profit. Selling therefore leaves the average cost
    per share unchanged.

    Pure: performs no I/O and mutates nothing.
    """
    cash = 0.0
    deposited = 0.0
    shares: dict[str, float] = {}
    cost: dict[str, float] = {}
    realized: dict[str, float] = {}

    for entry in log:
        kind = entry.get("type")

        if kind == "deposit":
            amount = entry.get("amount", 0.0)
            cash += amount
            deposited += amount

        elif kind == "withdraw":
            amount = entry.get("amount", 0.0)
            cash -= amount
            deposited -= amount

        elif kind == "buy":
            symbol = entry.get("symbol", "")
            n = entry.get("shares", 0.0)
            price = entry.get("price", 0.0)
            cash -= n * price
            shares[symbol] = shares.get(symbol, 0.0) + n
            cost[symbol] = cost.get(symbol, 0.0) + n * price

        elif kind == "sell":
            symbol = entry.get("symbol", "")
            n = entry.get("shares", 0.0)
            price = entry.get("price", 0.0)
            held = shares.get(symbol, 0.0)
            average = cost.get(symbol, 0.0) / held if held > DUST else 0.0
            cash += n * price
            shares[symbol] = held - n
            cost[symbol] = cost.get(symbol, 0.0) - average * n
            realized[symbol] = realized.get(symbol, 0.0) + (price - average) * n

        else:
            # Skipping an unrecognised entry would silently misstate the balance.
            raise BourseError(
                f"This portfolio contains a '{kind}' entry that this version of "
                f"bourse doesn't know about. Upgrade bourse to read it."
            )

        if kind in ("buy", "sell") and abs(shares.get(symbol, 0.0)) < DUST:
            shares.pop(symbol, None)
            cost.pop(symbol, None)

    return State(cash, shares, cost, realized, deposited)


class Portfolio:
    """A named portfolio: its log, the state that log implies, and its actions.

    Every action appends one entry and writes the file immediately, so a
    portfolio on disk is never behind the object in memory.

    ``by`` records the script responsible for the trades placed through this
    instance, distinguishing automated trades from ones entered by hand.
    """

    def __init__(
        self,
        name: str,
        log: list[dict] | None = None,
        created: str | None = None,
        by: str | None = None,
    ):
        self.name = name
        self.log = log if log is not None else []
        self.created = created or stamp()
        self.by = by
        self._state = replay(self.log)

    # -- derived state ----------------------------------------------------

    @property
    def cash(self) -> float:
        return self._state.cash

    @property
    def shares(self) -> dict[str, float]:
        return self._state.shares

    @property
    def cost(self) -> dict[str, float]:
        return self._state.cost

    @property
    def realized(self) -> dict[str, float]:
        return self._state.realized

    @property
    def deposited(self) -> float:
        return self._state.deposited

    def average_cost(self, symbol: str) -> float:
        """The average price paid for the shares currently held."""
        held = self._state.shares.get(symbol, 0.0)
        return self._state.cost.get(symbol, 0.0) / held if held > DUST else 0.0

    # -- preconditions ----------------------------------------------------

    def require_cash(self, amount: float, verb: str = "spend") -> None:
        if amount > self.cash + 0.005:
            raise BourseError(
                f"You have ${self.cash:,.2f} in cash, so you can't {verb} ${amount:,.2f}."
            )

    def require_shares(self, symbol: str, count: float) -> None:
        held = self.shares.get(symbol, 0.0)
        if held <= DUST:
            raise BourseError(f"You don't own any {symbol}.")
        if count > held + DUST:
            raise BourseError(
                f"You hold {held:.4f} shares of {symbol}, not {count:.4f}. "
                f"Use --all to sell the lot."
            )

    # -- actions ----------------------------------------------------------

    def deposit(self, amount: float, note: str = "") -> dict:
        if amount <= 0:
            raise BourseError("Deposit an amount greater than zero.")
        entry = {"at": stamp(), "type": "deposit", "amount": round(amount, 2)}
        if note:
            entry["note"] = note
        return self._append(entry)

    def withdraw(self, amount: float, note: str = "") -> dict:
        if amount <= 0:
            raise BourseError("Withdraw an amount greater than zero.")
        self.require_cash(amount, "withdraw")
        entry = {"at": stamp(), "type": "withdraw", "amount": round(amount, 2)}
        if note:
            entry["note"] = note
        return self._append(entry)

    def buy(
        self,
        symbol: str,
        amount: float | None = None,
        *,
        shares: float | None = None,
        price: float | None = None,
    ) -> dict:
        """Buy a dollar ``amount`` of a stock, or a given number of ``shares``.

        Fills at the current market price unless ``price`` is supplied.
        """
        symbol = symbol.upper()
        price = _resolve_price(symbol, price)
        n = _shares_wanted(amount, shares, price)

        self.require_cash(n * price)
        return self._trade("buy", symbol, n, price)

    def sell(
        self,
        symbol: str,
        amount: float | None = None,
        *,
        shares: float | None = None,
        all: bool = False,
        price: float | None = None,
    ) -> dict:
        """Sell a dollar ``amount``, a number of ``shares``, or the whole position."""
        symbol = symbol.upper()
        held = self.shares.get(symbol, 0.0)
        self.require_shares(symbol, 0.0)

        price = _resolve_price(symbol, price)
        if all:
            n = held
        else:
            n = _shares_wanted(amount, shares, price)
            self.require_shares(symbol, n)
            n = min(n, held)  # absorb float noise so "sell everything" lands on zero
        return self._trade("sell", symbol, n, price)

    def _trade(self, kind: str, symbol: str, shares: float, price: float) -> dict:
        entry = {
            "at": stamp(),
            "type": kind,
            "symbol": symbol,
            "shares": round(shares, 8),
            "price": round(price, 4),
        }
        if self.by:
            entry["by"] = self.by
        return self._append(entry)

    def _append(self, entry: dict) -> dict:
        """Append one entry to the log on disk.

        The file is locked for the whole read-modify-write, so concurrent
        writers -- a scheduled strategy and an interactive command, say --
        cannot overwrite one another's entries.
        """
        home().mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            held.seek(0)
            text = held.read()
            if text.strip():
                self.log = _parse(text, self.name).get("log", [])
            self.log.append(entry)
            self.save()
        self._state = replay(self.log)
        return entry

    # -- storage ----------------------------------------------------------

    @property
    def path(self) -> Path:
        return home() / f"{self.name}.json"

    def save(self) -> None:
        home().mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"name": self.name, "created": self.created, "log": self.log}, indent=2)
            + "\n"
        )


def _resolve_price(symbol: str, price: float | None) -> float:
    """Return the supplied price, or fetch the current one.

    ``market`` is imported here rather than at module scope so that the
    accounting above can be exercised without any network dependency.
    """
    if price is not None:
        return price
    from . import market

    return market.price(symbol)


def _shares_wanted(amount: float | None, shares: float | None, price: float) -> float:
    if (amount is None) == (shares is None):
        raise BourseError("Say either a dollar amount or a number of shares, not both.")
    if amount is not None:
        if amount <= 0:
            raise BourseError("Amount must be greater than zero.")
        return amount / price
    if shares <= 0:
        raise BourseError("Share count must be greater than zero.")
    return shares


# -- locating and creating portfolios --------------------------------------


def _check_name(name: str) -> str:
    """Require a name that is safe to use as a filename.

    Names must be a single path component so that a portfolio cannot be written
    outside the bourse directory or into a location ``names()`` cannot list.
    """
    if not name or name.startswith(".") or name != Path(name).name:
        raise BourseError(
            f"'{name}' can't be used as a portfolio name. Use a plain name like "
            f"'mine' or 'swing-trades' — no slashes, and not starting with a dot."
        )
    return name


def _parse(text: str, name: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise BourseError(
            f"The file for '{name}' is damaged and can't be read: {error}. "
            f"If you have a copy of {name}.json from another machine, restore it."
        ) from None


def load(name: str, by: str | None = None) -> Portfolio:
    _check_name(name)
    path = home() / f"{name}.json"
    if not path.exists():
        known = ", ".join(names()) or "none yet"
        raise BourseError(f"No portfolio called '{name}'. You have: {known}.")
    data = _parse(path.read_text(), name)
    return Portfolio(
        # The filename, not the recorded name, determines identity: copying a
        # portfolio file must produce an independent portfolio.
        name=name,
        # Valuation walks the log forwards, so order is a precondition rather
        # than an assumption. Timestamps are fixed-width UTC and sort as text;
        # the sort is stable, preserving entries recorded in the same second.
        log=sorted(data.get("log", []), key=lambda entry: entry.get("at", "")),
        created=data.get("created"),
        by=by,
    )


def create(name: str, cash: float = 0.0) -> Portfolio:
    _check_name(name)
    if cash < 0:
        raise BourseError("A portfolio can't start with less than nothing.")
    if (home() / f"{name}.json").exists():
        raise BourseError(f"A portfolio called '{name}' already exists.")
    portfolio = Portfolio(name=name)
    if cash <= 0:
        portfolio.save()
    if cash > 0:
        portfolio.deposit(cash, note="opening balance")
    return portfolio


def names() -> list[str]:
    if not home().exists():
        return []
    return sorted(p.stem for p in home().glob("*.json"))


def current() -> str | None:
    """The portfolio commands act on unless one is named explicitly."""
    from_env = os.environ.get("BOURSE_PORTFOLIO")
    if from_env:
        return from_env
    marker = home() / "current"
    if marker.exists():
        return marker.read_text().strip() or None
    return None


def use(name: str) -> None:
    _check_name(name)
    home().mkdir(parents=True, exist_ok=True)
    (home() / "current").write_text(name + "\n")
