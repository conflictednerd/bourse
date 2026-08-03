"""Keep an equal amount of money in each of a handful of stocks.

Whatever has run ahead gets trimmed; whatever has lagged gets topped up. Run it
once a month and it will quietly keep the weights level.

    uv run python strategies/rebalance.py
"""

import bourse

PORTFOLIO = "mine"
TARGETS = ["AAPL", "MSFT", "NVDA", "GOOGL"]
TOLERANCE = 0.02  # don't bother trading for less than 2% out of line

p = bourse.load(PORTFOLIO)
prices = bourse.prices(TARGETS)

# Everything this strategy is responsible for, plus the cash to spread around.
invested = sum(p.shares.get(s, 0) * prices[s] for s in TARGETS)
budget = invested + p.cash
target = budget / len(TARGETS)

print(f"${budget:,.2f} across {len(TARGETS)} names — ${target:,.2f} each\n")

# Sell the overweight ones first, so the cash is there to buy the rest.
for symbol in TARGETS:
    held = p.shares.get(symbol, 0) * prices[symbol]
    if held - target > budget * TOLERANCE:
        p.sell(symbol, held - target)
        print(f"{symbol}: trimmed ${held - target:,.2f}")

for symbol in TARGETS:
    held = p.shares.get(symbol, 0) * prices[symbol]
    short = target - held
    if short > budget * TOLERANCE and p.cash > 0:
        p.buy(symbol, min(short, p.cash))
        print(f"{symbol}: topped up ${min(short, p.cash):,.2f}")

print(f"\nCash left: ${p.cash:,.2f}")
