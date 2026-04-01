"""
Seasonality Analysis Engine
─────────────────────────────
Analyses historical price behaviour to identify recurring seasonal patterns.

Two sources:
  1. Internal data — our own performance_tracker JSONL (cycle-level P&L)
  2. Historical price data — 5+ years of yfinance data per asset

Seasonal dimensions:
  Day of week    — Mon=0 through Fri=4, crypto adds Sat/Sun
  Month of year  — Jan=1 … Dec=12
  Hour of day    — 0–23 UTC

Outputs a SeasonalScore object: a score modifier (0.5–1.5) that the
orchestrator applies to strategy base scores.

Usage:
    from core.seasonality import get_seasonal_score, get_seasonal_summary

    score = get_seasonal_score()                    # multiplier for right now
    summary = get_seasonal_summary()                # human-readable context
    monthly = get_asset_seasonality("BTC-USD")      # monthly returns dict
"""

import json
import os
from datetime import datetime
from typing import Optional


_PERF_LOG = os.path.join(os.path.dirname(__file__), "..", "data", "performance_log.jsonl")

# ── Known market seasonality patterns (based on published research) ───────────
# Source: aggregate of academic studies + practitioner research.
# Values represent average excess return vs baseline for that period.

# Crypto monthly bias (positive = historically above-average month for BTC)
_CRYPTO_MONTHLY_BIAS = {
    1:   0.12,   # January  — new-year capital deployment, strong historically
    2:   0.05,   # February — mixed
    3:  -0.03,   # March    — tax selling pressure begins
    4:   0.08,   # April    — Q2 often strong
    5:  -0.06,   # May      — "sell in May" effect
    6:  -0.04,   # June     — summer lull
    7:   0.03,   # July     — mid-summer recovery
    8:   0.04,   # August   — modest positive
    9:  -0.08,   # September — historically worst month
    10:  0.15,   # October  — "Uptober" — historically crypto's best month
    11:  0.18,   # November — bull season continues into Q4
    12:  0.12,   # December — Santa rally + year-end window dressing
}

# Stock market monthly bias (SPY-like large caps)
_STOCK_MONTHLY_BIAS = {
    1:   0.04,   # January Effect
    2:   0.01,
    3:  -0.01,
    4:   0.05,   # Q1 earnings optimism
    5:  -0.03,   # "Sell in May"
    6:  -0.02,
    7:   0.03,
    8:  -0.01,
    9:  -0.05,   # September — historically worst for stocks too
    10:  0.02,
    11:  0.04,
    12:  0.03,   # Santa rally
}

# Day-of-week bias for crypto (7-day market)
# Based on aggregate research on BTC/ETH weekend vs weekday patterns
_CRYPTO_DOW_BIAS = {
    0: -0.01,   # Monday   — often weak open
    1:  0.02,   # Tuesday
    2:  0.01,   # Wednesday
    3:  0.02,   # Thursday
    4:  0.01,   # Friday   — risk-on into weekend
    5:  0.00,   # Saturday — weekend volume
    6: -0.01,   # Sunday   — weekend low liquidity
}

# Hour-of-day bias for crypto (UTC) — higher = above-average activity
_CRYPTO_HOUR_BIAS = {
    **{h: -0.01 for h in range(0, 6)},      # 0–5 UTC: Asian overnight
    **{h:  0.01 for h in range(6, 10)},     # 6–9 UTC: Asia open
    **{h:  0.02 for h in range(10, 14)},    # 10–13 UTC: Europe active
    **{h:  0.03 for h in range(14, 18)},    # 14–17 UTC: US open overlap
    **{h:  0.01 for h in range(18, 22)},    # 18–21 UTC: US afternoon
    **{h: -0.01 for h in range(22, 24)},    # 22–23 UTC: late US
}


# ── Internal performance analysis ────────────────────────────────────────────

def _load_internal_perf() -> list[dict]:
    """Load our own cycle performance log."""
    records = []
    if not os.path.exists(_PERF_LOG):
        return records
    try:
        with open(_PERF_LOG) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception:
        pass
    return records


def _internal_dow_returns() -> dict[int, float]:
    """Compute average pnl_pct by day-of-week from our own data."""
    records = _load_internal_perf()
    if len(records) < 20:
        return {}

    dow_pnl: dict[int, list] = {i: [] for i in range(7)}
    for r in records:
        ts = r.get("timestamp") or r.get("time")
        pnl = r.get("pnl_pct") or r.get("pnl")
        if ts and pnl is not None:
            try:
                dow = datetime.fromisoformat(ts).weekday()
                dow_pnl[dow].append(float(pnl))
            except Exception:
                pass

    return {
        dow: sum(vals) / len(vals)
        for dow, vals in dow_pnl.items()
        if len(vals) >= 3
    }


