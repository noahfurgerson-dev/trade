"""
Goal tracker toward $100,000/year.
Tracks daily, weekly, monthly progress and projects yearly performance.
"""

import json
import os
from datetime import datetime, date, timedelta
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "data" / "goal_progress.json"
ANNUAL_GOAL = 100_000.0


def _load_data() -> dict:
    DATA_FILE.parent.mkdir(exist_ok=True)
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            pass
    return {
        "starting_balance": None,
        "starting_date": None,
        "snapshots": [],   # list of {date, equity}
        "trades_pnl": [],  # list of {date, pnl, ticker, note}
    }


def _save_data(data: dict):
    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2, default=str))


def initialize(starting_balance: float):
    """Call once when user first connects. Sets the baseline."""
    data = _load_data()
    if data["starting_balance"] is None:
        data["starting_balance"] = starting_balance
        data["starting_date"] = str(date.today())
        _save_data(data)


def record_snapshot(equity: float):
    """Record today's portfolio equity. Call once per session/day."""
    data = _load_data()
    today = str(date.today())
    # Update or add today's snapshot
    snaps = data["snapshots"]
    existing = next((s for s in snaps if s["date"] == today), None)
    if existing:
        existing["equity"] = equity
    else:
        snaps.append({"date": today, "equity": equity})
    # Keep last 365 days
    snaps.sort(key=lambda x: x["date"])
    data["snapshots"] = snaps[-365:]
    _save_data(data)


def record_trade(pnl: float, ticker: str = "", note: str = ""):
    """Log a realized trade P&L."""
    data = _load_data()
    data["trades_pnl"].append({
        "date": str(date.today()),
        "pnl": pnl,
        "ticker": ticker,
        "note": note,
    })
    _save_data(data)


def get_stats(current_equity: float) -> dict:
    """Return full goal-tracking stats."""
    data = _load_data()
    today = date.today()
    start_bal = data.get("starting_balance") or current_equity
    start_date_str = data.get("starting_date") or str(today)
    start_date = date.fromisoformat(start_date_str)

    days_elapsed = max((today - start_date).days, 1)
    total_gain = current_equity - start_bal
    total_gain_pct = (total_gain / start_bal * 100) if start_bal else 0

    # Progress toward $100k goal
    goal_progress_pct = min((total_gain / ANNUAL_GOAL) * 100, 100)

    # Daily pacing
    daily_goal = ANNUAL_GOAL / 365
    weekly_goal = ANNUAL_GOAL / 52
    monthly_goal = ANNUAL_GOAL / 12

    # Actual daily average
    daily_avg = total_gain / days_elapsed if days_elapsed else 0
    projected_annual = daily_avg * 365

    # Days until goal at current pace
    if daily_avg > 0:
        days_to_goal = int((ANNUAL_GOAL - total_gain) / daily_avg)
    else:
        days_to_goal = None

    # Today's P&L from snapshots
    snaps = data["snapshots"]
    today_pnl = 0.0
    if snaps:
        yesterday = str(today - timedelta(days=1))
        prev = next((s["equity"] for s in snaps if s["date"] == yesterday), None)
        if prev:
            today_pnl = current_equity - prev

    # Week P&L
    week_ago = str(today - timedelta(days=7))
    prev_week = next((s["equity"] for s in snaps if s["date"] >= week_ago), None)
    week_pnl = (current_equity - prev_week) if prev_week else 0.0

    # Month P&L
    month_ago = str(today - timedelta(days=30))
    prev_month = next((s["equity"] for s in snaps if s["date"] >= month_ago), None)
    month_pnl = (current_equity - prev_month) if prev_month else 0.0

    # Equity curve
    equity_curve = [{"date": s["date"], "equity": s["equity"]} for s in snaps]
    if not equity_curve or equity_curve[-1]["date"] != str(today):
        equity_curve.append({"date": str(today), "equity": current_equity})

    return {
        "annual_goal": ANNUAL_GOAL,
        "starting_balance": start_bal,
        "current_equity": current_equity,
        "total_gain": total_gain,
        "total_gain_pct": total_gain_pct,
        "goal_progress_pct": goal_progress_pct,
        "days_elapsed": days_elapsed,
        "days_to_goal": days_to_goal,
        "today_pnl": today_pnl,
        "week_pnl": week_pnl,
        "month_pnl": month_pnl,
        "daily_avg": daily_avg,
        "daily_goal": daily_goal,
        "weekly_goal": weekly_goal,
        "monthly_goal": monthly_goal,
        "projected_annual": projected_annual,
        "equity_curve": equity_curve,
        "trades_pnl": data["trades_pnl"][-30:],  # last 30 trades
    }
