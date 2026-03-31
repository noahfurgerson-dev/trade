"""
Autonomous Trading Scheduler
─────────────────────────────
Runs the Strategy Orchestrator on a fixed interval, completely independent
of the Streamlit dashboard.  Start this once and leave it running — it will
trade 24/7 even if no browser has the dashboard open.

Usage:
    python run_scheduler.py              # default: every 30 minutes
    python run_scheduler.py --interval 15   # every 15 minutes
    python run_scheduler.py --dry-run       # evaluate only, no trades

To run in the background on Windows (keeps running after you close the terminal):
    pythonw run_scheduler.py
  or use the provided start_scheduler.bat

Logs are written to:
    data/scheduler.log   (rotated at 10 MB, keeps 3 backups)
"""

import argparse
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime

# ── Resolve paths before anything else ───────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from core.env_loader import load_env, build_clients
load_env(_ROOT)

# ── Logging setup ─────────────────────────────────────────────────────────────
_LOG_DIR  = os.path.join(_ROOT, "data")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, "scheduler.log")

_handler = logging.handlers.RotatingFileHandler(
    _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

log = logging.getLogger("scheduler")
log.setLevel(logging.INFO)
log.addHandler(_handler)
log.addHandler(_console)


# ── Single orchestration cycle ────────────────────────────────────────────────

def run_cycle(dry_run: bool = False):
    log.info("=" * 60)
    log.info(f"CYCLE START  dry_run={dry_run}")

    rh, alpaca = build_clients(log)

    if not rh and not alpaca:
        log.error("No clients available — check .env for RH_API_KEY / ALPACA_API_KEY")
        return

    try:
        from core.strategy_orchestrator import StrategyOrchestrator
        orch   = StrategyOrchestrator(rh_client=rh, alpaca_client=alpaca)
        result = orch.run(dry_run=dry_run)

        selected = result.get("selected", [])
        actions  = result.get("actions",  [])
        pv_b     = result.get("pv_before", 0)
        pv_a     = result.get("pv_after",  0)
        delta    = pv_a - pv_b

        log.info(f"Strategies selected: {[s['name'] for s in selected]}")
        log.info(f"Actions executed:    {len(actions)}")
        if pv_b:
            log.info(f"Portfolio:           ${pv_b:,.2f} → ${pv_a:,.2f}  ({delta:+.2f})")

        lr = result.get("learning_result")
        if lr and lr.get("ran"):
            log.info(f"Learning cycle #{lr['cycle']} ran — {len(lr.get('changes', {}))} weights updated")

    except Exception as e:
        log.exception(f"Orchestrator cycle failed: {e}")

    log.info("CYCLE END")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Autonomous Trading Scheduler")
    parser.add_argument("--interval", type=int, default=30,
                        help="Minutes between orchestrator cycles (default: 30)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate strategies but do not execute trades")
    args = parser.parse_args()

    interval_sec = args.interval * 60
    log.info(f"Autonomous scheduler starting — interval={args.interval}min  dry_run={args.dry_run}")
    log.info(f"Log file: {_LOG_FILE}")
    log.info("Press Ctrl+C to stop.\n")

    # Run immediately on start, then on schedule
    run_cycle(dry_run=args.dry_run)

    while True:
        next_run = datetime.now().timestamp() + interval_sec
        log.info(f"Next cycle in {args.interval} minutes "
                 f"({datetime.fromtimestamp(next_run).strftime('%H:%M:%S')})")
        try:
            time.sleep(interval_sec)
        except KeyboardInterrupt:
            log.info("Scheduler stopped by user.")
            sys.exit(0)

        run_cycle(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
