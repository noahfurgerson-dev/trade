"""
Fear & Greed Contrarian Strategy
──────────────────────────────────
Uses the Crypto Fear & Greed Index (free, no API key needed).
Classic contrarian logic: be greedy when others are fearful.

  Index 0–24  = Extreme Fear  → STRONG BUY
  Index 25–44 = Fear          → BUY
  Index 45–55 = Neutral       → HOLD
  Index 56–74 = Greed         → REDUCE / HOLD
  Index 75–100= Extreme Greed → SELL / TAKE PROFIT

Source: https://api.alternative.me/fng/
"""

import requests
from strategies.base import BaseStrategy

FNG_URL = "https://api.alternative.me/fng/?limit=7&format=json"

# Thresholds
EXTREME_FEAR_BUY  = 25    # Score ≤ 25 → strong buy signal
FEAR_BUY          = 44    # Score ≤ 44 → moderate buy
GREED_REDUCE      = 65    # Score ≥ 65 → start trimming
EXTREME_GREED_SELL= 80    # Score ≥ 80 → take profits aggressively

from core.crypto_universe import get_pairs_for as _gpf
BUY_PAIRS   = _gpf("fear_greed")
SELL_PAIRS  = _gpf("fear_greed")


def fetch_fear_greed() -> dict:
    """Fetch latest Fear & Greed data. Returns {value, label, history}"""
    resp = requests.get(FNG_URL, timeout=8)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    if not data:
        return {}
    latest = data[0]
    return {
        "value": int(latest["value"]),
        "label": latest["value_classification"],
        "history": [
            {"value": int(d["value"]), "label": d["value_classification"]}
            for d in data
        ],
    }


class FearGreedStrategy(BaseStrategy):

    def __init__(self, client, max_position_pct=0.12):
        super().__init__(client, max_position_pct)
        self.last_index: dict = {}

    def describe(self) -> str:
        return "Buys crypto on Extreme Fear (≤25), takes profits on Extreme Greed (≥80)."

    def run(self) -> list[dict]:
        actions = []
        self._log("Fetching Fear & Greed Index...")

        try:
            fng = fetch_fear_greed()
        except Exception as e:
            self._log(f"Failed to fetch Fear & Greed: {e}", "WARN")
            return actions

        self.last_index = fng
        score = fng["value"]
        label = fng["label"]
        self._log(f"Fear & Greed Index: {score}/100 — {label}")

        holdings = {h["pair"]: h for h in self.client.get_holdings()}
        cash = self.client.get_cash()
        equity = self.client.get_total_equity()

        # ── EXTREME FEAR: Strong buy opportunity ───────────────────
        if score <= EXTREME_FEAR_BUY:
            self._log(f"EXTREME FEAR ({score}) — deploying capital into BTC/ETH/SOL", "TRADE")
            # Use up to 30% of cash per signal (spread across pairs)
            per_pair_notional = min(cash * 0.30 / len(BUY_PAIRS), equity * self.max_position_pct)

            for pair in BUY_PAIRS:
                if holdings.get(pair):
                    continue  # Already holding — don't double-up
                quote = self.client.get_quote(pair)
                price = quote.get("price", 0)
                if not price or per_pair_notional < 10:
                    continue
                qty = per_pair_notional / price
                self._log(f"BUY {pair} ${per_pair_notional:.2f} @ ${price:.4f} (extreme fear)", "TRADE")
                order = self.client.buy_market(pair, qty)
                actions.append({
                    "pair": pair, "action": "BUY", "quantity": qty,
                    "price": price, "notional": per_pair_notional,
                    "reason": f"Extreme Fear index {score}",
                    "order_id": order.get("id"),
                })

        # ── FEAR: Moderate buy ─────────────────────────────────────
        elif score <= FEAR_BUY:
            self._log(f"FEAR ({score}) — adding to BTC/ETH on dips", "TRADE")
            per_pair_notional = min(cash * 0.15 / len(BUY_PAIRS), equity * self.max_position_pct)
            for pair in ["BTC-USD", "ETH-USD"]:
                if holdings.get(pair):
                    continue
                quote = self.client.get_quote(pair)
                price = quote.get("price", 0)
                if not price or per_pair_notional < 10:
                    continue
                qty = per_pair_notional / price
                self._log(f"BUY {pair} ${per_pair_notional:.2f} (fear signal)", "TRADE")
                order = self.client.buy_market(pair, qty)
                actions.append({
                    "pair": pair, "action": "BUY", "quantity": qty,
                    "price": price, "notional": per_pair_notional,
                    "reason": f"Fear index {score}",
                    "order_id": order.get("id"),
                })

        # ── EXTREME GREED: Take profits ────────────────────────────
        elif score >= EXTREME_GREED_SELL:
            self._log(f"EXTREME GREED ({score}) — taking profits on altcoins", "TRADE")
            for pair in SELL_PAIRS:
                h = holdings.get(pair)
                if not h or not h["quantity"] or not h["avg_cost"]:
                    continue
                pnl_pct = h["pnl_pct"]
                if pnl_pct > 10:  # Only sell if we're up
                    sell_qty = h["quantity"] * 0.50  # Sell half the position
                    self._log(f"SELL 50% {pair} @ ${h['current_price']:.4f} +{pnl_pct:.1f}% (extreme greed)", "TRADE")
                    order = self.client.sell_market(pair, sell_qty)
                    actions.append({
                        "pair": pair, "action": "SELL", "quantity": sell_qty,
                        "price": h["current_price"],
                        "reason": f"Extreme Greed index {score}, +{pnl_pct:.1f}% profit",
                        "order_id": order.get("id"),
                    })

        # ── GREED: Reduce / hold ───────────────────────────────────
        elif score >= GREED_REDUCE:
            self._log(f"GREED ({score}) — holding, no new entries. Monitor for reversal.")
            actions.append({"action": "HOLD", "reason": f"Greed index {score} — await pullback"})

        # ── NEUTRAL ────────────────────────────────────────────────
        else:
            self._log(f"NEUTRAL ({score}) — no contrarian edge. DCA strategies preferred.")
            actions.append({"action": "NEUTRAL", "reason": f"Index {score} — neutral zone"})

        self._log(f"Fear & Greed cycle complete. {len(actions)} action(s).")
        return actions
