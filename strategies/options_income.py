"""
Options Income Strategy (Alpaca)
──────────────────────────────────
Systematic options premium collection via two approaches:

  1. Cash-Secured Puts (CSP)
     Sell put options on stocks you'd be happy to own.
     Collect premium. If assigned, you own shares at a discount.
     Target: 1-3% premium per 30-day cycle = 12-36% annualised.

  2. Covered Calls (CC)
     Sell call options against stock you already hold.
     Collect premium while holding long equity.
     Target: 1-2% premium per 30-day cycle.

NOTE: Alpaca supports options trading on their platform.
This module tracks the income strategy and provides execution guidance.
For programmatic options via Alpaca API, you need an options-enabled account.

If options API is unavailable, this strategy logs recommendations and
uses a QYLD/JEPI ETF proxy approach (buy covered-call ETFs = outsource
the options writing to professionals).
"""

import json
import os
from datetime import datetime, timedelta
from strategies.base import BaseStrategy

# Preferred underlyings for options writing
CSP_CANDIDATES = [
    # (symbol, max_notional_per_trade, target_delta)
    ("SPY",  5000, 0.20),    # S&P 500 ETF — liquid, safe
    ("QQQ",  3000, 0.20),    # NASDAQ ETF
    ("AAPL", 2000, 0.25),    # Apple — highly liquid
    ("MSFT", 2000, 0.25),    # Microsoft
    ("NVDA", 1500, 0.20),    # NVIDIA — high premium
]

CC_ETF_PROXIES = [
    # Covered-call ETFs — buy these to earn options income without writing
    ("JEPI",  0.25),  # ~7-9% yield, S&P covered calls
    ("JEPQ",  0.20),  # ~9-11% yield, NASDAQ covered calls
    ("QYLD",  0.15),  # ~10-12% yield, NASDAQ covered calls
    ("XYLD",  0.10),  # ~9% yield, S&P covered calls
]

# P&L data file
DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "options_income.json")


def _load_data() -> dict:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"trades": [], "total_premium_collected": 0.0, "positions": []}


def _save_data(d: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)


def log_options_trade(
    symbol: str, strategy_type: str, premium: float,
    expiry: str, strike: float, qty: int = 1, note: str = ""
):
    """Log a manually-executed options trade for income tracking."""
    d = _load_data()
    d["trades"].append({
        "date": datetime.now().isoformat(),
        "symbol": symbol,
        "type": strategy_type,   # "CSP" or "CC"
        "premium": premium,
        "expiry": expiry,
        "strike": strike,
        "qty": qty,
        "note": note,
        "status": "open",
    })
    d["total_premium_collected"] = sum(t["premium"] for t in d["trades"])
    _save_data(d)
    return d


def get_options_summary() -> dict:
    d = _load_data()
    trades = d.get("trades", [])
    open_trades   = [t for t in trades if t.get("status") == "open"]
    closed_trades = [t for t in trades if t.get("status") == "closed"]
    this_month    = datetime.now().strftime("%Y-%m")
    monthly = sum(t["premium"] for t in trades if t["date"].startswith(this_month))
    return {
        "total_premium":   d.get("total_premium_collected", 0),
        "monthly_premium": monthly,
        "open_positions":  len(open_trades),
        "total_trades":    len(trades),
        "trades":          trades[-20:],  # Last 20
    }


