"""
Treasury & Fixed Income Strategy (Alpaca)
───────────────────────────────────────────
Earns safe, government-backed yield on idle cash via Treasury ETFs.

Current yields (as of 2025/2026):
  SGOV  — iShares 0-3 Month Treasury    ~5.2% yield  (cash equivalent)
  BIL   — SPDR 1-3 Month T-Bill ETF     ~5.1% yield
  SHY   — iShares 1-3 Year Treasury     ~4.8% yield
  IEF   — iShares 7-10 Year Treasury    ~4.5% yield
  TLT   — iShares 20+ Year Treasury     ~4.8% yield  (rate sensitive)
  TIPS  — iShares TIPS Bond ETF         ~3.5% real yield (inflation protected)
  LQD   — iShares Corp Bond ETF         ~5.3% yield  (investment grade)
  HYG   — iShares High Yield Corp       ~7.2% yield  (high risk)

Strategy:
  - Short duration (SGOV/BIL): Park idle cash — essentially a money market
  - Medium duration (SHY/IEF): Earn term premium
  - Inflation protection (TIPS): Hedge rising prices
  - Corp bonds (LQD): Boost yield with modest credit risk
  Never allocate to long-duration in a rising rate environment.
"""

from strategies.base import BaseStrategy

# ── Allocation tiers ──────────────────────────────────────────────────────────

# Conservative (capital preservation)
CONSERVATIVE = {
    "SGOV": 0.50,   # ~5.2% — effectively cash
    "BIL":  0.30,   # ~5.1%
    "SHY":  0.20,   # ~4.8%
}

# Moderate (balance yield vs duration risk)
MODERATE = {
    "SGOV": 0.30,
    "BIL":  0.20,
    "SHY":  0.20,
    "IEF":  0.15,
    "LQD":  0.15,
}

# Aggressive (maximize yield)
AGGRESSIVE = {
    "SGOV": 0.15,
    "IEF":  0.20,
    "TLT":  0.15,
    "TIPS": 0.20,
    "LQD":  0.20,
    "HYG":  0.10,
}

# ETF metadata for display
ETF_META = {
    "SGOV": {"name": "0-3 Month Treasury",  "yield_est": 0.052, "duration": "< 1 mo",  "risk": "LOW"},
    "BIL":  {"name": "1-3 Month T-Bill",    "yield_est": 0.051, "duration": "2 mo",    "risk": "LOW"},
    "SHY":  {"name": "1-3 Year Treasury",   "yield_est": 0.048, "duration": "1.9 yr",  "risk": "LOW"},
    "IEF":  {"name": "7-10 Year Treasury",  "yield_est": 0.045, "duration": "7.5 yr",  "risk": "MED"},
    "TLT":  {"name": "20+ Year Treasury",   "yield_est": 0.048, "duration": "16 yr",   "risk": "HIGH"},
    "TIPS": {"name": "Inflation Protected",  "yield_est": 0.035, "duration": "7.5 yr",  "risk": "MED"},
    "LQD":  {"name": "IG Corp Bonds",        "yield_est": 0.053, "duration": "8.5 yr",  "risk": "MED"},
    "HYG":  {"name": "High Yield Corp",      "yield_est": 0.072, "duration": "3.5 yr",  "risk": "HIGH"},
}

MIN_CASH_RESERVE    = 0.05    # Always keep 5% cash
MIN_TRADE_USD       = 10.0
CASH_ALLOC_PCT      = 0.30    # Allocate up to 30% of portfolio to bonds


