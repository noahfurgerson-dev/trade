"""
Trading Mode Manager
─────────────────────
Rotates between 3 trading modes every 8 hours, tracks which mode is active,
and supports manual overrides from the dashboard.

Modes:
  ai_consensus     — Claude + GPT-4o + Gemini + Groq vote on every trade
  news_sentiment   — RSS / Reddit / keyword-scored news drives all signals
  algo_strategies  — Full 17-strategy orchestrator (momentum, TA, pairs, etc.)

Rotation schedule (repeating, 5-day A/B test):
  Hour  0–8   → ai_consensus
  Hour  8–16  → news_sentiment
  Hour 16–24  → algo_strategies
  (repeats)

After 5 days the performance tracker declares a winner.
"""

import json
import os
from datetime import datetime, timedelta

_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_FILE = os.path.join(_ROOT, "data", "mode_state.json")

ROTATION_HOURS = 8
TEST_DAYS      = 5

MODES = {
    "ai_consensus": {
        "name":        "Multi-AI Consensus",
        "icon":        "AI",
        "color":       "#7C3AED",   # Purple
        "description": "Claude + GPT-4o + Gemini + Groq vote on every trade signal",
        "strategies":  ["ai_signals"],   # ai_signals now runs all 4 AIs
    },
    "news_sentiment": {
        "name":        "News Sentiment",
        "icon":        "NEWS",
        "color":       "#0EA5E9",   # Blue
        "description": "Reuters / CNBC / Reddit / CoinDesk + AI analysis drives signals",
        "strategies":  ["news_sentiment"],
    },
    "algo_strategies": {
        "name":        "Algorithmic Strategies",
        "icon":        "ALGO",
        "color":       "#10B981",   # Green
        "description": "17 quantitative strategies: momentum, TA, pairs, sector rotation…",
        "strategies":  ["all"],     # Full orchestrator selection
    },
}

ROTATION_ORDER = ["ai_consensus", "news_sentiment", "algo_strategies"]


# ── State persistence ─────────────────────────────────────────────────────────

def _load_state() -> dict:
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    if os.path.exists(_STATE_FILE):
        try:
            with open(_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Mode management ───────────────────────────────────────────────────────────

def initialize_rotation():
    """
    Start the 5-day A/B test rotation from now.
    Call once to set the baseline.  Safe to call multiple times — only
    initialises if no rotation is already running.
    """
    state = _load_state()
    if state.get("rotation_start") and not state.get("manual_override"):
        return state   # Already running

    now = datetime.now()
    state["rotation_start"]    = now.isoformat()
    state["rotation_end"]      = (now + timedelta(days=TEST_DAYS)).isoformat()
    state["current_mode"]      = ROTATION_ORDER[0]
    state["last_switch"]       = now.isoformat()
    state["manual_override"]   = False
    state["override_until"]    = None
    _save_state(state)
    return state


def get_current_mode() -> str:
    """
    Return the currently active mode key ('ai_consensus' | 'news_sentiment' | 'algo_strategies').
    Respects manual overrides.  Auto-advances rotation if 8 hours have elapsed.
    """
    state = _load_state()

    # Initialise if first run
    if not state.get("rotation_start"):
        state = initialize_rotation()

    # Check if manual override has expired
    if state.get("manual_override") and state.get("override_until"):
        if datetime.now() >= datetime.fromisoformat(state["override_until"]):
            state["manual_override"] = False
            state["override_until"]  = None
            _save_state(state)

    # If manual override is active, use it
    if state.get("manual_override"):
        return state.get("current_mode", ROTATION_ORDER[0])

    # Auto-rotation: check if 8 hours have passed since last switch
    last_switch = datetime.fromisoformat(state.get("last_switch", state["rotation_start"]))
    hours_elapsed = (datetime.now() - last_switch).total_seconds() / 3600

    if hours_elapsed >= ROTATION_HOURS:
        # Advance to next mode
        current = state.get("current_mode", ROTATION_ORDER[0])
        idx     = ROTATION_ORDER.index(current) if current in ROTATION_ORDER else 0
        next_mode = ROTATION_ORDER[(idx + 1) % len(ROTATION_ORDER)]
        state["current_mode"] = next_mode
        state["last_switch"]  = datetime.now().isoformat()
        _save_state(state)
        print(f"[ModeManager] Auto-rotated to {next_mode} after {hours_elapsed:.1f}h")
        return next_mode

    return state.get("current_mode", ROTATION_ORDER[0])


def set_mode(mode: str, duration_hours: float = None):
    """
    Manually set the active mode.
    If duration_hours is given, reverts to auto-rotation after that time.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode}. Valid: {list(MODES.keys())}")

    state = _load_state()
    if not state.get("rotation_start"):
        state = initialize_rotation()

    state["current_mode"]    = mode
    state["manual_override"] = True
    state["last_switch"]     = datetime.now().isoformat()

    if duration_hours:
        state["override_until"] = (
            datetime.now() + timedelta(hours=duration_hours)
        ).isoformat()
    else:
        state["override_until"] = None   # Permanent until changed

    _save_state(state)
    print(f"[ModeManager] Manual override -> {mode}" +
          (f" for {duration_hours}h" if duration_hours else " (indefinite)"))


def resume_auto_rotation():
    """Cancel manual override and resume automatic rotation."""
    state = _load_state()
    state["manual_override"] = False
    state["override_until"]  = None
    state["last_switch"]     = datetime.now().isoformat()
    _save_state(state)


def get_mode_info() -> dict:
    """
    Full mode status for dashboard display.
    """
    state        = _load_state()
    if not state.get("rotation_start"):
        state = initialize_rotation()

    current      = get_current_mode()
    last_switch  = datetime.fromisoformat(state.get("last_switch", state["rotation_start"]))
    elapsed_h    = (datetime.now() - last_switch).total_seconds() / 3600
    remaining_h  = max(0.0, ROTATION_HOURS - elapsed_h)
    next_idx     = (ROTATION_ORDER.index(current) + 1) % len(ROTATION_ORDER)
    next_mode    = ROTATION_ORDER[next_idx]

    rot_start    = datetime.fromisoformat(state["rotation_start"])
    rot_end      = datetime.fromisoformat(state.get(
        "rotation_end",
        (rot_start + timedelta(days=TEST_DAYS)).isoformat()
    ))
    days_elapsed = (datetime.now() - rot_start).days
    days_left    = max(0, (rot_end - datetime.now()).days)
    test_complete = datetime.now() >= rot_end

    return {
        "current_mode":    current,
        "current_info":    MODES[current],
        "next_mode":       next_mode,
        "next_info":       MODES[next_mode],
        "hours_remaining": round(remaining_h, 1),
        "next_switch_at":  (last_switch + timedelta(hours=ROTATION_HOURS)).strftime("%H:%M"),
        "manual_override": state.get("manual_override", False),
        "override_until":  state.get("override_until"),
        "rotation_start":  state["rotation_start"][:16].replace("T", " "),
        "rotation_end":    rot_end.strftime("%Y-%m-%d %H:%M"),
        "days_elapsed":    days_elapsed,
        "days_left":       days_left,
        "test_complete":   test_complete,
        "modes":           MODES,
        "rotation_order":  ROTATION_ORDER,
    }


def get_strategies_for_mode(mode: str) -> list[str]:
    """Return strategy names to run for a given mode."""
    return MODES.get(mode, MODES["algo_strategies"])["strategies"]
