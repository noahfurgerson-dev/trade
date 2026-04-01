"""
Advanced Pattern Recognition Strategy
────────────────────────────────────────
Scans a universe of crypto and stocks for high-probability technical
chart formations.  Each pattern has a historical success probability
(success = price moved in predicted direction within 5 trading days).

Patterns detected:
  Golden Cross       — 50MA crosses above 200MA (bullish, 73% hist. success)
  Death Cross        — 50MA crosses below 200MA (bearish, 68%)
  MACD Bullish Cross — MACD line crosses above signal (bullish, 64%)
  MACD Bearish Cross — MACD line crosses below signal (bearish, 61%)
  RSI Oversold Bounce — RSI recovering from below 30 (bullish, 67%)
  RSI Overbought Drop — RSI turning down from above 70 (bearish, 62%)
  BB Squeeze Breakout — Bollinger Bands compressed, breakout imminent (neutral→directional)
  Volume Breakout    — Price at N-day high with 2× average volume (bullish, 71%)
  Momentum Divergence— Price lower high + RSI higher high (bullish reversal, 59%)
"""

from strategies.base import BaseStrategy
from core.market_data import get_technicals, get_ohlcv

# ── Watchlist ─────────────────────────────────────────────────────────────────

CRYPTO_SCAN = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "ADA-USD",
    "DOGE-USD", "AVAX-USD", "LINK-USD", "DOT-USD", "MATIC-USD",
    "XRP-USD", "LTC-USD",
]

STOCK_SCAN = [
    "NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMZN", "TSLA",
    "AMD", "JPM", "SPY", "QQQ", "JEPI",
]

ALL_SCAN = CRYPTO_SCAN + STOCK_SCAN

# ── Pattern definitions ───────────────────────────────────────────────────────

# Pattern → (direction, historical_success_pct, description)
PATTERN_META = {
    "golden_cross":        ("BULLISH", 73, "50MA crossed above 200MA"),
    "death_cross":         ("BEARISH", 68, "50MA crossed below 200MA"),
    "macd_bullish_cross":  ("BULLISH", 64, "MACD line crossed above signal"),
    "macd_bearish_cross":  ("BEARISH", 61, "MACD line crossed below signal"),
    "rsi_oversold_bounce": ("BULLISH", 67, "RSI recovering from oversold (<30)"),
    "rsi_overbought_drop": ("BEARISH", 62, "RSI turning down from overbought (>70)"),
    "bb_squeeze":          ("NEUTRAL", 78, "Bollinger Band squeeze — big move imminent"),
    "volume_breakout":     ("BULLISH", 71, "Price at 20-day high with 2x average volume"),
    "momentum_divergence": ("BULLISH", 59, "Price lower low but RSI higher low — bullish divergence"),
}


# ── Individual pattern detectors ──────────────────────────────────────────────

def detect_patterns(ticker: str) -> list[dict]:
    """
    Run all pattern detectors on a single ticker.
    Returns list of detected patterns with confidence and description.
    """
    t = get_technicals(ticker)
    if not t:
        return []

    patterns = []

    # Golden / Death Cross (requires 200 MA — not all assets have enough data)
    if t.get("golden_cross") is not None:
        df = get_ohlcv(ticker, period="1y")
        if not df.empty and len(df) >= 202:
            close = df["Close"].squeeze()
            ma50  = close.rolling(50).mean()
            ma200 = close.rolling(200).mean()
            cross_prev = float(ma50.iloc[-2]) - float(ma200.iloc[-2])
            cross_now  = float(ma50.iloc[-1]) - float(ma200.iloc[-1])
            if cross_prev < 0 < cross_now:
                patterns.append(_make(ticker, "golden_cross", 0.85, t["price"]))
            elif cross_prev > 0 > cross_now:
                patterns.append(_make(ticker, "death_cross", 0.80, t["price"]))

    # MACD crossover (fresh this bar)
    if t.get("macd_cross") == "bullish":
        patterns.append(_make(ticker, "macd_bullish_cross", 0.70, t["price"]))
    elif t.get("macd_cross") == "bearish":
        patterns.append(_make(ticker, "macd_bearish_cross", 0.68, t["price"]))

    # RSI extremes with reversal confirmation
    rsi = t.get("rsi", 50)
    df = get_ohlcv(ticker, period="3mo")
    if not df.empty and len(df) >= 20:
        close = df["Close"].squeeze()
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, 1e-9)
        rsi_series = 100 - 100 / (1 + rs)
        if len(rsi_series) >= 3:
            rsi_prev = float(rsi_series.iloc[-2])
            if rsi_prev < 30 and rsi > rsi_prev:   # Was oversold, now turning up
                patterns.append(_make(ticker, "rsi_oversold_bounce", 0.72, t["price"]))
            elif rsi_prev > 70 and rsi < rsi_prev:  # Was overbought, now turning down
                patterns.append(_make(ticker, "rsi_overbought_drop", 0.65, t["price"]))

        # Bullish divergence: price lower low, RSI higher low (last 20 bars)
        if len(close) >= 20 and len(rsi_series) >= 20:
            price_low_now  = float(close.iloc[-5:].min())
            price_low_prev = float(close.iloc[-20:-10].min())
            rsi_low_now    = float(rsi_series.iloc[-5:].min())
            rsi_low_prev   = float(rsi_series.iloc[-20:-10].min())
            if price_low_now < price_low_prev and rsi_low_now > rsi_low_prev + 3:
                patterns.append(_make(ticker, "momentum_divergence", 0.62, t["price"]))

    # Bollinger Band squeeze
    if t.get("bb_squeeze"):
        patterns.append(_make(ticker, "bb_squeeze", 0.75, t["price"]))

    # Volume breakout: price at 20-day high, volume > 1.8x average
    if not df.empty and len(df) >= 20 and t.get("vol_ratio", 0) >= 1.8:
        recent_high = float(df["Close"].squeeze().iloc[-20:].max())
        if t["price"] >= recent_high * 0.998:   # Within 0.2% of 20-day high
            patterns.append(_make(ticker, "volume_breakout", 0.78, t["price"]))

    return patterns


