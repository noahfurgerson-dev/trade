"""
AI Signal Generator — Claude-Powered Market Intelligence
──────────────────────────────────────────────────────────
Uses the Anthropic API (already in your .env) to analyse live
market data, portfolio state, and macro context, then produces
structured BUY / SELL / HOLD recommendations with confidence scores.

Each call costs ~$0.01-0.03 of API credits. Run once per session
or on a schedule — not in tight loops.
"""

import os
import json
from datetime import datetime
from anthropic import Anthropic
from strategies.base import BaseStrategy

CRYPTO_UNIVERSE = ["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "ADA-USD", "AVAX-USD"]

SIGNAL_SCHEMA = """
Return ONLY valid JSON. No prose. Example:
{
  "signals": [
    {
      "symbol": "BTC-USD",
      "action": "BUY",
      "confidence": 0.72,
      "rationale": "Strong momentum above 200d MA, Fear & Greed at 28 (fear zone)",
      "suggested_allocation_pct": 15
    }
  ],
  "market_summary": "One sentence on overall market conditions.",
  "risk_level": "medium"
}
Confidence range: 0.0 (no conviction) to 1.0 (very high conviction).
action must be one of: BUY, SELL, HOLD.
risk_level must be one of: low, medium, high.
"""


class AISignalStrategy(BaseStrategy):

    def __init__(self, client, max_position_pct=0.15):
        super().__init__(client, max_position_pct)
        self._anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        self.last_signals: list[dict] = []
        self.last_summary: str = ""
        self.last_risk: str = "unknown"

    def describe(self) -> str:
        return "Claude AI analyses portfolio + market data and generates BUY/SELL/HOLD signals."

    def run(self) -> list[dict]:
        actions = []
        self._log("Querying Claude for market signals...")

        # ── Gather context ─────────────────────────────────────────
        holdings = self.client.get_holdings()
        cash = self.client.get_cash()
        equity = self.client.get_total_equity()

        quotes = {}
        for pair in CRYPTO_UNIVERSE:
            q = self.client.get_quote(pair)
            if q.get("price"):
                quotes[pair] = round(float(q["price"]), 4)

        portfolio_ctx = {
            "total_equity_usd": round(equity, 2),
            "cash_usd": round(cash, 2),
            "holdings": [
                {
                    "symbol": h["pair"],
                    "quantity": h["quantity"],
                    "current_price": h["current_price"],
                    "market_value": round(h["market_value"], 2),
                    "unrealized_pnl_pct": round(h["pnl_pct"], 2),
                }
                for h in holdings
            ],
            "live_prices": quotes,
            "timestamp_utc": datetime.utcnow().isoformat(),
        }

        prompt = f"""You are a disciplined crypto trading analyst.
Analyse the portfolio and live market snapshot below and return trading signals.

PORTFOLIO SNAPSHOT:
{json.dumps(portfolio_ctx, indent=2)}

INSTRUCTIONS:
- Consider momentum, risk management, and portfolio concentration.
- Never suggest allocating more than 20% of equity to a single asset.
- Flag if cash allocation is too low (< 10% of equity) — keep dry powder.
- Be conservative when equity is below $5,000.
- Prioritise capital preservation over aggressive gains.

{SIGNAL_SCHEMA}"""

        try:
            response = self._anthropic.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)

            self.last_signals = result.get("signals", [])
            self.last_summary = result.get("market_summary", "")
            self.last_risk = result.get("risk_level", "unknown")

            self._log(f"Market summary: {self.last_summary}")
            self._log(f"Risk level: {self.last_risk.upper()}")

            for sig in self.last_signals:
                symbol = sig["symbol"]
                action = sig["action"]
                conf = sig["confidence"]
                rationale = sig["rationale"]
                alloc_pct = sig.get("suggested_allocation_pct", 5)

                self._log(
                    f"{action} {symbol} — conf {conf:.0%} — {rationale[:80]}",
                    "TRADE" if action in ("BUY", "SELL") else "INFO",
                )

                # Only auto-execute high-confidence signals (>= 0.75)
                if conf >= 0.75 and action == "BUY":
                    price = quotes.get(symbol, 0)
                    if price and cash > 50:
                        notional = min(equity * (alloc_pct / 100), cash * 0.9)
                        asset_qty = notional / price
                        self._log(f"AUTO-EXECUTE: BUY {asset_qty:.6f} {symbol} (${notional:.2f})", "TRADE")
                        order = self.client.buy_market(symbol, asset_qty)
                        actions.append({
                            "symbol": symbol, "action": "BUY",
                            "quantity": asset_qty, "price": price,
                            "notional": notional, "confidence": conf,
                            "reason": rationale,
                            "order_id": order.get("id"),
                        })

                elif conf >= 0.75 and action == "SELL":
                    holding = next((h for h in holdings if h["pair"] == symbol), None)
                    if holding and holding["quantity"] > 0:
                        self._log(f"AUTO-EXECUTE: SELL {holding['quantity']} {symbol}", "TRADE")
                        order = self.client.sell_market(symbol, holding["quantity"])
                        actions.append({
                            "symbol": symbol, "action": "SELL",
                            "quantity": holding["quantity"],
                            "price": holding["current_price"],
                            "confidence": conf, "reason": rationale,
                            "order_id": order.get("id"),
                        })
                else:
                    actions.append({
                        "symbol": symbol, "action": action,
                        "confidence": conf, "reason": rationale,
                        "auto_executed": False,
                    })

        except json.JSONDecodeError as e:
            self._log(f"Failed to parse AI response as JSON: {e}", "WARN")
        except Exception as e:
            self._log(f"AI signal error: {e}", "WARN")

        self._log(f"AI cycle complete. {len(self.last_signals)} signal(s), {len(actions)} action(s).")
        return actions
