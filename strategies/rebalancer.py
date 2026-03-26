"""
Portfolio Rebalancer
─────────────────────
Automatically rebalances your crypto portfolio to target allocations.
Sells over-weight positions and buys under-weight ones.

Default target (adjust to your preference):
  BTC-USD  40%
  ETH-USD  30%
  SOL-USD  15%
  Cash     15%

Triggers a rebalance when any asset drifts > DRIFT_THRESHOLD from target.
"""

from strategies.base import BaseStrategy

# ── Target allocations (must sum to 100) ──────────────────────────
DEFAULT_TARGETS = {
    "BTC-USD": 40.0,
    "ETH-USD": 30.0,
    "SOL-USD": 15.0,
    "_CASH":   15.0,
}

DRIFT_THRESHOLD = 5.0    # Rebalance if any asset is >5% off target
MIN_TRADE_USD   = 15.0   # Ignore trades smaller than $15 (avoid fees)


class RebalancerStrategy(BaseStrategy):

    def __init__(self, client, targets: dict = None, max_position_pct: float = 0.45):
        super().__init__(client, max_position_pct)
        self.targets = targets or DEFAULT_TARGETS
        assert abs(sum(self.targets.values()) - 100) < 0.01, "Targets must sum to 100"

    def describe(self) -> str:
        t = ", ".join(f"{k} {v:.0f}%" for k, v in self.targets.items())
        return f"Rebalances to: {t}. Triggers on >{DRIFT_THRESHOLD}% drift."

    def run(self) -> list[dict]:
        actions = []
        self._log("Analysing portfolio allocation drift...")

        holdings = self.client.get_holdings()
        cash = self.client.get_cash()
        equity = self.client.get_total_equity()

        if equity < 50:
            self._log("Portfolio too small to rebalance (< $50)", "WARN")
            return actions

        # ── Current allocations ────────────────────────────────────
        current = {"_CASH": (cash / equity) * 100}
        holding_map = {}
        for h in holdings:
            pair = h["pair"]
            current[pair] = (h["market_value"] / equity) * 100
            holding_map[pair] = h

        self._log(f"Total equity: ${equity:,.2f}")
        self._log("Current vs target allocations:")

        needs_rebalance = False
        for asset, target_pct in self.targets.items():
            actual_pct = current.get(asset, 0.0)
            drift = actual_pct - target_pct
            drift_str = f"{'+' if drift >= 0 else ''}{drift:.1f}%"
            status = "OK" if abs(drift) < DRIFT_THRESHOLD else "DRIFT"
            self._log(f"  {asset:12} target {target_pct:.0f}%  actual {actual_pct:.1f}%  drift {drift_str}  [{status}]")
            if abs(drift) >= DRIFT_THRESHOLD:
                needs_rebalance = True

        if not needs_rebalance:
            self._log("Portfolio within tolerance. No rebalance needed.")
            actions.append({"action": "HOLD", "reason": "Within drift tolerance"})
            return actions

        self._log("Rebalance triggered. Calculating trades...", "TRADE")

        # ── Sell over-weight positions first ───────────────────────
        for asset, target_pct in self.targets.items():
            if asset == "_CASH":
                continue
            actual_pct = current.get(asset, 0.0)
            drift = actual_pct - target_pct
            if drift > DRIFT_THRESHOLD:
                h = holding_map.get(asset)
                if not h:
                    continue
                sell_value = (drift / 100) * equity
                if sell_value < MIN_TRADE_USD:
                    continue
                sell_qty = sell_value / h["current_price"] if h["current_price"] else 0
                if sell_qty <= 0:
                    continue
                self._log(f"SELL {sell_qty:.6f} {asset} (${sell_value:.2f}) — over-weight by {drift:.1f}%", "TRADE")
                order = self.client.sell_market(asset, sell_qty)
                actions.append({
                    "pair": asset, "action": "SELL",
                    "quantity": sell_qty, "price": h["current_price"],
                    "reason": f"Rebalance: over-weight {drift:.1f}%",
                    "order_id": order.get("id"),
                })
                # Update cash estimate for buys
                cash += sell_value

        # ── Buy under-weight positions ─────────────────────────────
        for asset, target_pct in self.targets.items():
            if asset == "_CASH":
                continue
            actual_pct = current.get(asset, 0.0)
            drift = actual_pct - target_pct
            if drift < -DRIFT_THRESHOLD:
                buy_value = abs(drift / 100) * equity
                if buy_value > cash * 0.95:
                    buy_value = cash * 0.95  # Don't exceed available cash
                if buy_value < MIN_TRADE_USD:
                    continue
                quote = self.client.get_quote(asset)
                price = quote.get("price", 0)
                if not price:
                    continue
                buy_qty = buy_value / price
                self._log(f"BUY {buy_qty:.6f} {asset} (${buy_value:.2f}) — under-weight by {abs(drift):.1f}%", "TRADE")
                order = self.client.buy_market(asset, buy_qty)
                actions.append({
                    "pair": asset, "action": "BUY",
                    "quantity": buy_qty, "price": price,
                    "notional": buy_value,
                    "reason": f"Rebalance: under-weight {abs(drift):.1f}%",
                    "order_id": order.get("id"),
                })
                cash -= buy_value

        self._log(f"Rebalance complete. {len(actions)} trade(s) executed.")
        return actions

    def get_drift_report(self) -> list[dict]:
        """Return current drift for display without trading."""
        holdings = self.client.get_holdings()
        cash = self.client.get_cash()
        equity = self.client.get_total_equity()
        if not equity:
            return []

        holding_map = {h["pair"]: h for h in holdings}
        report = []
        for asset, target_pct in self.targets.items():
            if asset == "_CASH":
                actual_value = cash
            else:
                h = holding_map.get(asset)
                actual_value = h["market_value"] if h else 0.0
            actual_pct = (actual_value / equity) * 100
            drift = actual_pct - target_pct
            report.append({
                "asset": asset,
                "target_pct": target_pct,
                "actual_pct": round(actual_pct, 1),
                "drift": round(drift, 1),
                "actual_value": round(actual_value, 2),
                "target_value": round((target_pct / 100) * equity, 2),
                "needs_rebalance": abs(drift) >= DRIFT_THRESHOLD,
            })
        return report
