"""
Market Data Layer
──────────────────
Centralized OHLCV provider using yfinance. All callers share a disk-based
cache so multiple strategies/agents don't hammer the same API endpoint.

Cache TTL:
  Daily bars    → 1 hour
  Intraday bars → 5 minutes

Usage:
    from core.market_data import get_ohlcv, get_technicals, get_multi_technicals

    df = get_ohlcv("BTC-USD", period="3mo")       # crypto
    df = get_ohlcv("NVDA",    period="6mo")        # stocks
    t  = get_technicals("ETH-USD")                 # precomputed indicators dict
    all_t = get_multi_technicals(["BTC-USD","ETH-USD","NVDA"])  # batch
"""

import json
import os
import time
import pandas as pd

_CACHE_DIR    = os.path.join(os.path.dirname(__file__), "..", "data", "market_cache")
_DAILY_TTL    = 3600   # seconds — refresh hourly
_INTRADAY_TTL = 300    # seconds — refresh every 5 min

_INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_path(key: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    safe = key.replace("/", "_").replace("-", "_").replace(" ", "_")
    return os.path.join(_CACHE_DIR, f"{safe}.parquet")


def _read_cache(key: str, ttl: int):
    path = _cache_path(key)
    ts_path = path + ".ts"
    if not os.path.exists(path) or not os.path.exists(ts_path):
        return None
    try:
        with open(ts_path) as f:
            ts = float(f.read().strip())
        if time.time() - ts > ttl:
            return None
        return pd.read_parquet(path)
    except Exception:
        return None


def _write_cache(key: str, df: pd.DataFrame):
    path = _cache_path(key)
    ts_path = path + ".ts"
    try:
        df.to_parquet(path)
        with open(ts_path, "w") as f:
            f.write(str(time.time()))
    except Exception:
        # parquet may not be installed — fall back silently
        pass


# ── Core fetcher ──────────────────────────────────────────────────────────────

def get_ohlcv(ticker: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame:
    """
    Fetch OHLCV bars for any ticker.  Crypto: use yfinance symbol (BTC-USD).
    Returns DataFrame[Open, High, Low, Close, Volume] indexed by date.
    Returns empty DataFrame on failure (never raises).
    """
    cache_key = f"{ticker}_{period}_{interval}"
    ttl = _INTRADAY_TTL if interval in _INTRADAY_INTERVALS else _DAILY_TTL

    cached = _read_cache(cache_key, ttl)
    if cached is not None and not cached.empty:
        return cached

    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        # Flatten multi-level columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if not df.empty:
            _write_cache(cache_key, df)
        return df
    except Exception as e:
        print(f"[MarketData] {ticker}: {e}")
        return pd.DataFrame()


# ── Technical indicator calculator ───────────────────────────────────────────

def get_technicals(ticker: str, period: str = "6mo") -> dict:
    """
    Compute standard technical indicators for a single ticker.

    Returns dict with keys:
        price, rsi, macd, macd_signal, macd_hist, macd_cross,
        bb_upper, bb_lower, bb_middle, bb_position, bb_squeeze,
        ma50, ma200, golden_cross,
        vol_ratio, mom_5d, mom_20d, mom_60d
    Returns {} if insufficient data.
    """
    df = get_ohlcv(ticker, period=period)
    if df.empty or len(df) < 26:
        return {}

    close = df["Close"].squeeze()
    if not isinstance(close, pd.Series):
        return {}

    # ── RSI (14) ──────────────────────────────────────────────────────────────
    delta    = close.diff()
    gain     = delta.clip(lower=0).rolling(14).mean()
    loss     = (-delta.clip(upper=0)).rolling(14).mean()
    rs       = gain / loss.replace(0, 1e-9)
    rsi      = float((100 - 100 / (1 + rs)).iloc[-1])

    # ── MACD (12, 26, 9) ──────────────────────────────────────────────────────
    ema12        = close.ewm(span=12, adjust=False).mean()
    ema26        = close.ewm(span=26, adjust=False).mean()
    macd_line    = ema12 - ema26
    signal_line  = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist    = macd_line - signal_line

    hist_now  = float(macd_hist.iloc[-1])
    hist_prev = float(macd_hist.iloc[-2]) if len(macd_hist) >= 2 else 0.0
    if hist_now > 0 and hist_prev <= 0:
        macd_cross = "bullish"
    elif hist_now < 0 and hist_prev >= 0:
        macd_cross = "bearish"
    else:
        macd_cross = "neutral"

    # ── Bollinger Bands (20, 2σ) ──────────────────────────────────────────────
    sma20       = close.rolling(20).mean()
    std20       = close.rolling(20).std()
    bb_upper    = sma20 + 2 * std20
    bb_lower    = sma20 - 2 * std20
    current     = float(close.iloc[-1])
    bw          = float(bb_upper.iloc[-1] - bb_lower.iloc[-1])
    bb_position = float((current - float(bb_lower.iloc[-1])) / bw) if bw else 0.5
    bb_squeeze  = (bw / float(sma20.iloc[-1])) < 0.05 if float(sma20.iloc[-1]) else False

    # ── Moving averages ────────────────────────────────────────────────────────
    ma50  = float(close.rolling(50).mean().iloc[-1])  if len(close) >= 50  else None
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    # ── Volume ─────────────────────────────────────────────────────────────────
    vol_ratio = 1.0
    if "Volume" in df.columns:
        vol      = df["Volume"].squeeze()
        vol_avg  = float(vol.rolling(20).mean().iloc[-1])
        vol_last = float(vol.iloc[-1])
        vol_ratio = (vol_last / vol_avg) if vol_avg else 1.0

    # ── Momentum ──────────────────────────────────────────────────────────────
    def _mom(n):
        return float((current / float(close.iloc[-(n+1)]) - 1) * 100) if len(close) > n else 0.0

    return {
        "ticker":       ticker,
        "price":        round(current, 6),
        "rsi":          round(rsi, 1),
        "macd":         round(float(macd_line.iloc[-1]),   6),
        "macd_signal":  round(float(signal_line.iloc[-1]), 6),
        "macd_hist":    round(hist_now, 6),
        "macd_cross":   macd_cross,
        "bb_upper":     round(float(bb_upper.iloc[-1]), 6),
        "bb_lower":     round(float(bb_lower.iloc[-1]), 6),
        "bb_middle":    round(float(sma20.iloc[-1]), 6),
        "bb_position":  round(bb_position, 3),  # 0=at lower band, 1=at upper
        "bb_squeeze":   bb_squeeze,
        "ma50":         round(ma50, 6) if ma50 else None,
        "ma200":        round(ma200, 6) if ma200 else None,
        "golden_cross": (ma50 > ma200) if (ma50 and ma200) else None,
        "vol_ratio":    round(vol_ratio, 2),     # >1.5 = elevated volume
        "mom_5d":       round(_mom(5),  2),
        "mom_20d":      round(_mom(20), 2),
        "mom_60d":      round(_mom(60), 2),
    }


def get_multi_technicals(tickers: list[str], period: str = "6mo") -> dict[str, dict]:
    """Fetch technicals for a list of tickers. Returns {ticker: technicals_dict}."""
    return {t: get_technicals(t, period) for t in tickers}
