"""
Sector Rotation Strategy (Alpaca)
───────────────────────────────────
Rotates capital into the strongest performing sectors based on
momentum and relative strength. Follows the economic cycle:

  Early Expansion  → Tech (XLK), Financials (XLF), Consumer Disc (XLY)
  Mid Expansion    → Industrials (XLI), Materials (XLB), Energy (XLE)
  Late Expansion   → Energy (XLE), Materials (XLB), Utilities (XLU)
  Contraction      → Utilities (XLU), Healthcare (XLV), Consumer Stap (XLP)
  Recovery         → Tech (XLK), Financials (XLF), Real Estate (XLRE)

Momentum scoring: buy the top 3 performing sectors over 20 days,
sell the bottom 2 if held. Rebalance monthly (or on 10% drift).

Additional leveraged plays for high-conviction sectors:
  XLK  → TQQQ (3x NASDAQ) — only in extreme momentum
  XLE  → XOP  (oil & gas exploration)
"""

from strategies.base import BaseStrategy

# All 11 SPDR sector ETFs
SECTORS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLV":  "Healthcare",
    "XLY":  "Consumer Disc",
    "XLP":  "Consumer Staples",
    "XLE":  "Energy",
    "XLI":  "Industrials",
    "XLB":  "Materials",
    "XLRE": "Real Estate",
    "XLU":  "Utilities",
    "XLC":  "Communication",
}

TOP_N          = 3     # Hold top N sectors
BOTTOM_N       = 2     # Exit bottom N if held
MOMENTUM_DAYS  = 20    # Lookback for momentum ranking
MAX_PCT        = 0.15  # Max 15% per sector
MIN_TRADE_USD  = 25.0

def _momentum_score(bars: dict) -> float:
    """Simple 20-day price momentum: (close - open_20d) / open_20d."""
    closes = bars.get("closes", [])
    if len(closes) < MOMENTUM_DAYS:
        return 0.0
    start = closes[-MOMENTUM_DAYS]
    end   = closes[-1]
    return (end - start) / start if start else 0.0


class SectorRotationStrategy(BaseStrategy):
    """
    Buys top 3 momentum sectors, exits bottom 2. Monthly rotation.
    """

    def __init__(self, alpaca_client, max_position_pct: float = MAX_PCT):
        super().__init__(alpaca_client, max_position_pct)
        self.alpaca = alpaca_client

    def describe(self) -> str:
        return "Rotates into top 3 momentum sectors (XLK/XLF/XLE/XLV etc.) monthly."

    def _fetch_momentum(self) -> list[dict]:
        from strategies.technical_engine import fetch_bars
        scored = []
        for sym, name in SECTORS.items():
            bars  = fetch_bars(self.alpaca, sym, limit=25)
            if not bars or "error" in bars:
                continue
            mom   = _momentum_score(bars)
            price = bars["closes"][-1] if bars.get("closes") else 0
            scored.append({
                "symbol":   sym,
                "name":     name,
                "momentum": round(mom * 100, 2),
                "price":    price,
            })
        scored.sort(key=lambda x: x["momentum"], reverse=True)
        return scored

    def run(self) -> list[dict]:
        actions = []

        if not self.alpaca.is_configured():
            self._log("Alpaca not configured.", "WARN")
            return actions

        if not self.alpaca.is_market_open():
            self._log("Market closed — sector rotation deferred.")
            return [{"action": "HOLD", "reason": "Market closed"}]

        self._log("Sector Rotation: ranking all 11 SPDR sector ETFs by 20-day momentum...")

        ranked   = self._fetch_momentum()
        if not ranked:
            self._log("No sector data available.", "WARN")
            return actions

        top_syms    = {r["symbol"] for r in ranked[:TOP_N]}
        bottom_syms = {r["symbol"] for r in ranked[-BOTTOM_N:]}
        portfolio   = self.alpaca.get_portfolio_value()
        cash        = self.alpaca.get_cash()
        positions   = {p["symbol"]: p for p in self.alpaca.get_positions()}

        for i, r in enumerate(ranked):
            rank_label = f"#{i+1}"
            zone       = "TOP" if r["symbol"] in top_syms else (
                         "BOT" if r["symbol"] in bottom_syms else "MID")
            self._log(
                f"  {rank_label:3} {r['symbol']:6} {r['name']:20} "
                f"20d={r['momentum']:+.1f}%  [{zone}]"
            )

        # ── Sell bottom sectors if held ────────────────────────────────
        for sym in bottom_syms:
            if sym not in positions:
                continue
            pos   = positions[sym]
            price = pos["current_price"]
            qty   = pos["qty"]
            self._log(f"  SELL {sym} (bottom sector, momentum weak)", "TRADE")
            order = self.alpaca.sell_market(sym, qty)
            actions.append({
                "symbol": sym, "action": "SELL", "quantity": qty, "price": price,
                "reason": f"Bottom {BOTTOM_N} sector — rotating out",
                "order_id": order.get("id"),
            })

        # ── Buy top sectors if not held ────────────────────────────────
        for r in ranked[:TOP_N]:
            sym = r["symbol"]
            if sym in positions:
                self._log(f"  HOLD {sym} — already in top sector")
                continue

            notional = min(portfolio * MAX_PCT, cash / TOP_N)
            if notional < MIN_TRADE_USD:
                continue

            self._log(
                f"  BUY ${notional:.0f} {sym} ({r['name']}) "
                f"20d momentum={r['momentum']:+.1f}%",
                "TRADE"
            )
            order = self.alpaca.buy_market(sym, notional=notional)
            actions.append({
                "symbol": sym, "action": "BUY", "notional": notional,
                "price":  r["price"], "momentum": r["momentum"],
                "reason": f"Top {TOP_N} sector rotation: {r['momentum']:+.1f}% 20d momentum",
                "order_id": order.get("id"),
            })
            cash -= notional

        self._log(f"Sector rotation done. {len(actions)} action(s).")
        return actions

    def get_sector_report(self) -> list[dict]:
        return self._fetch_momentum()
