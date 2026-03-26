"""
Statistical Pairs Trading (Alpaca)
────────────────────────────────────
Exploits mean-reverting spread between two correlated assets.
When two historically correlated stocks diverge, bet on convergence.

Method — Z-score of the spread:
  spread  = price_A - hedge_ratio * price_B
  z_score = (spread - mean) / std_dev

  z > +2.0 → A is expensive vs B → SHORT A, LONG B  (or just LONG B)
  z < -2.0 → A is cheap  vs B → LONG A, SHORT B     (or just LONG A)
  |z| < 0.5 → spread converged → EXIT position

Pairs selected for high historical correlation (>0.85):
  SPY  / QQQ     — S&P 500 vs NASDAQ (classic)
  MSFT / GOOGL   — Tech giants, AI peers
  JPM  / BAC     — Bank pairs
  GLD  / SLV     — Gold vs Silver
  XLE  / XOP     — Energy sector ETF vs E&P
  NVDA / AMD     — Semiconductor peers
  AAPL / MSFT    — Mega-cap tech

Since Alpaca doesn't support short selling easily in this context,
we use a LONG-ONLY variant:
  z < -2.0 → Buy the underperformer (it will revert up)
  z > +2.0 → Sell any held position in the overperformer
  z → 0    → Exit all pairs positions
"""

from strategies.base import BaseStrategy
from strategies.technical_engine import fetch_bars

PAIRS = [
    ("SPY",  "QQQ",   1.0,   "S&P500 vs NASDAQ"),
    ("MSFT", "GOOGL", 1.0,   "Microsoft vs Alphabet"),
    ("JPM",  "BAC",   3.5,   "JPMorgan vs BofA"),
    ("NVDA", "AMD",   2.5,   "NVIDIA vs AMD"),
    ("AAPL", "MSFT",  0.9,   "Apple vs Microsoft"),
    ("XLE",  "XOP",   0.6,   "Energy ETF vs E&P"),
]

Z_ENTRY    =  2.0    # Enter when spread diverges this many std devs
Z_EXIT     =  0.5    # Exit when spread converges
LOOKBACK   =  30     # Days for mean/std calculation
MAX_PCT    =  0.08   # 8% per pair trade
MIN_TRADE  =  25.0


def _zscore(values: list[float]) -> float:
    if len(values) < 5:
        return 0.0
    mean = sum(values) / len(values)
    std  = (sum((x - mean)**2 for x in values) / len(values)) ** 0.5
    return (values[-1] - mean) / std if std else 0.0


class PairsTradingStrategy(BaseStrategy):
    """
    Long-only statistical pairs: buys the laggard when spread diverges > 2σ.
    Exits when spread converges back to within 0.5σ.
    """

    def __init__(self, alpaca_client, max_position_pct: float = MAX_PCT):
        super().__init__(alpaca_client, max_position_pct)
        self.alpaca = alpaca_client

    def describe(self) -> str:
        return "Statistical arbitrage: buys laggard when correlated pairs diverge > 2σ."

    def _analyse_pair(self, sym_a: str, sym_b: str,
                       hedge: float, label: str) -> dict:
        bars_a = fetch_bars(self.alpaca, sym_a, limit=LOOKBACK + 5)
        bars_b = fetch_bars(self.alpaca, sym_b, limit=LOOKBACK + 5)

        if not bars_a or not bars_b or "error" in bars_a or "error" in bars_b:
            return {"label": label, "error": "No data"}

        closes_a = bars_a["closes"][-LOOKBACK:]
        closes_b = bars_b["closes"][-LOOKBACK:]
        n        = min(len(closes_a), len(closes_b))
        if n < 10:
            return {"label": label, "error": "Insufficient bars"}

        spreads = [closes_a[i] - hedge * closes_b[i] for i in range(n)]
        z       = _zscore(spreads)
        price_a = closes_a[-1]
        price_b = closes_b[-1]

        action = "HOLD"
        trade_sym = None
        if z < -Z_ENTRY:
            action    = "BUY_A"     # A is cheap relative to B
            trade_sym = sym_a
        elif z > Z_ENTRY:
            action    = "BUY_B"     # B is cheap relative to A
            trade_sym = sym_b
        elif abs(z) < Z_EXIT:
            action    = "EXIT"

        return {
            "label":     label,
            "sym_a":     sym_a,
            "sym_b":     sym_b,
            "price_a":   price_a,
            "price_b":   price_b,
            "spread":    round(spreads[-1], 4),
            "z_score":   round(z, 3),
            "action":    action,
            "trade_sym": trade_sym,
            "hedge":     hedge,
        }

    def run(self) -> list[dict]:
        actions = []

        if not self.alpaca.is_configured():
            self._log("Alpaca not configured.", "WARN")
            return actions

        if not self.alpaca.is_market_open():
            self._log("Market closed — pairs trading deferred.")
            return [{"action": "HOLD", "reason": "Market closed"}]

        self._log("Pairs Trading: analysing spread z-scores...")

        portfolio = self.alpaca.get_portfolio_value()
        cash      = self.alpaca.get_cash()
        positions = {p["symbol"]: p for p in self.alpaca.get_positions()}

        for sym_a, sym_b, hedge, label in PAIRS:
            result = self._analyse_pair(sym_a, sym_b, hedge, label)
            if "error" in result:
                self._log(f"  {label}: {result['error']}", "WARN")
                continue

            z = result["z_score"]
            self._log(
                f"  {label:30} z={z:+.2f}  "
                f"{sym_a}=${result['price_a']:,.2f}  "
                f"{sym_b}=${result['price_b']:,.2f}  → {result['action']}"
            )

            if result["action"] == "EXIT":
                for sym in [sym_a, sym_b]:
                    if sym in positions:
                        qty = positions[sym]["qty"]
                        self._log(f"  EXIT pairs position {sym} (spread converged z={z:.2f})", "TRADE")
                        order = self.alpaca.sell_market(sym, qty)
                        actions.append({
                            "symbol": sym, "action": "SELL", "quantity": qty,
                            "reason": f"Pairs convergence z={z:.2f}", "order_id": order.get("id"),
                        })

            elif result["action"] in ("BUY_A", "BUY_B"):
                sym = result["trade_sym"]
                if sym in positions:
                    self._log(f"  SKIP {sym} — already in pairs position")
                    continue

                notional = min(portfolio * MAX_PCT, cash * 0.3)
                if notional < MIN_TRADE:
                    continue

                price = result["price_a"] if sym == sym_a else result["price_b"]
                self._log(
                    f"  BUY ${notional:.0f} {sym} @ ${price:.2f} "
                    f"(pairs divergence z={z:+.2f} σ on {label})",
                    "TRADE"
                )
                order = self.alpaca.buy_market(sym, notional=notional)
                actions.append({
                    "symbol": sym, "action": "BUY", "notional": notional, "price": price,
                    "z_score": z, "pair": label,
                    "reason": f"Pairs divergence: {label} z={z:+.2f}σ",
                    "order_id": order.get("id"),
                })
                cash -= notional

        self._log(f"Pairs trading done. {len(actions)} action(s).")
        return actions

    def get_pairs_report(self) -> list[dict]:
        results = []
        for sym_a, sym_b, hedge, label in PAIRS:
            r = self._analyse_pair(sym_a, sym_b, hedge, label)
            results.append(r)
        return results
