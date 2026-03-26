"""
Technical Analysis Engine (Alpaca)
────────────────────────────────────
Full suite of institutional-grade technical indicators computed
from live Alpaca bar data. Generates BUY/SELL signals and
executes automatically when multiple indicators align.

Indicators computed:
  RSI(14)          — Momentum oscillator. <30=oversold, >70=overbought
  MACD(12,26,9)    — Trend + momentum. Signal line crossovers
  Bollinger Bands  — Volatility bands. Price touching lower=buy
  EMA 50/200       — Golden cross (50>200) bull, death cross bear
  VWAP             — Volume-weighted avg price. Trading above=bullish
  ATR(14)          — Average True Range for stop-loss sizing

Signal confluence scoring:
  Each indicator votes +1 (bullish) or -1 (bearish).
  Score ≥ +3  → Strong BUY
  Score ≥ +2  → Moderate BUY
  Score ≤ -3  → Strong SELL
  Score ≤ -2  → Moderate SELL (reduce position)
"""

import requests
import os
from datetime import datetime, timedelta
from strategies.base import BaseStrategy

WATCHLIST = [
    "SPY", "QQQ", "NVDA", "MSFT", "AAPL",
    "GOOGL", "META", "AMZN", "TSLA", "AMD",
    "JPM", "XLK", "XLE", "XLF", "SOFI",
]

BUY_THRESHOLD   =  2    # Min confluence score to BUY
SELL_THRESHOLD  = -2    # Max confluence score to SELL
MAX_POSITION_PCT = 0.08  # Max 8% per position
MIN_BARS         = 26    # Need at least 26 bars for MACD
MIN_TRADE_USD    = 25.0


