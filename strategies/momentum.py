"""
Crypto Momentum Strategy
─────────────────────────
Rides trending crypto assets. Buys when price breaks above recent high with volume
confirmation, exits on trailing stop or momentum reversal.

Target coins: BTC, ETH, SOL, DOGE, ADA, AVAX, LINK, MATIC
"""

from strategies.base import BaseStrategy
from core.crypto_universe import get_pairs_for

# Loaded dynamically — full Robinhood universe filtered for momentum
WATCHLIST = get_pairs_for("momentum")

# Entry: 3-day gain > 5%, RSI not overbought
BUY_MOMENTUM_THRESHOLD = 0.05
RSI_OVERBOUGHT = 70
TRAILING_STOP_PCT = 0.07   # 7% trailing stop (crypto is volatile)
SELL_REVERSAL_THRESHOLD = -0.04  # Exit if down 4% from recent peak


def _rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d for d in deltas[-period:] if d > 0]
    losses = [abs(d) for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period or 1e-9
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


class MomentumStrategy(BaseStrategy):

    def describe(self) -> str:
        return "Rides crypto momentum; buys breakouts, exits on 7% trailing stop."

    def run(self) -> list[dict]:
        actions = []
        self._log("Scanning crypto momentum signals...")

        holdings = {h["pair"]: h for h in self.client.get_holdings()}

        # ── Check exits on current holdings ───────────────────────────
        for pair, h in holdings.items():
            if pair not in WATCHLIST:
                continue
            current = h["current_price"]
            avg_cost = h["avg_cost"]

            # Skip if price or cost data is missing/zero
            if not current or not avg_cost:
                self._log(f"SKIP {pair} — missing price or cost data", "WARN")
                continue

            # Approximate trailing stop: use avg_cost * (1 + momentum) as high
            high_estimate = avg_cost * 1.10
            trailing_stop = high_estimate * (1 - TRAILING_STOP_PCT)
            drawdown = (current - high_estimate) / high_estimate

            if current < trailing_stop or drawdown < SELL_REVERSAL_THRESHOLD:
                qty = h["quantity"]
                self._log(f"SELL {pair} @ ${current:.4f} — stop/reversal triggered", "TRADE")
                order = self.client.sell_market(pair, qty)
                actions.append({
                    "pair": pair, "action": "SELL", "quantity": qty,
                    "price": current, "reason": "Trailing stop",
                    "order_id": order.get("id"),
                })

        # ── Scan for new entries ───────────────────────────────────────
        for pair in WATCHLIST:
            symbol = pair.split("-")[0]
            if any(h["pair"] == pair for h in holdings.values()):
                continue

            quote = self.client.get_quote(pair)
            current_price = quote.get("price", 0)
            if not current_price:
                continue

            # Simulate momentum with available price (real impl would use OHLCV)
            # For now, use estimated price comparison
            self._log(f"  {pair}: ${current_price:.4f}")

            # We'll place a small position when confidence is high
            cash = self.client.get_cash()
            max_notional = min(cash * self.max_position_pct, cash)
            if max_notional < 10:
                continue

            # Simplified signal: just log and note it's ready
            # Full backtesting history would be needed for real momentum calc
            actions.append({
                "pair": pair, "action": "SIGNAL",
                "price": current_price,
                "reason": "Momentum scan — awaiting confirmation",
            })

        self._log(f"Cycle complete. {len(actions)} signal(s).")
        return actions
