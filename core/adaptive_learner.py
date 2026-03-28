"""
Adaptive Learner
────────────────
Every 12 hours, reviews the last 12 hours of strategy performance and
adjusts each strategy's weight multiplier accordingly.

Weights are saved to data/strategy_weights.json and loaded by the
StrategyOrchestrator to bias final scores.

Weight rules (applied each 12-hour cycle):
  win_rate > 60% AND avg_pnl > 0  →  boost  +15%  (cap 2.5)
  win_rate > 50% AND avg_pnl > 0  →  boost  + 8%
  win_rate < 35% OR  avg_pnl < 0  →  reduce -12%  (floor 0.25)
  0 cycles run this window         →  nudge toward 1.0 by 3%
  otherwise                        →  nudge toward 1.0 by 5%

The weight is stored per-strategy and persists across restarts.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional

from core.performance_tracker import get_strategy_performance

_DATA_DIR     = os.path.join(os.path.dirname(__file__), "..", "data")
_WEIGHTS_FILE = os.path.join(_DATA_DIR, "strategy_weights.json")
_CYCLE_HOURS  = 12

# Boundaries
_MAX_WEIGHT   = 2.5
_MIN_WEIGHT   = 0.25


def _ensure_dir():
    os.makedirs(_DATA_DIR, exist_ok=True)


# ── Persistence ────────────────────────────────────────────────────────────────

def load_weights() -> dict:
    """Return the current weight map.  Missing strategies default to 1.0."""
    _ensure_dir()
    if os.path.exists(_WEIGHTS_FILE):
        try:
            with open(_WEIGHTS_FILE) as f:
                data = json.load(f)
            return data.get("weights", {})
        except Exception:
            pass
    return {}


def _save_weights(weights: dict, last_cycle: str, notes: list[str]):
    _ensure_dir()
    data = {
        "last_cycle":  last_cycle,
        "cycle_count": _get_cycle_count() + 1,
        "weights":     weights,
        "last_notes":  notes[-30:],   # keep last 30 adjustment notes
    }
    with open(_WEIGHTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _get_cycle_count() -> int:
    if os.path.exists(_WEIGHTS_FILE):
        try:
            with open(_WEIGHTS_FILE) as f:
                return json.load(f).get("cycle_count", 0)
        except Exception:
            pass
    return 0


def get_last_cycle_time() -> Optional[datetime]:
    """When the last learning cycle ran (or None if never)."""
    if not os.path.exists(_WEIGHTS_FILE):
        return None
    try:
        with open(_WEIGHTS_FILE) as f:
            ts = json.load(f).get("last_cycle")
        return datetime.fromisoformat(ts) if ts else None
    except Exception:
        return None


def should_run_cycle() -> bool:
    """True if 12+ hours have elapsed since the last cycle (or never run)."""
    last = get_last_cycle_time()
    if not last:
        return True
    return datetime.now() >= last + timedelta(hours=_CYCLE_HOURS)


def get_next_cycle_time() -> Optional[datetime]:
    last = get_last_cycle_time()
    if not last:
        return datetime.now()
    return last + timedelta(hours=_CYCLE_HOURS)


# ── Learning logic ─────────────────────────────────────────────────────────────

def run_learning_cycle(force: bool = False) -> dict:
    """
    Analyse the last 12 hours of performance and update weights.

    Returns a summary dict with notes, old/new weights, and cycle metadata.
    """
    if not force and not should_run_cycle():
        last = get_last_cycle_time()
        nxt  = get_next_cycle_time()
        return {
            "ran":     False,
            "reason":  f"Next cycle due at {nxt.strftime('%H:%M') if nxt else 'N/A'}",
            "last_cycle": last.isoformat() if last else None,
        }

    perf    = get_strategy_performance(hours=_CYCLE_HOURS)
    weights = load_weights()
    notes   = []
    now_str = datetime.now().isoformat(timespec="seconds")

    all_known = [
        "momentum", "mean_reversion", "dca", "fear_greed", "trending",
        "ai_signals", "rebalancer", "cross_platform_rebalancer",
        "stock_momentum", "technical_analysis", "sector_rotation",
        "pairs_trading", "earnings_play", "whale_copy",
        "dividend", "options_income", "treasury", "news_sentiment",
    ]

    changes = {}

    for strategy in all_known:
        old_w = weights.get(strategy, 1.0)
        p     = perf.get(strategy)

        if p and p["cycles_run"] > 0:
            wr  = p["win_rate"]
            avg = p["avg_pnl"]

            if wr > 0.60 and avg > 0:
                new_w = min(_MAX_WEIGHT, old_w * 1.15)
                tag   = f"BOOST +15% (win={wr:.0%}, avg=${avg:+.2f})"
            elif wr > 0.50 and avg > 0:
                new_w = min(_MAX_WEIGHT, old_w * 1.08)
                tag   = f"BOOST +8%  (win={wr:.0%}, avg=${avg:+.2f})"
            elif wr < 0.35 or avg < 0:
                new_w = max(_MIN_WEIGHT, old_w * 0.88)
                tag   = f"REDUCE -12% (win={wr:.0%}, avg=${avg:+.2f})"
            else:
                # Nudge toward 1.0
                new_w = old_w + (1.0 - old_w) * 0.05
                tag   = f"NUDGE→1.0  (win={wr:.0%}, avg=${avg:+.2f})"
        else:
            # No data — gently nudge toward 1.0
            new_w = old_w + (1.0 - old_w) * 0.03
            tag   = "NUDGE→1.0 (no data this window)"

        new_w = round(new_w, 4)
        weights[strategy] = new_w

        note = f"[{now_str[:16]}] {strategy:<28} {old_w:.3f} → {new_w:.3f}  {tag}"
        notes.append(note)
        changes[strategy] = {"old": old_w, "new": new_w, "tag": tag}

    # Load existing notes and append
    existing_notes = []
    if os.path.exists(_WEIGHTS_FILE):
        try:
            with open(_WEIGHTS_FILE) as f:
                existing_notes = json.load(f).get("last_notes", [])
        except Exception:
            pass

    _save_weights(weights, now_str, existing_notes + notes)

    return {
        "ran":        True,
        "timestamp":  now_str,
        "cycle":      _get_cycle_count(),
        "changes":    changes,
        "notes":      notes,
        "perf":       perf,
    }


def get_weights_report() -> dict:
    """Full state for dashboard display."""
    _ensure_dir()
    weights   = load_weights()
    last      = get_last_cycle_time()
    nxt       = get_next_cycle_time()
    count     = _get_cycle_count()
    notes     = []

    if os.path.exists(_WEIGHTS_FILE):
        try:
            with open(_WEIGHTS_FILE) as f:
                notes = json.load(f).get("last_notes", [])
        except Exception:
            pass

    return {
        "weights":         {s: weights.get(s, 1.0) for s in [
            "momentum", "mean_reversion", "dca", "fear_greed", "trending",
            "ai_signals", "rebalancer", "cross_platform_rebalancer",
            "stock_momentum", "technical_analysis", "sector_rotation",
            "pairs_trading", "earnings_play", "whale_copy",
            "dividend", "options_income", "treasury", "news_sentiment",
        ]},
        "last_cycle":      last.strftime("%Y-%m-%d %H:%M") if last else "Never",
        "next_cycle":      nxt.strftime("%Y-%m-%d %H:%M")  if nxt else "Now",
        "cycle_count":     count,
        "should_run_now":  should_run_cycle(),
        "last_notes":      notes[-20:],
    }
