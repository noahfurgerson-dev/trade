"""
Performance Tracker
───────────────────
Logs every orchestrator cycle and the portfolio value delta it produced.
Used by the Adaptive Learner to score each strategy's contribution.

Storage: data/perf_log.jsonl  (one JSON object per line, easy to append)

Each record:
  {
    "ts":         "2026-03-27T10:05:00",
    "cycle_id":   "abc123",
    "strategies": ["momentum", "dca"],
    "pv_before":  5000.0,
    "pv_after":   5050.0,
    "delta_usd":  50.0,
    "delta_pct":  1.0,
    "actions":    3
  }
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

_DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
_PERF_FILE = os.path.join(_DATA_DIR, "perf_log.jsonl")


def _ensure_dir():
    os.makedirs(_DATA_DIR, exist_ok=True)


# ── Writing ────────────────────────────────────────────────────────────────────

def log_cycle(
    strategies: list[str],
    pv_before: float,
    pv_after: float,
    actions: int = 0,
    cycle_id: Optional[str] = None,
) -> str:
    """
    Append one cycle record.  Returns the cycle_id.
    Handles edge cases: zero portfolio, missing values.
    """
    _ensure_dir()
    if not cycle_id:
        cycle_id = uuid.uuid4().hex[:8]

    delta_usd = round(pv_after - pv_before, 4)
    delta_pct = round((delta_usd / pv_before * 100) if pv_before else 0.0, 4)

    record = {
        "ts":         datetime.now().isoformat(timespec="seconds"),
        "cycle_id":   cycle_id,
        "strategies": strategies,
        "pv_before":  round(pv_before, 2),
        "pv_after":   round(pv_after, 2),
        "delta_usd":  delta_usd,
        "delta_pct":  delta_pct,
        "actions":    actions,
    }

    with open(_PERF_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    return cycle_id


# ── Reading ────────────────────────────────────────────────────────────────────

def _read_records(hours: int = 12) -> list[dict]:
    """Return all records from the last `hours` hours."""
    _ensure_dir()
    if not os.path.exists(_PERF_FILE):
        return []

    cutoff = datetime.now() - timedelta(hours=hours)
    records = []
    with open(_PERF_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts  = datetime.fromisoformat(rec["ts"])
                if ts >= cutoff:
                    records.append(rec)
            except Exception:
                pass
    return records


def get_strategy_performance(hours: int = 12) -> dict[str, dict]:
    """
    Compute per-strategy performance over the last `hours` hours.

    Each strategy that ran in a cycle is given an equal share of that cycle's
    delta_usd.  This is the fairest attribution we can do without per-trade P&L.

    Returns:
      {
        "momentum": {
          "cycles_run":   5,
          "total_pnl":    12.50,
          "avg_pnl":      2.50,
          "win_rate":     0.80,    # fraction of cycles with positive delta
          "total_pct":    0.25,    # sum of attributed delta_pct
        },
        ...
      }
    """
    records = _read_records(hours)
    perf: dict[str, dict] = {}

    for rec in records:
        strats = rec.get("strategies", [])
        if not strats:
            continue

        share_usd = rec["delta_usd"] / len(strats)
        share_pct = rec["delta_pct"] / len(strats)
        win       = rec["delta_usd"] > 0

        for s in strats:
            if s not in perf:
                perf[s] = {
                    "cycles_run": 0,
                    "total_pnl":  0.0,
                    "wins":       0,
                    "total_pct":  0.0,
                }
            perf[s]["cycles_run"] += 1
            perf[s]["total_pnl"]  += share_usd
            perf[s]["total_pct"]  += share_pct
            if win:
                perf[s]["wins"]   += 1

    # Compute derived fields
    for s, d in perf.items():
        n = d["cycles_run"]
        d["avg_pnl"]  = round(d["total_pnl"] / n, 4) if n else 0.0
        d["win_rate"] = round(d["wins"] / n, 4)       if n else 0.0
        d["total_pnl"] = round(d["total_pnl"], 4)
        d["total_pct"] = round(d["total_pct"], 4)

    return perf


def get_recent_cycles(limit: int = 20) -> list[dict]:
    """Return the most recent `limit` cycle records, newest first."""
    _ensure_dir()
    if not os.path.exists(_PERF_FILE):
        return []
    records = []
    with open(_PERF_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                pass
    return list(reversed(records[-limit:]))
