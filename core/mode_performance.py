"""
Mode Performance Tracker
─────────────────────────
Records portfolio value before/after each trading cycle, broken down by mode.
After 5 days, compares P&L across the three modes and declares a winner.

Data stored in: data/mode_performance.jsonl  (one JSON record per cycle)
Summary cached: data/mode_summary.json        (updated after each cycle)
"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

_ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_FILE     = os.path.join(_ROOT, "data", "mode_performance.jsonl")
_SUMMARY_FILE = os.path.join(_ROOT, "data", "mode_summary.json")

TEST_DAYS = 5


# ── Write ─────────────────────────────────────────────────────────────────────

def log_mode_cycle(
    mode: str,
    pv_before: float,
    pv_after: float,
    actions: int,
    strategies_run: list[str],
):
    """Append one cycle result to the performance log."""
    if pv_before <= 0:
        return   # No meaningful data without a portfolio value

    pnl     = pv_after - pv_before
    pnl_pct = (pnl / pv_before * 100) if pv_before else 0.0

    record = {
        "ts":             datetime.now().isoformat(),
        "mode":           mode,
        "pv_before":      round(pv_before, 4),
        "pv_after":       round(pv_after,  4),
        "pnl":            round(pnl,        4),
        "pnl_pct":        round(pnl_pct,    6),
        "actions":        actions,
        "strategies_run": strategies_run,
    }

    os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    _rebuild_summary()


# ── Read ──────────────────────────────────────────────────────────────────────

def _read_log(since_days: int = TEST_DAYS) -> list[dict]:
    """Read all records from the log within the last N days."""
    records = []
    cutoff  = datetime.now() - timedelta(days=since_days)
    if not os.path.exists(_LOG_FILE):
        return records
    with open(_LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if datetime.fromisoformat(r["ts"]) >= cutoff:
                    records.append(r)
            except Exception:
                pass
    return records


def _rebuild_summary() -> dict:
    """Recompute per-mode stats and cache to summary file."""
    records = _read_log(since_days=TEST_DAYS)

    stats = defaultdict(lambda: {
        "cycles":        0,
        "total_pnl":     0.0,
        "total_pnl_pct": 0.0,
        "wins":          0,
        "losses":        0,
        "total_actions": 0,
        "pnl_series":    [],
        "first_seen":    None,
        "last_seen":     None,
    })

    for r in records:
        m = r["mode"]
        s = stats[m]
        s["cycles"]        += 1
        s["total_pnl"]     += r["pnl"]
        s["total_pnl_pct"] += r["pnl_pct"]
        s["total_actions"] += r["actions"]
        s["pnl_series"].append(round(r["pnl"], 4))
        if r["pnl"] > 0:
            s["wins"]  += 1
        elif r["pnl"] < 0:
            s["losses"] += 1
        ts = r["ts"]
        if not s["first_seen"] or ts < s["first_seen"]:
            s["first_seen"] = ts
        if not s["last_seen"] or ts > s["last_seen"]:
            s["last_seen"] = ts

    # Compute derived metrics
    summary = {}
    for mode, s in stats.items():
        n = s["cycles"]
        summary[mode] = {
            "cycles":        n,
            "total_pnl":     round(s["total_pnl"], 4),
            "total_pnl_pct": round(s["total_pnl_pct"], 4),
            "avg_pnl":       round(s["total_pnl"] / n, 4) if n else 0,
            "avg_pnl_pct":   round(s["total_pnl_pct"] / n, 6) if n else 0,
            "win_rate":      round(s["wins"] / n * 100, 1) if n else 0,
            "total_actions": s["total_actions"],
            "pnl_series":    s["pnl_series"][-50:],   # Last 50 for charting
            "first_seen":    s["first_seen"],
            "last_seen":     s["last_seen"],
        }

    # Determine winner (highest total P&L)
    winner = None
    if summary:
        winner = max(summary, key=lambda m: summary[m]["total_pnl"])

    result = {
        "updated":   datetime.now().isoformat(),
        "modes":     summary,
        "winner":    winner,
        "days":      TEST_DAYS,
        "total_records": len(records),
    }

    os.makedirs(os.path.dirname(_SUMMARY_FILE), exist_ok=True)
    with open(_SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    return result


def get_summary() -> dict:
    """Load cached summary (or rebuild if stale)."""
    if os.path.exists(_SUMMARY_FILE):
        try:
            with open(_SUMMARY_FILE, encoding="utf-8") as f:
                d = json.load(f)
            # Rebuild if older than 10 minutes
            updated = datetime.fromisoformat(d.get("updated", "2000-01-01"))
            if datetime.now() - updated < timedelta(minutes=10):
                return d
        except Exception:
            pass
    return _rebuild_summary()


def get_winner() -> dict | None:
    """
    Return the winning mode dict if the 5-day test is complete, else None.
    """
    summary = get_summary()
    winner  = summary.get("winner")
    if not winner:
        return None
    return {
        "mode":     winner,
        "stats":    summary["modes"].get(winner, {}),
        "all_modes": summary["modes"],
    }


def get_daily_breakdown(mode: str) -> list[dict]:
    """Return per-day P&L for a specific mode (for bar chart)."""
    records = _read_log(since_days=TEST_DAYS)
    by_day  = defaultdict(float)
    for r in records:
        if r["mode"] == mode:
            day = r["ts"][:10]
            by_day[day] += r["pnl"]
    return [{"date": d, "pnl": round(p, 4)} for d, p in sorted(by_day.items())]
