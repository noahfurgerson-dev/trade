"""
ML Prediction Engine
─────────────────────
Trains a GradientBoosting classifier on 1 year of historical indicator data
and predicts 3-day price direction (UP / DOWN) for a given ticker.

Features used:
  RSI, MACD histogram, Bollinger Band position, Volume ratio,
  5-day / 20-day / 60-day momentum, Fear & Greed index.

Model lifecycle:
  - First call: downloads history, trains, saves model to disk.
  - Subsequent calls: loads saved model (< 24h old) for fast inference.
  - Auto-retrains every 24 hours with new data.

Usage:
    from core.ml_predictor import predict, batch_predict, train_model

    pred = predict("BTC-USD")
    # {"ticker": "BTC-USD", "direction": "UP", "confidence": 0.74, "features": {...}}

    preds = batch_predict(["BTC-USD", "ETH-USD", "NVDA"])
"""

import json
import os
import time
import pickle
import numpy as np
import pandas as pd

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "ml_models")
_MODEL_TTL = 86400   # retrain once per day
_LOOKAHEAD = 3       # predict direction N days ahead


# ── Feature engineering ───────────────────────────────────────────────────────

def _compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Given a daily OHLCV DataFrame, return a feature DataFrame aligned row-by-row.
    Each row represents the state of indicators on that day.
    """
    close = df["Close"].squeeze()
    vol   = df["Volume"].squeeze() if "Volume" in df.columns else pd.Series(1, index=df.index)

    # RSI (14)
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))

    # MACD hist (12, 26, 9)
    ema12      = close.ewm(span=12, adjust=False).mean()
    ema26      = close.ewm(span=26, adjust=False).mean()
    macd_line  = ema12 - ema26
    sig_line   = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist  = (macd_line - sig_line) / close  # normalise by price

    # Bollinger Band position (0 = at lower, 1 = at upper)
    sma20    = close.rolling(20).mean()
    std20    = close.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    bb_pos   = (close - bb_lower) / (bb_upper - bb_lower + 1e-9)

    # Volume ratio vs 20-day average
    vol_ratio = vol / (vol.rolling(20).mean() + 1e-9)

    # Price momentum at multiple windows
    mom_5  = close.pct_change(5)
    mom_20 = close.pct_change(20)
    mom_60 = close.pct_change(60)

    feats = pd.DataFrame({
        "rsi":       rsi,
        "macd_hist": macd_hist,
        "bb_pos":    bb_pos,
        "vol_ratio": vol_ratio,
        "mom_5":     mom_5,
        "mom_20":    mom_20,
        "mom_60":    mom_60,
    })
    return feats


def _make_labels(close: pd.Series, lookahead: int = _LOOKAHEAD) -> pd.Series:
    """Binary label: 1 if price is higher N days later, 0 otherwise."""
    future = close.shift(-lookahead)
    return (future > close).astype(int)


# ── Model storage ─────────────────────────────────────────────────────────────

def _model_path(ticker: str) -> str:
    os.makedirs(_MODEL_DIR, exist_ok=True)
    safe = ticker.replace("-", "_").replace("/", "_")
    return os.path.join(_MODEL_DIR, f"{safe}.pkl")


def _load_model(ticker: str):
    path = _model_path(ticker)
    ts_path = path + ".ts"
    if not os.path.exists(path):
        return None
    try:
        with open(ts_path) as f:
            ts = float(f.read().strip())
        if time.time() - ts > _MODEL_TTL:
            return None
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _save_model(ticker: str, model):
    path = _model_path(ticker)
    ts_path = path + ".ts"
    try:
        with open(path, "wb") as f:
            pickle.dump(model, f)
        with open(ts_path, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


# ── Training ──────────────────────────────────────────────────────────────────

def train_model(ticker: str):
    """
    Download 1 year of data for ticker, engineer features, train a
    GradientBoostingClassifier, and persist to disk.
    Returns trained model or None on failure.
    """
    from core.market_data import get_ohlcv
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline
    except ImportError:
        print("[ML] scikit-learn not installed — run: pip install scikit-learn")
        return None

    df = get_ohlcv(ticker, period="2y")
    if df.empty or len(df) < 100:
        return None

    feats  = _compute_features(df)
    labels = _make_labels(df["Close"].squeeze())

    # Align and drop NaN rows
    combined = pd.concat([feats, labels.rename("target")], axis=1).dropna()
    if len(combined) < 50:
        return None

    X = combined[feats.columns].values
    y = combined["target"].values

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("clf",    GradientBoostingClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        )),
    ])
    model.fit(X, y)
    _save_model(ticker, model)
    return model


# ── Prediction ─────────────────────────────────────────────────────────────────

def predict(ticker: str, fg_index: int = 50) -> dict:
    """
    Predict 3-day price direction for a single ticker.

    Returns:
        {
            "ticker":     str,
            "direction":  "UP" | "DOWN",
            "confidence": float,   # 0.5–1.0
            "features":   dict,    # last feature values used
            "trained":    bool,    # True if fresh model, False if loaded from cache
        }
    Returns {"ticker": ..., "error": ...} on failure.
    """
    from core.market_data import get_ohlcv

    model = _load_model(ticker)
    trained = False
    if model is None:
        model = train_model(ticker)
        trained = True
    if model is None:
        return {"ticker": ticker, "error": "Could not train model (insufficient data)"}

    df = get_ohlcv(ticker, period="6mo")
    if df.empty or len(df) < 30:
        return {"ticker": ticker, "error": "Insufficient recent data"}

    feats = _compute_features(df)
    last_row = feats.iloc[-1:]
    if last_row.isnull().any().any():
        return {"ticker": ticker, "error": "NaN in feature vector"}

    X = last_row.values
    try:
        proba = model.predict_proba(X)[0]
        up_prob = float(proba[1])
        direction = "UP" if up_prob >= 0.50 else "DOWN"
        confidence = max(up_prob, 1 - up_prob)

        feat_values = last_row.iloc[0].to_dict()

        return {
            "ticker":     ticker,
            "direction":  direction,
            "confidence": round(confidence, 3),
            "up_prob":    round(up_prob, 3),
            "features":   {k: round(float(v), 4) for k, v in feat_values.items()},
            "trained":    trained,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def batch_predict(tickers: list[str], fg_index: int = 50) -> list[dict]:
    """
    Predict direction for a list of tickers.
    Returns list sorted by confidence descending.
    """
    results = []
    for t in tickers:
        try:
            r = predict(t, fg_index)
            if "error" not in r:
                results.append(r)
        except Exception:
            pass
    results.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return results


def get_top_predictions(tickers: list[str], min_confidence: float = 0.60) -> dict:
    """
    Return top ML predictions split by direction.
    Returns {"buy": [...], "sell": [...], "all": [...]}
    """
    preds = batch_predict(tickers)
    filtered = [p for p in preds if p.get("confidence", 0) >= min_confidence]
    return {
        "buy":  [p for p in filtered if p["direction"] == "UP"],
        "sell": [p for p in filtered if p["direction"] == "DOWN"],
        "all":  filtered,
    }
