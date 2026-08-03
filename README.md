# bourse

Paper-trade the S&P 500 from the terminal. Real prices, imaginary money.

```
bourse new mine --cash 10000
bourse buy AAPL 3000
bourse show
bourse analyze
```

## Install

Requires [uv](https://docs.astral.sh/uv/). Nothing else — no API key, no account.

```
uv tool install git+https://github.com/conflictednerd/bourse
```

Or run it without installing:

```
uvx --from git+https://github.com/conflictednerd/bourse bourse show
```

## Commands

```
bourse new mine --cash 10000     start a portfolio
bourse ls                        list portfolios
bourse use mine                  choose the one commands act on

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

p = bourse.load("mine")

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
  mine.json
  current          the portfolio in use
```

Copy `mine.json` to another machine and carry on; copy it alongside to branch a
portfolio. `BOURSE_HOME` relocates the directory, `BOURSE_PORTFOLIO` pins one
portfolio to a single shell.

The file is a log of what you did. Cash, holdings, cost basis and profit are
derived from it rather than stored, so nothing can disagree with the record.

```json
{
  "name": "mine",
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

## Licence

MIT.