# ── yfinance historical seasonality ──────────────────────────────────────────

def get_asset_seasonality(ticker: str, years: int = 5) -> dict:
    """
    Compute per-month average return for a ticker over N years.
    Returns {month_num: avg_return_pct} — e.g. {1: 3.2, 2: -1.1, ...}
    """
    try:
        from core.market_data import get_ohlcv
        period = f"{years}y"
        df = get_ohlcv(ticker, period=period)
        if df.empty or len(df) < 60:
            return {}

        close = df["Close"].squeeze()
        monthly = close.resample("ME").last()
        monthly_returns = monthly.pct_change().dropna() * 100

        by_month: dict[int, list] = {i: [] for i in range(1, 13)}
        for date, ret in monthly_returns.items():
            by_month[date.month].append(float(ret))

        return {
            m: round(sum(v) / len(v), 2)
            for m, v in by_month.items()
            if v
        }
    except Exception:
        return {}


# ── Score computation ─────────────────────────────────────────────────────────

def get_seasonal_score(
    asset_type: str = "crypto",
    dt: Optional[datetime] = None,
) -> float:
    """
    Return a seasonal score multiplier for right now (or a given datetime).

    asset_type: "crypto" | "stocks"
    Returns float in range [0.6, 1.4]:
      1.0 = neutral season
      >1.0 = seasonally favorable
      <1.0 = seasonally unfavorable
    """
    if dt is None:
        dt = datetime.utcnow()

    month   = dt.month
    dow     = dt.weekday()
    hour    = dt.hour

    if asset_type == "crypto":
        month_bias = _CRYPTO_MONTHLY_BIAS.get(month, 0.0)
        dow_bias   = _CRYPTO_DOW_BIAS.get(dow, 0.0)
        hour_bias  = _CRYPTO_HOUR_BIAS.get(hour, 0.0)
    else:
        month_bias = _STOCK_MONTHLY_BIAS.get(month, 0.0)
        dow_bias   = 0.0  # stocks don't trade on weekends
        hour_bias  = 0.0  # handled by market-open checks

    # Blend biases: month is highest weight
    raw_bias = (month_bias * 0.60) + (dow_bias * 0.25) + (hour_bias * 0.15)

    # Incorporate our own internal performance data if available
    internal_dow = _internal_dow_returns()
    if dow in internal_dow:
        internal_bias = min(max(internal_dow[dow] / 10, -0.05), 0.05)
        raw_bias = raw_bias * 0.70 + internal_bias * 0.30

    # Convert to multiplier: 0.1 bias = 1.10 multiplier
    multiplier = 1.0 + (raw_bias * 3.0)
    return round(max(0.6, min(1.4, multiplier)), 3)


def get_seasonal_summary(dt: Optional[datetime] = None) -> dict:
    """
    Return a human-readable seasonal context for the dashboard.
    """
    if dt is None:
        dt = datetime.utcnow()

    month_names = {
        1:"January", 2:"February", 3:"March", 4:"April",
        5:"May", 6:"June", 7:"July", 8:"August",
        9:"September", 10:"October", 11:"November", 12:"December",
    }
    dow_names = {0:"Monday", 1:"Tuesday", 2:"Wednesday", 3:"Thursday",
                 4:"Friday", 5:"Saturday", 6:"Sunday"}

    month = dt.month
    dow   = dt.weekday()

    crypto_score = get_seasonal_score("crypto", dt)
    stock_score  = get_seasonal_score("stocks", dt)

    def _label(score):
        if score >= 1.15: return "Strongly Favorable"
        if score >= 1.05: return "Favorable"
        if score >= 0.95: return "Neutral"
        if score >= 0.85: return "Unfavorable"
        return "Strongly Unfavorable"

    # Month-specific notes
    month_notes = {
        1:  "January Effect — institutional money re-enters market",
        5:  "Sell in May — historically weak period begins",
        9:  "September — statistically the worst month for both crypto and stocks",
        10: "Uptober — crypto historically at its strongest in October",
        11: "Q4 bull season — historically strong for both asset classes",
        12: "Santa rally + year-end window dressing",
    }

    return {
        "month":        month_names[month],
        "day":          dow_names[dow],
        "crypto_score": crypto_score,
        "stock_score":  stock_score,
        "crypto_label": _label(crypto_score),
        "stock_label":  _label(stock_score),
        "note":         month_notes.get(month, "No special seasonal pattern this month."),
        "timestamp":    dt.isoformat(),
    }