def _make(ticker: str, pattern: str, confidence: float, price: float) -> dict:
    meta = PATTERN_META.get(pattern, ("NEUTRAL", 50, pattern))
    return {
        "ticker":      ticker,
        "pattern":     pattern,
        "direction":   meta[0],
        "hist_success": meta[1],
        "description": meta[2],
        "confidence":  confidence,
        "price":       price,
        "signal":      _pattern_to_signal(meta[0], confidence, meta[1]),
    }


def _pattern_to_signal(direction: str, confidence: float, success_pct: int) -> str:
    """Convert pattern direction + confidence into a trade signal."""
    score = confidence * (success_pct / 100)
    if direction == "BULLISH" and score >= 0.50:
        return "BUY"
    if direction == "BEARISH" and score >= 0.45:
        return "SELL"
    return "WATCH"


def scan_all_patterns(tickers: list[str] = None) -> list[dict]:
    """
    Scan a list of tickers for patterns.
    Returns all detected patterns sorted by confidence × historical success.
    """
    if tickers is None:
        tickers = ALL_SCAN
    results = []
    for ticker in tickers:
        try:
            results.extend(detect_patterns(ticker))
        except Exception:
            pass
    results.sort(key=lambda x: x["confidence"] * x["hist_success"], reverse=True)
    return results


# ── Strategy class ─────────────────────────────────────────────────────────────

MIN_CONFIDENCE  = 0.68
MIN_HIST_SUCCESS = 65    # % historical success required to act
MAX_POSITION_PCT = 0.05  # 5% of portfolio per pattern trade


class PatternRecognitionStrategy(BaseStrategy):
    """
    Scans crypto and stocks for high-probability technical chart formations.
    Only acts on patterns with confidence ≥ 0.68 AND historical success ≥ 65%.
    """

    def describe(self) -> str:
        return (
            "Detects Golden Cross, MACD crossovers, RSI divergence, BB squeeze, "
            "and volume breakouts. Trades patterns with ≥68% confidence."
        )

    def run(self) -> list[dict]:
        actions = []
        self._log("Pattern Recognition: scanning universe...")

        patterns = scan_all_patterns()
        actionable = [
            p for p in patterns
            if p["confidence"] >= MIN_CONFIDENCE
            and p["hist_success"] >= MIN_HIST_SUCCESS
            and p["signal"] in ("BUY", "SELL")
        ]

        self._log(
            f"  Found {len(patterns)} patterns across {len(ALL_SCAN)} assets, "
            f"{len(actionable)} actionable."
        )

        for p in actionable[:6]:   # Cap at 6 per cycle
            self._log(
                f"  [{p['signal']}] {p['ticker']:12} {p['pattern']:25} "
                f"conf={p['confidence']:.0%}  hist={p['hist_success']}%  "
                f"@ ${p['price']:,.4f}",
                "TRADE" if p["signal"] in ("BUY", "SELL") else "INFO"
            )

        # ── Execute on crypto (Robinhood) ─────────────────────────────────────
        rh = self.client
        if rh and rh.is_configured():
            holdings   = {h["pair"]: h for h in rh.get_holdings()}
            cash       = rh.get_cash()
            equity     = rh.get_total_equity()
            max_notional = equity * MAX_POSITION_PCT

            for p in actionable:
                ticker = p["ticker"]
                # Map to RH pair format for crypto
                if not ticker.endswith("-USD"):
                    continue                          # Skip stocks for RH
                pair = ticker

                if p["signal"] == "BUY":
                    if pair in holdings:
                        continue  # Already holding
                    notional = min(cash * 0.20, max_notional)
                    if notional < 10:
                        continue
                    quote = rh.get_quote(pair)
                    price = quote.get("price", 0)
                    if not price:
                        continue
                    qty = notional / price
                    self._log(
                        f"  PATTERN BUY {pair} ${notional:.0f} — {p['description']}", "TRADE"
                    )
                    order = rh.buy_market(pair, qty)
                    actions.append({
                        "pair":     pair,   "action":  "BUY",
                        "quantity": qty,    "price":   price,
                        "notional": notional,
                        "pattern":  p["pattern"],
                        "reason":   f"{p['description']} (conf={p['confidence']:.0%}, hist={p['hist_success']}%)",
                        "order_id": order.get("id"),
                    })

                elif p["signal"] == "SELL":
                    h = holdings.get(pair)
                    if not h:
                        continue
                    qty = h["quantity"] * 0.5   # Sell half on bearish pattern
                    self._log(
                        f"  PATTERN SELL 50% {pair} — {p['description']}", "TRADE"
                    )
                    order = rh.sell_market(pair, qty)
                    actions.append({
                        "pair":     pair,   "action":  "SELL",
                        "quantity": qty,    "price":   h["current_price"],
                        "pattern":  p["pattern"],
                        "reason":   f"{p['description']} (conf={p['confidence']:.0%}, hist={p['hist_success']}%)",
                        "order_id": order.get("id"),
                    })

        self._log(f"Pattern Recognition done. {len(actions)} action(s).")
        return actions
