"""
Earnings Play Strategy (Alpaca)
─────────────────────────────────
Captures earnings momentum: stocks that beat estimates gap up 3-8%
on average. Two approaches:

  Pre-earnings drift:
    Stocks often drift upward in the 5 days before earnings as
    expectations build. Buy 5 days before, sell day before (avoid
    binary earnings risk).

  Post-earnings momentum:
    If a stock gaps up >2% on earnings beat, buy the gap and ride
    momentum for 2-5 days. Statistically, earnings beats continue
    drifting for 3+ days (Post-Earnings Announcement Drift / PEAD).

  Earnings calendar: pulled from Alpha Vantage (free tier) and
  Nasdaq Trader API (no key required).

Watchlist of high-momentum earnings candidates:
  NVDA, MSFT, AAPL, GOOGL, META, AMZN, TSLA, AMD
  JPM, GS, MS (financials)
  NFLX, SPOT (streaming)
"""

import requests
from datetime import datetime, timedelta
from strategies.base import BaseStrategy

# Free earnings calendar endpoint
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"

EARNINGS_WATCHLIST = [
    "NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMZN",
    "TSLA", "AMD", "JPM", "GS", "NFLX", "SPOT", "CRM",
]

PRE_EARNINGS_BUY_DAYS   = 5     # Buy N days before earnings
POST_EARNINGS_MIN_GAP   = 0.02  # Buy post-earnings if gap > 2%
MAX_POSITION_PCT        = 0.07
MIN_TRADE_USD           = 25.0


def fetch_earnings_calendar(days_ahead: int = 7) -> list[dict]:
    """Fetch upcoming earnings from Nasdaq API (free, no key)."""
    events = []
    try:
        for offset in range(days_ahead):
            d    = (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")
            resp = requests.get(
                NASDAQ_EARNINGS_URL,
                params={"date": d},
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                timeout=8
            )
            if resp.status_code != 200:
                continue
            rows = (resp.json().get("data", {}) or {}).get("rows", []) or []
            for row in rows:
                sym = row.get("symbol", "")
                if sym in EARNINGS_WATCHLIST:
                    events.append({
                        "symbol":    sym,
                        "date":      d,
                        "time":      row.get("time", ""),
                        "eps_est":   row.get("epsForecast", ""),
                        "days_away": offset,
                    })
    except Exception as e:
        events.append({"error": str(e)})
    return events


def detect_earnings_gap(alpaca_client, symbol: str) -> dict:
    """
    Check if a stock gapped up significantly today (post-earnings).
    Compares today's open vs yesterday's close.
    """
    try:
        from strategies.technical_engine import fetch_bars
        bars  = fetch_bars(alpaca_client, symbol, limit=5)
        if not bars or len(bars.get("closes", [])) < 2:
            return {"symbol": symbol, "gap": 0}
        opens   = bars["opens"]
        closes  = bars["closes"]
        gap_pct = (opens[-1] - closes[-2]) / closes[-2] if closes[-2] else 0
        return {
            "symbol":    symbol,
            "gap_pct":   round(gap_pct * 100, 2),
            "prev_close":closes[-2],
            "today_open":opens[-1],
            "today_close":closes[-1],
        }
    except Exception as e:
        return {"symbol": symbol, "gap": 0, "error": str(e)}


class EarningsPlayStrategy(BaseStrategy):
    """
    Two-phase earnings strategy:
    1. Pre-earnings drift: buy 5 days before, exit day before earnings
    2. Post-earnings PEAD: buy gap-ups > 2% on earnings day
    """

    def __init__(self, alpaca_client, max_position_pct: float = MAX_POSITION_PCT):
        super().__init__(alpaca_client, max_position_pct)
        self.alpaca = alpaca_client

    def describe(self) -> str:
        return "Buys pre-earnings drift + post-earnings gap-ups (PEAD effect)."

    def run(self) -> list[dict]:
        actions = []

        if not self.alpaca.is_configured():
            self._log("Alpaca not configured.", "WARN")
            return actions

        if not self.alpaca.is_market_open():
            self._log("Market closed — earnings play deferred.")
            return [{"action": "HOLD", "reason": "Market closed"}]

        self._log("Earnings Play: fetching upcoming calendar...")

        portfolio = self.alpaca.get_portfolio_value()
        cash      = self.alpaca.get_cash()
        positions = {p["symbol"]: p for p in self.alpaca.get_positions()}
        calendar  = fetch_earnings_calendar(days_ahead=7)

        if any("error" in e for e in calendar):
            self._log("Earnings calendar unavailable — checking gap-ups only", "WARN")

        # ── Phase 1: Pre-earnings drift ────────────────────────────────
        for event in calendar:
            if "error" in event:
                continue
            sym       = event["symbol"]
            days_away = event["days_away"]

            if days_away == 0:
                # Earnings today — exit any pre-earnings position
                if sym in positions:
                    pos = positions[sym]
                    self._log(f"  EXIT {sym} — earnings today (avoid binary risk)", "TRADE")
                    order = self.alpaca.sell_market(sym, pos["qty"])
                    actions.append({
                        "symbol": sym, "action": "SELL", "quantity": pos["qty"],
                        "reason": "Earnings today — exit pre-earnings position",
                        "order_id": order.get("id"),
                    })

            elif 1 <= days_away <= PRE_EARNINGS_BUY_DAYS:
                if sym in positions:
                    self._log(f"  HOLD {sym} — {days_away}d to earnings, riding drift")
                    continue
                notional = min(portfolio * MAX_POSITION_PCT, cash * 0.2)
                if notional < MIN_TRADE_USD:
                    continue
                bar   = self.alpaca.get_latest_bar(sym)
                price = bar.get("close", 0)
                if not price:
                    continue
                self._log(
                    f"  PRE-EARN BUY {sym} ${notional:.0f} @ ${price:.2f} "
                    f"({days_away}d before earnings on {event['date']})",
                    "TRADE"
                )
                order = self.alpaca.buy_market(sym, notional=notional)
                actions.append({
                    "symbol": sym, "action": "BUY", "notional": notional, "price": price,
                    "reason": f"Pre-earnings drift ({days_away}d before {event['date']})",
                    "order_id": order.get("id"),
                })
                cash -= notional

        # ── Phase 2: Post-earnings gap-up (PEAD) ──────────────────────
        self._log("Checking for post-earnings gap-ups...")
        for sym in EARNINGS_WATCHLIST:
            if sym in positions:
                continue
            gap_data = detect_earnings_gap(self.alpaca, sym)
            gap_pct  = gap_data.get("gap_pct", 0)

            if gap_pct >= POST_EARNINGS_MIN_GAP * 100:
                notional = min(portfolio * MAX_POSITION_PCT, cash * 0.2)
                if notional < MIN_TRADE_USD:
                    continue
                price = gap_data.get("today_open", 0)
                self._log(
                    f"  PEAD BUY {sym} +{gap_pct:.1f}% gap-up ${notional:.0f} @ ${price:.2f}",
                    "TRADE"
                )
                order = self.alpaca.buy_market(sym, notional=notional)
                actions.append({
                    "symbol": sym, "action": "BUY", "notional": notional, "price": price,
                    "gap_pct": gap_pct,
                    "reason": f"Post-earnings gap-up {gap_pct:.1f}% (PEAD)",
                    "order_id": order.get("id"),
                })
                cash -= notional

        self._log(f"Earnings play done. {len(actions)} action(s).")
        return actions
