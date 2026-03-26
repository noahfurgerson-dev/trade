"""
Cross-Platform Portfolio Rebalancer
─────────────────────────────────────
Maintains target allocations across BOTH Robinhood (crypto) and
Alpaca (stocks/ETFs) as a single unified portfolio.

How it works:
  1. Fetches total equity from both platforms
  2. Calculates each asset's current % of the COMBINED portfolio
  3. Compares against your target allocations
  4. Sells overweight assets on their respective platform
  5. Buys underweight assets on their respective platform
  6. Flags if cash needs to be manually moved between platforms

Default targets (fully customisable in the dashboard):
  ┌─────────────────────────────────────────────┐
  │  CRYPTO (Robinhood)           40% of total  │
  │    BTC-USD                        20%        │
  │    ETH-USD                        12%        │
  │    SOL-USD                         5%        │
  │    Other crypto                    3%        │
  ├─────────────────────────────────────────────┤
  │  STOCKS / ETFs (Alpaca)       45% of total  │
  │    SPY  (S&P 500)                  8%        │
  │    QQQ  (NASDAQ)                   7%        │
  │    JEPI (Income ETF)               8%        │
  │    SCHD (Dividend ETF)             7%        │
  │    SGOV (T-Bills)                 15%        │
  ├─────────────────────────────────────────────┤
  │  CASH BUFFER                  15% of total  │
  │    ~7.5% on each platform                   │
  └─────────────────────────────────────────────┘

Drift threshold: rebalance triggers when any bucket drifts >5% from target.
"""

import json
import os
from datetime import datetime
from strategies.base import BaseStrategy

# ── Default target allocations (% of TOTAL combined portfolio) ────────────────

DEFAULT_TARGETS = {
    # Robinhood crypto
    "BTC-USD":  20.0,
    "ETH-USD":  12.0,
    "SOL-USD":   5.0,
    # Alpaca stocks / ETFs
    "SPY":       8.0,
    "QQQ":       7.0,
    "JEPI":      8.0,
    "SCHD":      7.0,
    "SGOV":     15.0,
    # Cash buffer (remaining ~18% split across both platforms)
}

CRYPTO_PAIRS  = {"BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "ADA-USD",
                  "AVAX-USD", "LINK-USD", "MATIC-USD"}
ALPACA_STOCKS = {"SPY", "QQQ", "JEPI", "JEPQ", "SCHD", "SGOV", "BIL",
                  "VYM", "QYLD", "O", "NVDA", "MSFT", "AAPL"}

DRIFT_THRESHOLD   = 5.0    # % drift before triggering rebalance
MIN_TRADE_USD     = 20.0   # Minimum rebalance trade size
CASH_BUFFER_PCT   = 7.5    # Keep this % cash on EACH platform

# Targets config file (so user can customise via dashboard)
TARGETS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "xp_targets.json")


