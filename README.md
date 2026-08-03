# bourse

Simulate trading S&P 500 from the terminal. Real prices, imaginary money.

```
bourse new my_portfolio --cash 10000
bourse buy AAPL 3000
bourse show
bourse analyze
```

## Install

You need [uv](https://docs.astral.sh/uv/), which installs Python for you. If you
don't already have it, one line does it.

macOS or Linux:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows, in PowerShell:

```
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Open a new terminal so the change takes effect, then install bourse:

```
uv tool install git+https://github.com/conflictednerd/bourse
```

That is it! If uv warns that its directory is not on your PATH, run
`uv tool update-shell` and open a new terminal. Check it with `bourse help`, then:

```
bourse new my_portfolio --cash 10000
```

To update later, run the install command again with `--force`. To remove it,
`uv tool uninstall bourse`. To try it without installing anything permanently:

```
uvx --from git+https://github.com/conflictednerd/bourse bourse show
```

## Commands

```
bourse new my_portfolio --cash 10000     start a portfolio
bourse ls                        list portfolios
bourse use my_portfolio                  choose the one commands act on

bourse buy  AAPL 500             spend $500 on Apple
bourse buy  AAPL --shares 3      buy three shares
bourse sell AAPL 500             sell $500 worth
bourse sell AAPL --all           close the position

bourse deposit 1000              add cash
bourse withdraw 500              take cash out

bourse show                      current position
bourse analyze                   dashboard: charts, holdings, history, performance
bourse quote NVDA                look up a price
bourse search health             find a ticker

bourse help                      this list, from the terminal
bourse help buy                  every option for one command
```

Amounts are in dollars unless you pass `--shares`. Fractional shares are fine.
Every command that moves money asks for confirmation and names the portfolio it
will affect; `-y` skips the prompt, and `-p other` targets a different portfolio
for one command.

`bourse analyze` opens a dashboard with four tabs — value over time, per-stock
profit, trade history, and performance against the S&P 500 over a period you
pick. `r` refreshes, `q` quits.

## Writing a strategy

A strategy is an ordinary Python script. It keeps its own state and decides when
to run; bourse answers price questions and executes trades.

```python
import bourse

p = bourse.load("my_portfolio")

for symbol in ["AAPL", "MSFT", "NVDA"]:
    if bourse.price(symbol) < bourse.price_on(symbol, "2026-07-01") * 0.95:
        p.buy(symbol, 500)
```

| | |
|---|---|
| `bourse.load(name)` | open a portfolio |
| `bourse.price(symbol)` | current price |
| `bourse.price_on(symbol, day)` | closing price on a past day |
| `p.buy(symbol, dollars)` | or `shares=3` |
| `p.sell(symbol, dollars)` | or `shares=3`, or `all=True` |
| `p.deposit(amount)`, `p.withdraw(amount)` | move cash |
| `p.cash`, `p.shares`, `p.realized` | current position |

Trades are written to disk immediately — there is no `save()` — and are tagged
with the script that placed them. See `strategies/` for two worked examples.

## Storage

One JSON file per portfolio, and nothing else:

```
~/.bourse/
  my_portfolio.json
  current          the portfolio in use
```

Copy `my_portfolio.json` to another machine and carry on; copy it alongside to branch a
portfolio. `BOURSE_HOME` relocates the directory, `BOURSE_PORTFOLIO` pins one
portfolio to a single shell.

The file is a log of what you did. Cash, holdings, cost basis and profit are
derived from it rather than stored, so nothing can disagree with the record.

```json
{
  "name": "my_portfolio",
  "created": "2026-08-01T18:04:11Z",
  "log": [
    {"at": "2026-08-01T18:04:11Z", "type": "deposit", "amount": 10000},
    {"at": "2026-08-02T14:31:02Z", "type": "buy", "symbol": "AAPL", "shares": 2.33, "price": 214.33}
  ]
}
```

Prices are fetched when needed and never cached. Offline, you still see your
history and realized profit; only current values are unavailable.

## How the numbers work

- **Cost basis is an average.** Buy 10 shares at \$100 and 10 at \$200 and your
  average is \$150. Selling books the difference against that average and leaves
  it unchanged.
- **Buying today is not a gain or a loss.** Shares bought during the session are
  measured against what you paid, not against yesterday's close.
- **Returns exclude deposits.** The period is split at each deposit and
  withdrawal, so adding cash never shows up as a gain.

Orders fill instantly at the current price, with no commission and no slippage.
No shorting and no margin.

## Development

```
uv sync
uv run pytest
uv run python scripts/update_symbols.py    # refresh the S&P 500 list
```

The engine (`clock`, `portfolio`, `analytics`, `market`) takes prices as plain
mappings and never touches the terminal. The presentation layer (`format`, `ui`,
`cli`, `tui`) renders results and computes nothing.

Adding a command? Give it a docstring and a `help=` on each argument--that is
what `bourse help <command>` shows--and add a line to `GUIDE` in `bourse/ui.py`
so it appears in `bourse help`. A test fails if you forget the second part.

## Licence

MIT.