class TreasuryIncomeStrategy(BaseStrategy):
    """
    Parks idle Alpaca cash into Treasury/bond ETFs for risk-free income.
    Default: conservative allocation (SGOV + BIL).
    """

    def __init__(self, alpaca_client, risk_profile: str = "conservative",
                 max_position_pct: float = 0.20):
        super().__init__(alpaca_client, max_position_pct)
        self.alpaca = alpaca_client
        profile_map = {
            "conservative": CONSERVATIVE,
            "moderate":     MODERATE,
            "aggressive":   AGGRESSIVE,
        }
        self.allocation = profile_map.get(risk_profile, CONSERVATIVE)
        self.risk_profile = risk_profile

    def describe(self) -> str:
        tickers = ", ".join(self.allocation.keys())
        return f"Parks idle cash in Treasury ETFs ({tickers}) — ~{self._blended_yield():.1%} yield."

    def _blended_yield(self) -> float:
        total = 0.0
        for sym, wt in self.allocation.items():
            total += ETF_META.get(sym, {}).get("yield_est", 0) * wt
        return total

    def estimate_annual_income(self, positions: list[dict]) -> float:
        total = 0.0
        for p in positions:
            sym = p["symbol"]
            meta = ETF_META.get(sym, {})
            total += p.get("market_value", 0) * meta.get("yield_est", 0)
        return total

    def run(self) -> list[dict]:
        actions = []

        if not self.alpaca.is_configured():
            self._log("Alpaca not configured — skipping treasury income.", "WARN")
            return actions

        if not self.alpaca.is_market_open():
            self._log("Market closed — treasury check skipped.")
            return [{"action": "HOLD", "reason": "Market closed"}]

        self._log(f"Treasury Income ({self.risk_profile}): checking bond ladder...")

        try:
            positions = self.alpaca.get_positions()
        except Exception as e:
            self._log(f"Cannot fetch positions: {e}", "WARN")
            return actions

        cash      = self.alpaca.get_cash()
        portfolio = self.alpaca.get_portfolio_value()
        held      = {p["symbol"]: p for p in positions}

        # Amount earmarked for bonds
        bond_budget  = portfolio * CASH_ALLOC_PCT
        investable   = min(cash * (1 - MIN_CASH_RESERVE), bond_budget)

        annual_income = self.estimate_annual_income(positions)
        self._log(
            f"Portfolio ${portfolio:,.2f}  Cash ${cash:,.2f}  "
            f"Bond budget ${bond_budget:,.2f}  "
            f"Est. annual income ${annual_income:,.2f}"
        )

        for sym, target_weight in self.allocation.items():
            target_val  = bond_budget * target_weight
            current_val = held.get(sym, {}).get("market_value", 0)
            gap         = target_val - current_val

            if gap < MIN_TRADE_USD:
                meta = ETF_META.get(sym, {})
                self._log(
                    f"  {sym:6} ${current_val:,.0f} / ${target_val:,.0f} "
                    f"yield ~{meta.get('yield_est', 0):.1%} [{meta.get('risk','?')} risk]  — OK"
                )
                continue

            buy_amt = min(gap, investable)
            if buy_amt < MIN_TRADE_USD:
                self._log(f"  {sym:6} — insufficient investable cash (${investable:.2f})")
                continue

            bar   = self.alpaca.get_latest_bar(sym)
            price = bar.get("close", 0)
            if not price:
                continue

            qty = buy_amt / price
            meta = ETF_META.get(sym, {})
            self._log(
                f"  BUY {qty:.4f} {sym} @ ${price:.2f} (${buy_amt:.2f}) "
                f"— ~{meta.get('yield_est', 0):.1%} yield, {meta.get('duration', '?')} duration",
                "TRADE"
            )
            order = self.alpaca.buy_market(sym, notional=buy_amt)
            actions.append({
                "symbol":    sym,
                "action":    "BUY",
                "quantity":  qty,
                "price":     price,
                "notional":  buy_amt,
                "yield_est": meta.get("yield_est", 0),
                "duration":  meta.get("duration", "?"),
                "reason":    f"Treasury income — {meta.get('name', sym)} ~{meta.get('yield_est', 0):.1%}",
                "order_id":  order.get("id"),
            })
            investable -= buy_amt

        self._log(f"Treasury income done. {len(actions)} order(s). Blended yield ~{self._blended_yield():.1%}")
        return actions

    def get_income_report(self) -> dict:
        try:
            positions = self.alpaca.get_positions()
            annual = self.estimate_annual_income(positions)
            return {
                "annual_income":  round(annual, 2),
                "monthly_income": round(annual / 12, 2),
                "blended_yield":  self._blended_yield(),
                "risk_profile":   self.risk_profile,
                "holdings": [
                    {
                        "symbol":      p["symbol"],
                        "market_value": p["market_value"],
                        "yield_est":   ETF_META.get(p["symbol"], {}).get("yield_est", 0),
                        "annual_est":  round(p["market_value"] * ETF_META.get(p["symbol"], {}).get("yield_est", 0), 2),
                    }
                    for p in positions if p["symbol"] in ETF_META
                ],
            }
        except Exception as e:
            return {"error": str(e)}