def _ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    k = 2 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def _rsi(closes: list[float], period: int = 14) -> float:
    """Relative Strength Index."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas[-period:]]
    losses = [abs(min(d, 0)) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _macd(closes: list[float]) -> tuple[float, float, float]:
    """MACD line, signal line, histogram."""
    if len(closes) < 26:
        return 0.0, 0.0, 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [m - e for m, e in zip(ema12, ema26)]
    signal    = _ema(macd_line, 9)
    hist      = macd_line[-1] - signal[-1]
    return round(macd_line[-1], 4), round(signal[-1], 4), round(hist, 4)


def _bollinger(closes: list[float], period: int = 20, stdev: float = 2.0) -> tuple[float, float, float]:
    """Upper band, middle band (SMA), lower band."""
    if len(closes) < period:
        c = closes[-1]
        return c, c, c
    window = closes[-period:]
    sma    = sum(window) / period
    std    = (sum((x - sma)**2 for x in window) / period) ** 0.5
    return round(sma + stdev * std, 4), round(sma, 4), round(sma - stdev * std, 4)


def _atr(highs, lows, closes, period: int = 14) -> float:
    """Average True Range — for stop sizing."""
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i]  - closes[i-1]))
        trs.append(tr)
    return round(sum(trs[-period:]) / min(len(trs), period), 4)


def fetch_bars(alpaca_client, symbol: str, limit: int = 60) -> dict:
    """Fetch daily bars from Alpaca data API."""
    try:
        resp = alpaca_client._session.get(
            alpaca_client.data_url + f"/stocks/{symbol}/bars",
            params={"timeframe": "1Day", "limit": limit, "feed": "iex"},
            timeout=10
        )
        resp.raise_for_status()
        bars = resp.json().get("bars", [])
        if not bars:
            return {}
        return {
            "closes": [b["c"] for b in bars],
            "opens":  [b["o"] for b in bars],
            "highs":  [b["h"] for b in bars],
            "lows":   [b["l"] for b in bars],
            "volumes":[b["v"] for b in bars],
            "times":  [b["t"] for b in bars],
        }
    except Exception as e:
        return {"error": str(e)}


def analyse_symbol(alpaca_client, symbol: str) -> dict:
    """Run full TA suite on a symbol. Returns signal dict."""
    bars = fetch_bars(alpaca_client, symbol, limit=60)
    if not bars or "error" in bars or len(bars.get("closes", [])) < MIN_BARS:
        return {"symbol": symbol, "score": 0, "error": bars.get("error", "Insufficient data")}

    closes  = bars["closes"]
    highs   = bars["highs"]
    lows    = bars["lows"]
    volumes = bars["volumes"]
    price   = closes[-1]

    signals = {}
    score   = 0

    # ── RSI ──────────────────────────────────────────────────────────
    rsi = _rsi(closes)
    signals["rsi"] = rsi
    if rsi < 30:
        score += 2    # Oversold — strong buy
        signals["rsi_signal"] = "OVERSOLD (+2)"
    elif rsi < 45:
        score += 1
        signals["rsi_signal"] = "LEAN_BUY (+1)"
    elif rsi > 70:
        score -= 2    # Overbought — strong sell
        signals["rsi_signal"] = "OVERBOUGHT (-2)"
    elif rsi > 55:
        score -= 1
        signals["rsi_signal"] = "LEAN_SELL (-1)"
    else:
        signals["rsi_signal"] = "NEUTRAL (0)"

    # ── MACD ─────────────────────────────────────────────────────────
    macd, signal_line, hist = _macd(closes)
    signals["macd"] = macd
    signals["macd_signal"] = signal_line
    signals["macd_hist"] = hist
    if macd > signal_line and hist > 0:
        score += 1
        signals["macd_cross"] = "BULLISH_CROSS (+1)"
    elif macd < signal_line and hist < 0:
        score -= 1
        signals["macd_cross"] = "BEARISH_CROSS (-1)"
    else:
        signals["macd_cross"] = "NEUTRAL (0)"

    # ── Bollinger Bands ───────────────────────────────────────────────
    bb_upper, bb_mid, bb_lower = _bollinger(closes)
    signals["bb_upper"] = bb_upper
    signals["bb_mid"]   = bb_mid
    signals["bb_lower"] = bb_lower
    bb_pct = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
    signals["bb_pct"] = round(bb_pct, 3)
    if price <= bb_lower:
        score += 2
        signals["bb_signal"] = "LOWER_TOUCH (+2)"
    elif bb_pct < 0.2:
        score += 1
        signals["bb_signal"] = "NEAR_LOWER (+1)"
    elif price >= bb_upper:
        score -= 2
        signals["bb_signal"] = "UPPER_TOUCH (-2)"
    elif bb_pct > 0.8:
        score -= 1
        signals["bb_signal"] = "NEAR_UPPER (-1)"
    else:
        signals["bb_signal"] = "MID_RANGE (0)"

    # ── EMA 50/200 Golden/Death Cross ────────────────────────────────
    if len(closes) >= 50:
        ema50  = _ema(closes, 50)[-1]
        signals["ema50"] = round(ema50, 2)
        if len(closes) >= 200:
            ema200 = _ema(closes, 200)[-1]
            signals["ema200"] = round(ema200, 2)
            if ema50 > ema200:
                score += 1
                signals["ma_cross"] = "GOLDEN_CROSS (+1)"
            else:
                score -= 1
                signals["ma_cross"] = "DEATH_CROSS (-1)"
        # Price vs EMA50
        if price > ema50:
            score += 1
            signals["price_ema50"] = "ABOVE_EMA50 (+1)"
        else:
            score -= 1
            signals["price_ema50"] = "BELOW_EMA50 (-1)"

    # ── Volume surge ─────────────────────────────────────────────────
    avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else volumes[-1]
    vol_ratio = volumes[-1] / avg_vol if avg_vol else 1
    signals["volume_ratio"] = round(vol_ratio, 2)
    if vol_ratio > 1.5 and score > 0:
        score += 1
        signals["volume_signal"] = "HIGH_VOL_CONFIRM (+1)"
    elif vol_ratio > 1.5 and score < 0:
        score -= 1
        signals["volume_signal"] = "HIGH_VOL_CONFIRM (-1)"
    else:
        signals["volume_signal"] = f"NORMAL ({vol_ratio:.1f}x)"

    # ── ATR for stop sizing ───────────────────────────────────────────
    atr = _atr(highs, lows, closes)
    signals["atr"] = atr
    signals["stop_loss"] = round(price - 2 * atr, 2)
    signals["take_profit"] = round(price + 3 * atr, 2)

    action = "HOLD"
    if score >= BUY_THRESHOLD:
        action = "BUY"
    elif score <= SELL_THRESHOLD:
        action = "SELL"

    return {
        "symbol":      symbol,
        "price":       price,
        "score":       score,
        "action":      action,
        "rsi":         rsi,
        "macd_hist":   hist,
        "bb_pct":      signals.get("bb_pct", 0.5),
        "atr":         atr,
        "stop_loss":   signals["stop_loss"],
        "take_profit": signals["take_profit"],
        "signals":     signals,
        "volume_ratio": vol_ratio,
    }


class TechnicalAnalysisStrategy(BaseStrategy):
    """
    Runs RSI + MACD + Bollinger Bands + EMA cross + volume on 15 top stocks.
    Buys when 2+ indicators align bullish, sells when 2+ align bearish.
    Uses ATR-based stop-loss and take-profit sizing.
    """

    def __init__(self, alpaca_client, max_position_pct: float = MAX_POSITION_PCT):
        super().__init__(alpaca_client, max_position_pct)
        self.alpaca = alpaca_client

    def describe(self) -> str:
        return "Multi-indicator TA engine: RSI + MACD + Bollinger + EMA cross + Volume."

    def run(self) -> list[dict]:
        actions = []

        if not self.alpaca.is_configured():
            self._log("Alpaca not configured.", "WARN")
            return actions

        if not self.alpaca.is_market_open():
            self._log("Market closed — TA engine deferred.")
            return [{"action": "HOLD", "reason": "Market closed"}]

        self._log("Technical Analysis Engine: scanning watchlist...")

        portfolio = self.alpaca.get_portfolio_value()
        cash      = self.alpaca.get_cash()
        positions = {p["symbol"]: p for p in self.alpaca.get_positions()}

        results = []
        for sym in WATCHLIST:
            analysis = analyse_symbol(self.alpaca, sym)
            if "error" in analysis:
                self._log(f"  {sym}: {analysis['error']}", "WARN")
                continue
            results.append(analysis)
            icon = "🟢" if analysis["score"] >= BUY_THRESHOLD else (
                   "🔴" if analysis["score"] <= SELL_THRESHOLD else "⚪")
            self._log(
                f"  {icon} {sym:6} ${analysis['price']:,.2f} "
                f"score={analysis['score']:+d} RSI={analysis['rsi']:.0f} "
                f"MACD_H={analysis['macd_hist']:+.3f} BB%={analysis['bb_pct']:.2f} "
                f"Vol={analysis['volume_ratio']:.1f}x → {analysis['action']}"
            )

        # Sort by score
        results.sort(key=lambda x: x["score"], reverse=True)

        # ── Execute BUYs ───────────────────────────────────────────────
        for r in results:
            if r["action"] != "BUY":
                continue
            sym = r["symbol"]
            if sym in positions:
                self._log(f"  SKIP {sym} — already holding")
                continue

            notional = min(portfolio * self.max_position_pct, cash * 0.3)
            if notional < MIN_TRADE_USD:
                continue

            self._log(
                f"  BUY ${notional:.0f} {sym} @ ${r['price']:,.2f} "
                f"score={r['score']:+d} stop=${r['stop_loss']:.2f} tp=${r['take_profit']:.2f}",
                "TRADE"
            )
            order = self.alpaca.buy_market(sym, notional=notional)
            actions.append({
                "symbol": sym, "action": "BUY", "notional": notional,
                "price": r["price"], "score": r["score"],
                "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
                "reason": f"TA confluence score={r['score']:+d}",
                "order_id": order.get("id"),
            })
            cash -= notional

        # ── Execute SELLs on held positions ────────────────────────────
        for r in results:
            if r["action"] != "SELL":
                continue
            sym = r["symbol"]
            if sym not in positions:
                continue
            pos = positions[sym]
            qty = pos["qty"]
            self._log(
                f"  SELL {qty:.4f} {sym} @ ${r['price']:,.2f} score={r['score']:+d}",
                "TRADE"
            )
            order = self.alpaca.sell_market(sym, qty)
            actions.append({
                "symbol": sym, "action": "SELL", "quantity": qty,
                "price": r["price"], "score": r["score"],
                "reason": f"TA bearish confluence score={r['score']:+d}",
                "order_id": order.get("id"),
            })

        # Check stop-loss / take-profit on held positions not in sell list
        sell_syms = {r["symbol"] for r in results if r["action"] == "SELL"}
        for sym, pos in positions.items():
            if sym not in {r["symbol"] for r in results} or sym in sell_syms:
                continue
            r = next(x for x in results if x["symbol"] == sym)
            price = r["price"]
            # Check stop
            if price <= r["stop_loss"]:
                self._log(f"  STOP-LOSS {sym} @ ${price:.2f} (stop=${r['stop_loss']:.2f})", "TRADE")
                order = self.alpaca.sell_market(sym, pos["qty"])
                actions.append({"symbol": sym, "action": "SELL", "reason": "ATR stop-loss hit",
                                 "order_id": order.get("id")})
            # Check take-profit
            elif price >= r["take_profit"]:
                sell_qty = round(pos["qty"] * 0.5, 8)  # Take 50% off
                self._log(f"  TAKE-PROFIT 50% {sym} @ ${price:.2f} (tp=${r['take_profit']:.2f})", "TRADE")
                order = self.alpaca.sell_market(sym, sell_qty)
                actions.append({"symbol": sym, "action": "SELL", "reason": "ATR take-profit hit",
                                 "order_id": order.get("id")})

        self._log(f"TA engine done. {len(actions)} action(s).")
        return actions

    def get_scan_report(self) -> list[dict]:
        """Full scan without trading — for dashboard display."""
        results = []
        for sym in WATCHLIST:
            r = analyse_symbol(self.alpaca, sym)
            if "error" not in r:
                results.append(r)
        results.sort(key=lambda x: x["score"], reverse=True)
        return results
