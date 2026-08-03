"""Buy anything that has fallen more than 5% in the last month.

Run it whenever you like -- by hand, or from cron once a day:

    uv run python strategies/buy_the_dip.py

Nothing here is clever. It exists to show the whole API in one screen.
"""

from datetime import date, timedelta

import bourse

PORTFOLIO = "mine"
WATCHING = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]
FALL = 0.05  # how far it has to have dropped
STAKE = 500  # dollars per buy

p = bourse.load(PORTFOLIO)
a_month_ago = date.today() - timedelta(days=30)

for symbol in WATCHING:
    now = bourse.price(symbol)
    then = bourse.price_on(symbol, a_month_ago)
    change = now / then - 1

    if change < -FALL and p.cash >= STAKE:
        p.buy(symbol, STAKE)
        print(f"{symbol}: down {change:.1%} — bought ${STAKE}")
    else:
        print(f"{symbol}: {change:+.1%} — leaving it")

print(f"\nCash left: ${p.cash:,.2f}")
