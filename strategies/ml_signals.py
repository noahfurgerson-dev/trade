"""
ML Signals Strategy
─────────────────────
Uses trained GradientBoosting models to predict 3-day price direction for
a universe of crypto and stocks, then executes on high-confidence signals.

The model is trained on:
  RSI, MACD histogram, Bollinger Band position, Volume ratio,
  5d / 20d / 60d momentum — predicts whether price will be higher in 3 days.

Thresholds:
  confidence ≥ 0.65 to consider a signal
  direction == UP   → BUY (if not already holding)
  direction == DOWN → SELL 50% (if currently holding)
"""

from strategies.base import BaseStrategy
from core.ml_predictor import batch_predict

# Tickers the ML engine scans
CRYPTO_TICKERS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "DOGE-USD",
    "AVAX-USD", "LINK-USD", "BNB-USD", "XRP-USD", "DOT-USD",
]

STOCK_TICKERS = [
    "NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMZN",
    "SPY", "QQQ", "TSLA", "AMD",
]

ALL_TICKERS = CRYPTO_TICKERS + STOCK_TICKERS

MIN_CONFIDENCE   = 0.65
MAX_POSITION_PCT = 0.06


class MLSignalStrategy(BaseStrategy):
    """
    Machine-learning driven trade signals.
    Models retrain daily from 2 years of historical OHLCV data.
    """

    def describe(self) -> str:
        return (
            "GradientBoosting ML models predict 3-day price direction. "
            "Executes on signals with ≥65% confidence."
        )

    def run(self) -> list[dict]:
        actions = []
        self._log("ML Signals: generating predictions...")

        predictions = batch_predict(ALL_TICKERS)
        strong = [p for p in predictions if p.get("confidence", 0) >= MIN_CONFIDENCE]

        self._log(
            f"  {len(predictions)} predictions computed, {len(strong)} above confidence threshold."
        )
        for p in strong[:10]:
            arrow = "↑" if p["direction"] == "UP" else "↓"
            self._log(
                f"  {arrow} {p['ticker']:12} {p['direction']:4} "
                f"conf={p['confidence']:.0%}  up_prob={p['up_prob']:.0%}"
            )

        # ── Execute on crypto (Robinhood) ─────────────────────────────────────
        rh = self.client
        if rh and rh.is_configured():
            holdings  = {h["pair"]: h for h in rh.get_holdings()}
            cash      = rh.get_cash()
            equity    = rh.get_total_equity()

            for p in strong:
                ticker = p["ticker"]
                if not ticker.endswith("-USD"):
                    continue  # stocks handled separately

                pair = ticker

                if p["direction"] == "UP":
                    if pair in holdings:
                        continue
                    notional = min(equity * MAX_POSITION_PCT, cash * 0.25)
                    if notional < 10:
                        continue
                    quote = rh.get_quote(pair)
                    price = quote.get("price", 0)
                    if not price:
                        continue
                    qty = notional / price
                    self._log(
                        f"  ML BUY {pair} ${notional:.0f} "
                        f"(conf={p['confidence']:.0%}, UP model)", "TRADE"
                    )
                    order = rh.buy_market(pair, qty)
                    actions.append({
                        "pair":       pair,   "action": "BUY",
                        "quantity":   qty,    "price":  price,
                        "notional":   notional,
                        "ml_confidence": p["confidence"],
                        "ml_direction":  p["direction"],
                        "reason":     f"ML model predicts UP with {p['confidence']:.0%} confidence",
                        "order_id":   order.get("id"),
                    })

                elif p["direction"] == "DOWN":
                    h = holdings.get(pair)
                    if not h:
                        continue
                    qty = h["quantity"] * 0.5
                    self._log(
                        f"  ML SELL 50% {pair} "
                        f"(conf={p['confidence']:.0%}, DOWN model)", "TRADE"
                    )
                    order = rh.sell_market(pair, qty)
                    actions.append({
                        "pair":       pair,  "action": "SELL",
                        "quantity":   qty,
                        "price":      h["current_price"],
                        "ml_confidence": p["confidence"],
                        "ml_direction":  p["direction"],
                        "reason":     f"ML model predicts DOWN with {p['confidence']:.0%} confidence",
                        "order_id":   order.get("id"),
                    })

        self._log(f"ML Signals done. {len(actions)} action(s).")
        return actions
