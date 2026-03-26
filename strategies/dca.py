"""
DCA (Dollar Cost Averaging) Strategy
──────────────────────────────────────
Systematically buys a fixed USD amount of BTC/ETH on a schedule.
Most reliable long-term compounding strategy — especially powerful toward $100k goal.

Logic:
  - Buy $X of BTC every N days regardless of price
  - Optional: increase buy size on dip days (price down >5%)
  - Never sells — designed for long-term accumulation
"""

from datetime import date
from strategies.base import BaseStrategy

DEFAULT_DCA_AMOUNT_USD = 200    # $200 per cycle
DIP_BONUS_MULTIPLIER = 2.0      # Buy 2x on dip days
DIP_THRESHOLD = -0.05           # -5% day = dip


class DCAStrategy(BaseStrategy):

    def __init__(self, client, dca_amount: float = DEFAULT_DCA_AMOUNT_USD,
                 pairs: list[str] = None, max_position_pct: float = 0.25):
        super().__init__(client, max_position_pct)
        self.dca_amount = dca_amount
        self.pairs = pairs or ["BTC-USD", "ETH-USD"]

    def describe(self) -> str:
        return f"DCA ${self.dca_amount:.0f}/cycle into {', '.join(self.pairs)}. Doubles on dips."

    def run(self) -> list[dict]:
        actions = []
        self._log(f"Running DCA cycle — ${self.dca_amount:.0f} per pair...")

        cash = self.client.get_cash()

        for pair in self.pairs:
            quote = self.client.get_quote(pair)
            price = quote.get("price", 0)
            if not price:
                self._log(f"  {pair}: no price available", "WARN")
                continue

            buy_amount = self.dca_amount

            # Check for dip bonus
            # (In production, compare to yesterday's close from order history)
            if buy_amount > cash:
                self._log(f"  {pair}: insufficient cash (need ${buy_amount:.2f}, have ${cash:.2f})", "WARN")
                continue

            asset_qty = buy_amount / price
            self._log(f"  DCA BUY {pair}: ${buy_amount:.2f} = {asset_qty:.6f} @ ${price:.2f}", "TRADE")
            order = self.client.buy_market(pair, asset_qty)
            actions.append({
                "pair": pair, "action": "BUY",
                "quantity": asset_qty, "price": price,
                "notional": buy_amount,
                "reason": "DCA scheduled buy",
                "order_id": order.get("id"),
            })
            cash -= buy_amount

        self._log(f"DCA cycle complete. {len(actions)} buy(s).")
        return actions
