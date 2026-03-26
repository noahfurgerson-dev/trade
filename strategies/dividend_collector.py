"""
Dividend Collector Strategy (Alpaca)
──────────────────────────────────────
Buys high-yield dividend ETFs and dividend-paying stocks via Alpaca.
Targets a blended yield of ~6-8% annually via:

  SCHD   — Schwab US Dividend Equity ETF     ~3.5% yield
  JEPI   — JPMorgan Equity Premium Income    ~7.5% yield (covered calls)
  JEPQ   — JPMorgan NASDAQ Equity Premium    ~9.5% yield
  O      — Realty Income (Monthly Dividend)  ~5.5% yield
  VYM    — Vanguard High Div Yield ETF       ~2.8% yield
  QYLD   — Global X NASDAQ-100 Covered Call  ~11% yield

Strategy:
  - Allocate a fixed % of Alpaca cash to each ETF
  - Buy if we have cash and don't already hold the position
  - Never allocate more than MAX_POSITION_PCT to one holding
  - Re-runs monthly to DCA into existing positions
"""

from strategies.base import BaseStrategy

# Target holdings and allocation %
DIVIDEND_TARGETS = {
    "SCHD": 20.0,   # Core quality dividend
    "JEPI": 25.0,   # Monthly income + covered calls
    "JEPQ": 20.0,   # NASDAQ income
    "O":    15.0,   # Monthly dividend REIT
    "QYLD": 10.0,   # High yield covered call
    "VYM":  10.0,   # Broad dividend
    # 0% = keep as cash buffer
}

# Blended yield estimates (for income projection)
YIELD_ESTIMATES = {
    "SCHD": 0.035,
    "JEPI": 0.075,
    "JEPQ": 0.095,
    "O":    0.055,
    "QYLD": 0.110,
    "VYM":  0.028,
}

MIN_TRADE_USD  = 10.0   # Minimum order size
CASH_RESERVE   = 0.05   # Keep 5% of capital as cash


class DividendCollectorStrategy(BaseStrategy):
    """
    Buys dividend ETFs via Alpaca to generate passive income.
    Requires AlpacaClient (not RobinhoodClient).
    """

    def __init__(self, alpaca_client, max_position_pct: float = 0.30):
        # Pass alpaca_client as the 'client' to base
        super().__init__(alpaca_client, max_position_pct)
        self.alpaca = alpaca_client

    def describe(self) -> str:
        tickers = ", ".join(DIVIDEND_TARGETS.keys())
        return f"Buys high-yield dividend ETFs ({tickers}) via Alpaca for passive income."

    def estimate_annual_income(self, positions: list[dict]) -> float:
        """Estimate annual dividend income from current positions."""
        total = 0.0
        for p in positions:
            sym = p["symbol"]
            mval = p.get("market_value", 0)
            yield_rate = YIELD_ESTIMATES.get(sym, 0)
            total += mval * yield_rate
        return total

    def run(self) -> list[dict]:
        actions = []

        if not self.alpaca.is_configured():
            self._log("Alpaca not configured — skipping dividend strategy.", "WARN")
            return actions

        if not self.alpaca.is_market_open():
            self._log("Market closed — dividend check skipped.", "INFO")
            return [{"action": "HOLD", "reason": "Market closed"}]

        self._log("Dividend Collector: analysing Alpaca portfolio...")

        # Current state
        try:
            positions = self.alpaca.get_positions()
        except Exception as e:
            self._log(f"Could not fetch Alpaca positions: {e}", "WARN")
            return actions

        cash        = self.alpaca.get_cash()
        buying_pow  = self.alpaca.get_buying_power()
        portfolio   = self.alpaca.get_portfolio_value()

        self._log(f"Alpaca — Cash: ${cash:,.2f}  Portfolio: ${portfolio:,.2f}  BP: ${buying_pow:,.2f}")

        # Map existing positions
        held = {p["symbol"]: p for p in positions}
        investable_cash = cash * (1 - CASH_RESERVE)

        # ── Estimate current annual income ──────────────────────────
        annual_est = self.estimate_annual_income(positions)
        self._log(f"Estimated annual dividend income: ${annual_est:,.2f}")

        # ── Buy under-weight dividend holdings ──────────────────────
        for symbol, target_pct in DIVIDEND_TARGETS.items():
            target_value = portfolio * (target_pct / 100)
            current_value = held.get(symbol, {}).get("market_value", 0.0)
            gap = target_value - current_value

            if gap < MIN_TRADE_USD:
                status = "OK" if current_value >= target_value * 0.95 else "SMALL_GAP"
                self._log(f"  {symbol:6} target ${target_value:,.0f}  held ${current_value:,.0f}  [{status}]")
                continue

            # Cap by available investable cash
            buy_amount = min(gap, investable_cash)
            if buy_amount < MIN_TRADE_USD:
                self._log(f"  {symbol:6} — not enough cash (${investable_cash:.2f} available)")
                continue

            # Don't exceed max position
            max_val = portfolio * self.max_position_pct
            buy_amount = min(buy_amount, max_val - current_value)
            if buy_amount < MIN_TRADE_USD:
                continue

            # Get price
            bar = self.alpaca.get_latest_bar(symbol)
            price = bar.get("close", 0)
            if not price:
                self._log(f"  {symbol:6} — no price data", "WARN")
                continue

            qty = buy_amount / price
            self._log(
                f"  BUY {qty:.4f} {symbol} @ ${price:.2f} (${buy_amount:.2f}) "
                f"— closing gap of ${gap:.2f} vs target",
                "TRADE"
            )
            order = self.alpaca.buy_market(symbol, notional=buy_amount)
            actions.append({
                "symbol"   : symbol,
                "action"   : "BUY",
                "quantity" : qty,
                "price"    : price,
                "notional" : buy_amount,
                "reason"   : f"Dividend DCA — under target by ${gap:.2f}",
                "order_id" : order.get("id"),
                "yield_est": YIELD_ESTIMATES.get(symbol, 0),
            })
            investable_cash -= buy_amount

        self._log(f"Dividend cycle done. {len(actions)} order(s).")
        return actions

    def get_income_report(self) -> dict:
        """Return income estimate without placing orders."""
        try:
            positions   = self.alpaca.get_positions()
            annual      = self.estimate_annual_income(positions)
            monthly     = annual / 12
            daily       = annual / 365
            return {
                "annual_income"  : round(annual, 2),
                "monthly_income" : round(monthly, 2),
                "daily_income"   : round(daily, 2),
                "positions"      : [
                    {
                        "symbol"       : p["symbol"],
                        "market_value" : p["market_value"],
                        "est_yield_pct": YIELD_ESTIMATES.get(p["symbol"], 0) * 100,
                        "est_annual"   : round(p["market_value"] * YIELD_ESTIMATES.get(p["symbol"], 0), 2),
                    }
                    for p in positions if p["symbol"] in YIELD_ESTIMATES
                ],
            }
        except Exception as e:
            return {"error": str(e)}
