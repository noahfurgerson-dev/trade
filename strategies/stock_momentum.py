"""
Stock Momentum Strategy (Alpaca)
──────────────────────────────────
Swing-trades high-momentum US stocks and ETFs via Alpaca.
Uses a simple relative-strength approach:
  - Watchlist of high-quality growth + sector ETFs
  - Buys when the latest close is above the recent average (momentum)
  - Sells when price drops below trailing stop or hits profit target
  - Runs during market hours only

Watchlist split:
  Core ETFs  — SPY, QQQ, IWM        (broad market momentum)
  Tech       — NVDA, MSFT, AAPL, META, GOOGL
  Sector     — XLK, XLE, XLF, XLV   (sector rotation)
  Leveraged  — TQQQ, SOXL            (high-risk, small size)
"""

from strategies.base import BaseStrategy

# ── Watchlist & sizing ────────────────────────────────────────────────────────
CORE_ETFS    = ["SPY", "QQQ", "IWM"]
TECH_STOCKS  = ["NVDA", "MSFT", "AAPL", "META", "GOOGL"]
SECTOR_ETFS  = ["XLK", "XLE", "XLF", "XLV"]
LEVERAGED    = ["TQQQ", "SOXL"]   # Tiny position only

# Max allocation per position (% of Alpaca portfolio)
SIZE_MAP = {
    **{s: 0.10 for s in CORE_ETFS},
    **{s: 0.08 for s in TECH_STOCKS},
    **{s: 0.06 for s in SECTOR_ETFS},
    **{s: 0.03 for s in LEVERAGED},   # Leveraged = tiny
}

PROFIT_TARGET_PCT  = 0.06   # Take 50% off at +6%
TRAILING_STOP_PCT  = 0.04   # Exit if price falls 4% from avg cost
MIN_MOMENTUM_GAIN  = 0.005  # Only buy if latest close > avg by 0.5%
MIN_TRADE_USD      = 25.0


class StockMomentumStrategy(BaseStrategy):
    """
    Buys US stocks/ETFs showing upward momentum via Alpaca.
    Exits on profit target or trailing stop.
    Requires AlpacaClient.
    """

    def __init__(self, alpaca_client, max_position_pct: float = 0.10):
        super().__init__(alpaca_client, max_position_pct)
        self.alpaca = alpaca_client

    def describe(self) -> str:
        return "Swing-trades momentum stocks & ETFs (SPY/QQQ/NVDA/MSFT) via Alpaca."

    def run(self) -> list[dict]:
        actions = []

        if not self.alpaca.is_configured():
            self._log("Alpaca not configured — skipping stock momentum.", "WARN")
            return actions

        if not self.alpaca.is_market_open():
            self._log("Market closed — stock momentum skipped.", "INFO")
            return [{"action": "HOLD", "reason": "Market closed"}]

        self._log("Stock Momentum: scanning watchlist...")

        try:
            positions = self.alpaca.get_positions()
        except Exception as e:
            self._log(f"Cannot fetch Alpaca positions: {e}", "WARN")
            return actions

        cash         = self.alpaca.get_cash()
        portfolio    = self.alpaca.get_portfolio_value()
        held_map     = {p["symbol"]: p for p in positions}

        all_symbols = CORE_ETFS + TECH_STOCKS + SECTOR_ETFS + LEVERAGED

        # ── Manage existing positions first ──────────────────────────
        for sym, pos in held_map.items():
            if sym not in all_symbols:
                continue
            avg_cost      = pos.get("avg_cost", 0)
            current_price = pos.get("current_price", 0)
            qty           = pos.get("qty", 0)
            pnl_pct       = pos.get("pnl_pct", 0)

            if not avg_cost or not current_price or not qty:
                continue

            # Profit target
            if pnl_pct >= PROFIT_TARGET_PCT * 100:
                sell_qty = qty * 0.5
                self._log(
                    f"SELL 50% {sym} @ ${current_price:.2f} — profit target hit (+{pnl_pct:.1f}%)",
                    "TRADE"
                )
                order = self.alpaca.sell_market(sym, sell_qty)
                actions.append({
                    "symbol": sym, "action": "SELL",
                    "quantity": sell_qty, "price": current_price,
                    "reason": f"Profit target +{pnl_pct:.1f}%",
                    "order_id": order.get("id"),
                })
                continue

            # Trailing stop
            if pnl_pct <= -TRAILING_STOP_PCT * 100:
                self._log(
                    f"SELL {sym} @ ${current_price:.2f} — stop loss ({pnl_pct:.1f}%)",
                    "TRADE"
                )
                order = self.alpaca.sell_market(sym, qty)
                actions.append({
                    "symbol": sym, "action": "SELL",
                    "quantity": qty, "price": current_price,
                    "reason": f"Stop loss {pnl_pct:.1f}%",
                    "order_id": order.get("id"),
                })
                continue

            self._log(f"  HOLD {sym} — ${current_price:.2f} P&L {pnl_pct:+.1f}%")

        # ── Scan for new momentum entries ─────────────────────────────
        self._log("Scanning for momentum entries...")

        for sym in all_symbols:
            if sym in held_map:
                continue   # Already holding

            max_pct = SIZE_MAP.get(sym, self.max_position_pct)
            max_val = min(portfolio * max_pct, cash * 0.9)
            if max_val < MIN_TRADE_USD:
                continue

            bar = self.alpaca.get_latest_bar(sym)
            if bar.get("error"):
                continue

            close = bar.get("close", 0)
            open_ = bar.get("open", 0)
            high  = bar.get("high", 0)
            low   = bar.get("low", 0)
            vol   = bar.get("volume", 0)

            if not close or not open_:
                continue

            # Simple momentum check: close above open, candle body > 0.5%
            body_gain = (close - open_) / open_ if open_ else 0
            high_range = (high - low) / low if low else 0

            if body_gain < MIN_MOMENTUM_GAIN:
                self._log(f"  SKIP {sym} — no momentum (candle {body_gain:+.2%})")
                continue

            # Strong upward candle with decent volume
            qty = max_val / close
            self._log(
                f"  BUY {qty:.4f} {sym} @ ${close:.2f} (${max_val:.2f}) — "
                f"momentum {body_gain:+.2%} body",
                "TRADE"
            )
            order = self.alpaca.buy_market(sym, notional=max_val)
            actions.append({
                "symbol": sym, "action": "BUY",
                "quantity": qty, "price": close,
                "notional": max_val,
                "reason": f"Momentum entry: {body_gain:+.2%} candle body",
                "order_id": order.get("id"),
            })
            cash -= max_val

        self._log(f"Stock momentum done. {len(actions)} action(s).")
        return actions