class OptionsIncomeStrategy(BaseStrategy):
    """
    Earns options premium via CSPs and Covered Calls.
    Uses CC ETF proxies (JEPI/JEPQ/QYLD) when direct options API unavailable.
    Provides trade recommendations and tracks premium income.
    """

    def __init__(self, alpaca_client, max_position_pct: float = 0.12):
        super().__init__(alpaca_client, max_position_pct)
        self.alpaca = alpaca_client

    def describe(self) -> str:
        return "Earns options premium via Covered Calls & Cash-Secured Puts (+ CC ETF proxies)."

    def _check_options_available(self) -> bool:
        """Check if Alpaca options trading is available."""
        try:
            acct = self.alpaca.get_account()
            # Alpaca accounts with options enabled have options_approved_level > 0
            return acct.get("options_approved_level", 0) > 0
        except Exception:
            return False

    def generate_csp_recommendations(self) -> list[dict]:
        """Generate CSP trade recommendations based on current prices."""
        recommendations = []
        for symbol, max_notional, target_delta in CSP_CANDIDATES:
            bar = self.alpaca.get_latest_bar(symbol)
            if bar.get("error"):
                continue
            price = bar.get("close", 0)
            if not price:
                continue

            # Approximate strike (target_delta ~= OTM by 5-8% for 30-day)
            strike_pct = 1 - (target_delta * 0.35)   # Rough approximation
            strike     = round(price * strike_pct, 0)
            # Rough premium estimate (0.5-1.5% of strike per 30 days)
            est_premium_pct = 0.01   # 1% conservative estimate
            contracts = max(1, int(max_notional / (price * 100)))
            est_premium = round(strike * 100 * contracts * est_premium_pct, 2)

            recommendations.append({
                "symbol":         symbol,
                "type":           "CSP",
                "current_price":  price,
                "suggested_strike": strike,
                "contracts":      contracts,
                "max_collateral": strike * 100 * contracts,
                "est_premium_30d": est_premium,
                "est_annual_yield": est_premium_pct * 12 * 100,
                "rationale":     f"Sell {contracts}x {symbol} ${strike:.0f}P. "
                                 f"Collect ~${est_premium:.0f} premium. "
                                 f"Breakeven: ${strike - est_premium/contracts/100:.2f}",
            })

        return recommendations

    def run(self) -> list[dict]:
        actions = []

        if not self.alpaca.is_configured():
            self._log("Alpaca not configured — skipping options income.", "WARN")
            return actions

        market_open = self.alpaca.is_market_open()

        # ── Check if options API is available ────────────────────────
        has_options = self._check_options_available()

        if has_options:
            self._log("Options account detected — generating live recommendations.")
        else:
            self._log("Options API not enabled — using CC ETF proxy approach.")

        cash      = self.alpaca.get_cash()
        portfolio = self.alpaca.get_portfolio_value()

        # ── CC ETF Proxy: buy income ETFs if not holding ─────────────
        if market_open:
            try:
                positions = self.alpaca.get_positions()
                held = {p["symbol"]: p for p in positions}

                for sym, alloc_pct in CC_ETF_PROXIES:
                    target_val = portfolio * alloc_pct
                    current_val = held.get(sym, {}).get("market_value", 0)
                    gap = target_val - current_val

                    if gap < 25:
                        self._log(f"  {sym} — OK (${current_val:,.0f} vs target ${target_val:,.0f})")
                        continue

                    buy_amt = min(gap, cash * 0.25)
                    if buy_amt < 25:
                        continue

                    bar = self.alpaca.get_latest_bar(sym)
                    price = bar.get("close", 0)
                    if not price:
                        continue

                    qty = buy_amt / price
                    self._log(
                        f"BUY {qty:.4f} {sym} @ ${price:.2f} (${buy_amt:.2f}) — CC ETF income proxy",
                        "TRADE"
                    )
                    order = self.alpaca.buy_market(sym, notional=buy_amt)
                    actions.append({
                        "symbol": sym, "action": "BUY",
                        "quantity": qty, "price": price, "notional": buy_amt,
                        "reason": "CC ETF income proxy (options premium outsourced)",
                        "order_id": order.get("id"),
                    })
                    cash -= buy_amt

            except Exception as e:
                self._log(f"CC ETF proxy error: {e}", "WARN")

        # ── Generate CSP recommendations (logged, not auto-executed) ──
        self._log("Generating CSP recommendations for manual execution...")
        recs = self.generate_csp_recommendations()
        for r in recs:
            self._log(
                f"  RECOMMEND {r['type']} {r['symbol']}: {r['rationale'][:80]}",
                "INFO"
            )
            actions.append({
                "symbol":     r["symbol"],
                "action":     "RECOMMEND_CSP",
                "auto_executed": False,
                "recommendation": r,
                "reason": r["rationale"],
            })

        # ── Summary ───────────────────────────────────────────────────
        summary = get_options_summary()
        self._log(
            f"Options income summary — Total premium: ${summary['total_premium']:,.2f}  "
            f"Monthly: ${summary['monthly_premium']:,.2f}  "
            f"Open positions: {summary['open_positions']}"
        )

        self._log(f"Options income cycle done. {len(actions)} action(s).")
        return actions
