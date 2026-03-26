"""
Theta Decay (Options Premium Selling) Strategy
───────────────────────────────────────────────
Sells covered calls and cash-secured puts to collect premium income.

Logic:
  - Covered calls: Sell OTM calls on held positions (~5-10% above current price, 21-45 DTE)
  - Cash-secured puts: Sell OTM puts on stocks we'd like to own (~5% below, 21-45 DTE)
  - Target: collect premium = 1-2% of underlying per month
  - Close at 50% profit or 21 DTE (whichever comes first)

Note: Options trading requires Level 2+ options approval on Robinhood.
"""

import robin_stocks.robinhood as rh
from strategies.base import BaseStrategy
from datetime import datetime, timedelta

# Tickers eligible for cash-secured puts (high IV, liquid options)
CSP_CANDIDATES = ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ", "META", "AMZN"]
TARGET_DELTA = 0.20        # ~20 delta (OTM)
MIN_PREMIUM_PCT = 0.01     # Minimum 1% of strike price
MIN_DTE = 21
MAX_DTE = 45


class ThetaDecayStrategy(BaseStrategy):

    def describe(self) -> str:
        return "Sells OTM covered calls & cash-secured puts to harvest theta (premium income)."

    def run(self) -> list[dict]:
        actions = []
        self._log("Scanning options premium opportunities...")

        positions = {p["ticker"]: p for p in self.client.get_positions()}
        cash = self.client.get_cash()

        # ── Covered calls on existing positions ───────────────────────
        for ticker, pos in positions.items():
            if pos["quantity"] < 100:
                continue  # Need 100 shares minimum for covered call
            try:
                chain = rh.find_options_for_stock_by_expiration_and_strike(
                    ticker,
                    expirationDate=self._target_expiry(),
                    strikePrice=None,
                    optionType="call",
                )
                if not chain:
                    continue
                current_price = pos["current_price"]
                # Find strike ~7% OTM
                target_strike = current_price * 1.07
                best = self._find_best_option(chain, target_strike, MIN_PREMIUM_PCT, current_price)
                if best:
                    self._log(
                        f"SELL CALL {ticker} ${best['strike']} exp {best['expiration']} "
                        f"@ ${best['premium']:.2f} premium", "TRADE"
                    )
                    actions.append({
                        "ticker": ticker, "action": "SELL_CALL",
                        "strike": best["strike"], "expiration": best["expiration"],
                        "premium": best["premium"],
                        "reason": "Covered call for income",
                    })
            except Exception as e:
                self._log(f"Options chain error for {ticker}: {e}", "WARN")

        # ── Cash-secured puts on desired stocks ───────────────────────
        for ticker in CSP_CANDIDATES:
            if ticker in positions:
                continue
            try:
                quote = self.client.get_quote(ticker)
                current_price = quote.get("price", 0)
                if not current_price:
                    continue

                # Need cash to secure the put (strike * 100)
                target_strike = current_price * 0.95  # 5% OTM
                required_cash = target_strike * 100
                if cash < required_cash:
                    self._log(f"SKIP {ticker} CSP — insufficient cash (need ${required_cash:.0f})", "WARN")
                    continue

                chain = rh.find_options_for_stock_by_expiration_and_strike(
                    ticker,
                    expirationDate=self._target_expiry(),
                    strikePrice=None,
                    optionType="put",
                )
                if not chain:
                    continue
                best = self._find_best_option(chain, target_strike, MIN_PREMIUM_PCT, current_price)
                if best:
                    self._log(
                        f"SELL PUT {ticker} ${best['strike']} exp {best['expiration']} "
                        f"@ ${best['premium']:.2f} premium", "TRADE"
                    )
                    actions.append({
                        "ticker": ticker, "action": "SELL_PUT",
                        "strike": best["strike"], "expiration": best["expiration"],
                        "premium": best["premium"],
                        "reason": "Cash-secured put for income/entry",
                    })
                    cash -= required_cash  # Reserve cash
            except Exception as e:
                self._log(f"Options chain error for {ticker}: {e}", "WARN")

        # ── Close profitable existing options positions ────────────────
        opts = self.client.get_options_positions()
        for opt in opts:
            # Close at 50% profit (buy back at half premium)
            self._log(f"Checking {opt['ticker']} {opt['type']} position for 50% profit close...")
            # Full implementation would compare current option price to entry price
            # Simplified: just log the position
            actions.append({
                "ticker": opt["ticker"], "action": "HOLD_OPTION",
                "type": opt["type"], "qty": opt["quantity"],
                "reason": "Monitoring for 50% profit target",
            })

        self._log(f"Theta scan complete. {len(actions)} action(s).")
        return actions

    def _target_expiry(self) -> str:
        """Return expiry date string ~30 DTE from today."""
        target = datetime.today() + timedelta(days=30)
        # Roll to nearest Friday
        while target.weekday() != 4:
            target += timedelta(days=1)
        return target.strftime("%Y-%m-%d")

    def _find_best_option(self, chain: list, target_strike: float,
                           min_premium_pct: float, underlying: float) -> dict | None:
        """Find the option closest to target strike with sufficient premium."""
        best = None
        best_dist = float("inf")
        for opt in chain:
            try:
                strike = float(opt.get("strike_price", 0))
                premium = float(opt.get("adjusted_mark_price") or opt.get("mark_price") or 0)
                dist = abs(strike - target_strike)
                prem_pct = premium / underlying if underlying else 0
                if prem_pct >= min_premium_pct and dist < best_dist:
                    best_dist = dist
                    best = {
                        "strike": strike,
                        "expiration": opt.get("expiration_date"),
                        "premium": premium,
                        "delta": float(opt.get("delta") or 0),
                    }
            except Exception:
                continue
        return best