def load_targets() -> dict:
    os.makedirs(os.path.dirname(TARGETS_FILE), exist_ok=True)
    if os.path.exists(TARGETS_FILE):
        try:
            with open(TARGETS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_TARGETS.copy()


def save_targets(targets: dict):
    with open(TARGETS_FILE, "w") as f:
        json.dump(targets, f, indent=2)


class CrossPlatformRebalancer(BaseStrategy):
    """
    Rebalances across Robinhood (crypto) + Alpaca (stocks/ETFs) as one portfolio.
    """

    def __init__(self, rh_client, alpaca_client, drift_threshold: float = DRIFT_THRESHOLD):
        super().__init__(rh_client, 0.30)
        self.rh             = rh_client
        self.alpaca         = alpaca_client
        self.drift_threshold = drift_threshold
        self.targets        = load_targets()

    def describe(self) -> str:
        return "Rebalances Robinhood crypto + Alpaca stocks as one unified portfolio."

    # ── Portfolio snapshot ─────────────────────────────────────────────────────

    def get_unified_snapshot(self) -> dict:
        """
        Returns a unified view of both platforms with drift analysis.
        Safe to call without executing any trades.
        """
        snapshot = {
            "rh_equity":      0.0,
            "alpaca_equity":  0.0,
            "total_equity":   0.0,
            "rh_cash":        0.0,
            "alpaca_cash":    0.0,
            "positions":      [],   # All positions across both platforms
            "drift":          [],   # Drift analysis per target
            "needs_rebalance": False,
            "max_drift":      0.0,
            "timestamp":      datetime.now().isoformat(),
            "errors":         [],
        }

        # ── Robinhood ──────────────────────────────────────────────
        if self.rh and self.rh.is_configured():
            try:
                rh_holdings = self.rh.get_holdings()
                rh_cash     = self.rh.get_cash()
                snapshot["rh_cash"] = rh_cash
                for h in rh_holdings:
                    snapshot["positions"].append({
                        "symbol":        h["pair"],
                        "platform":      "Robinhood",
                        "market_value":  h["market_value"],
                        "current_price": h["current_price"],
                        "quantity":      h["quantity"],
                        "pnl_pct":       h.get("pnl_pct", 0),
                    })
                rh_equity = sum(h["market_value"] for h in rh_holdings) + rh_cash
                snapshot["rh_equity"] = rh_equity
            except Exception as e:
                snapshot["errors"].append(f"Robinhood: {e}")

        # ── Alpaca ─────────────────────────────────────────────────
        if self.alpaca and self.alpaca.is_configured():
            try:
                alp_positions = self.alpaca.get_positions()
                alp_cash      = self.alpaca.get_cash()
                snapshot["alpaca_cash"] = alp_cash
                for p in alp_positions:
                    snapshot["positions"].append({
                        "symbol":        p["symbol"],
                        "platform":      "Alpaca",
                        "market_value":  p["market_value"],
                        "current_price": p["current_price"],
                        "quantity":      p["qty"],
                        "pnl_pct":       p.get("pnl_pct", 0),
                    })
                alp_equity = sum(p["market_value"] for p in alp_positions) + alp_cash
                snapshot["alpaca_equity"] = alp_equity
            except Exception as e:
                snapshot["errors"].append(f"Alpaca: {e}")

        total = snapshot["rh_equity"] + snapshot["alpaca_equity"]
        snapshot["total_equity"] = total
        if total == 0:
            return snapshot

        # ── Drift analysis ─────────────────────────────────────────
        pos_map = {p["symbol"]: p["market_value"] for p in snapshot["positions"]}

        for symbol, target_pct in self.targets.items():
            current_val  = pos_map.get(symbol, 0.0)
            current_pct  = (current_val / total * 100) if total else 0
            drift        = current_pct - target_pct
            abs_drift    = abs(drift)
            target_val   = total * target_pct / 100

            snapshot["drift"].append({
                "symbol":       symbol,
                "platform":     "Robinhood" if symbol in CRYPTO_PAIRS else "Alpaca",
                "target_pct":   target_pct,
                "current_pct":  round(current_pct, 2),
                "drift_pct":    round(drift, 2),
                "target_val":   round(target_val, 2),
                "current_val":  round(current_val, 2),
                "gap_usd":      round(target_val - current_val, 2),
                "needs_action": abs_drift >= self.drift_threshold,
                "action":       "SELL" if drift > 0 else "BUY",
            })
            if abs_drift > snapshot["max_drift"]:
                snapshot["max_drift"] = abs_drift

        snapshot["needs_rebalance"] = snapshot["max_drift"] >= self.drift_threshold
        snapshot["drift"].sort(key=lambda x: abs(x["drift_pct"]), reverse=True)
        return snapshot

    # ── Execute rebalance ──────────────────────────────────────────────────────

    def run(self) -> list[dict]:
        actions = []

        rh_ok     = self.rh    and self.rh.is_configured()
        alpaca_ok = self.alpaca and self.alpaca.is_configured()

        if not rh_ok and not alpaca_ok:
            self._log("Neither Robinhood nor Alpaca configured.", "WARN")
            return actions

        self._log("Cross-Platform Rebalancer: building unified snapshot...")
        snap = self.get_unified_snapshot()

        if snap["errors"]:
            for err in snap["errors"]:
                self._log(f"  Warning: {err}", "WARN")

        total = snap["total_equity"]
        self._log(
            f"  Total portfolio: ${total:,.2f}  "
            f"(RH: ${snap['rh_equity']:,.2f}  Alpaca: ${snap['alpaca_equity']:,.2f})"
        )

        if not snap["needs_rebalance"]:
            self._log(
                f"  Portfolio balanced. Max drift: {snap['max_drift']:.1f}% "
                f"(threshold: {self.drift_threshold:.0f}%)"
            )
            return [{"action": "HOLD", "reason": "Portfolio within drift tolerance",
                     "max_drift": snap["max_drift"]}]

        self._log(
            f"  Rebalance triggered. Max drift: {snap['max_drift']:.1f}%",
            "TRADE"
        )

        # Sort: sells first (frees up cash), then buys
        to_sell = [d for d in snap["drift"] if d["needs_action"] and d["action"] == "SELL"]
        to_buy  = [d for d in snap["drift"] if d["needs_action"] and d["action"] == "BUY"]

        # ── Execute SELLs ──────────────────────────────────────────
        for item in to_sell:
            sell_usd = abs(item["gap_usd"])
            if sell_usd < MIN_TRADE_USD:
                continue

            sym      = item["symbol"]
            platform = item["platform"]
            self._log(
                f"  SELL ${sell_usd:,.2f} of {sym} on {platform} "
                f"(overweight {item['drift_pct']:+.1f}%)",
                "TRADE"
            )

            if platform == "Robinhood" and rh_ok:
                quote = self.rh.get_quote(sym)
                price = quote.get("price", 0)
                if price:
                    qty   = round(sell_usd / price, 8)
                    order = self.rh.sell_market(sym, qty)
                    actions.append({
                        "symbol": sym, "platform": "Robinhood",
                        "action": "SELL", "quantity": qty,
                        "price": price, "notional": sell_usd,
                        "reason": f"Cross-platform rebalance: overweight {item['drift_pct']:+.1f}%",
                        "order_id": order.get("id"),
                    })

            elif platform == "Alpaca" and alpaca_ok:
                pos_map = {p["symbol"]: p for p in snap["positions"]}
                pos     = pos_map.get(sym, {})
                qty     = pos.get("quantity", 0)
                price   = pos.get("current_price", 0)
                if qty and price:
                    sell_qty = round(min(sell_usd / price, qty), 8)
                    order    = self.alpaca.sell_market(sym, sell_qty)
                    actions.append({
                        "symbol": sym, "platform": "Alpaca",
                        "action": "SELL", "quantity": sell_qty,
                        "price": price, "notional": sell_usd,
                        "reason": f"Cross-platform rebalance: overweight {item['drift_pct']:+.1f}%",
                        "order_id": order.get("id"),
                    })

        # ── Execute BUYs ───────────────────────────────────────────
        for item in to_buy:
            buy_usd  = abs(item["gap_usd"])
            if buy_usd < MIN_TRADE_USD:
                continue

            sym      = item["symbol"]
            platform = item["platform"]

            self._log(
                f"  BUY  ${buy_usd:,.2f} of {sym} on {platform} "
                f"(underweight {item['drift_pct']:+.1f}%)",
                "TRADE"
            )

            if platform == "Robinhood" and rh_ok:
                rh_avail = snap["rh_cash"] - (snap["rh_equity"] * CASH_BUFFER_PCT / 100)
                if rh_avail < MIN_TRADE_USD:
                    self._log(
                        f"  SKIP {sym} — insufficient RH cash "
                        f"(${snap['rh_cash']:,.2f} available, buffer ${snap['rh_equity'] * CASH_BUFFER_PCT/100:.2f})",
                        "WARN"
                    )
                    actions.append({
                        "symbol": sym, "platform": "Robinhood",
                        "action": "NEEDS_CASH",
                        "amount_needed": buy_usd,
                        "reason": "Insufficient Robinhood cash — consider transferring from Alpaca",
                    })
                    continue
                actual_buy = min(buy_usd, rh_avail)
                quote = self.rh.get_quote(sym)
                price = quote.get("price", 0)
                if price:
                    qty   = round(actual_buy / price, 8)
                    order = self.rh.buy_market(sym, qty)
                    actions.append({
                        "symbol": sym, "platform": "Robinhood",
                        "action": "BUY", "quantity": qty,
                        "price": price, "notional": actual_buy,
                        "reason": f"Cross-platform rebalance: underweight {item['drift_pct']:+.1f}%",
                        "order_id": order.get("id"),
                    })

            elif platform == "Alpaca" and alpaca_ok:
                if not self.alpaca.is_market_open():
                    self._log(f"  DEFER {sym} — market closed, will buy next open", "INFO")
                    actions.append({
                        "symbol": sym, "platform": "Alpaca",
                        "action": "DEFERRED", "notional": buy_usd,
                        "reason": "Market closed — will execute at next open",
                    })
                    continue
                alp_avail = snap["alpaca_cash"] - (snap["alpaca_equity"] * CASH_BUFFER_PCT / 100)
                if alp_avail < MIN_TRADE_USD:
                    self._log(
                        f"  SKIP {sym} — insufficient Alpaca cash "
                        f"(${snap['alpaca_cash']:,.2f} available)",
                        "WARN"
                    )
                    actions.append({
                        "symbol": sym, "platform": "Alpaca",
                        "action": "NEEDS_CASH",
                        "amount_needed": buy_usd,
                        "reason": "Insufficient Alpaca cash — consider depositing funds",
                    })
                    continue
                actual_buy = min(buy_usd, alp_avail)
                order = self.alpaca.buy_market(sym, notional=actual_buy)
                actions.append({
                    "symbol": sym, "platform": "Alpaca",
                    "action": "BUY", "notional": actual_buy,
                    "reason": f"Cross-platform rebalance: underweight {item['drift_pct']:+.1f}%",
                    "order_id": order.get("id"),
                })

        self._log(f"Cross-platform rebalance done. {len(actions)} action(s).")
        return actions
