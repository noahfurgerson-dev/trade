"""
Trading Scheduler — Single Cycle Runner
────────────────────────────────────────
Runs ONE orchestrator cycle then exits immediately.
Designed to be called by Windows Task Scheduler on a repeating trigger
(every 30 minutes, wake-to-run enabled) so sleep/wake cycles never break it.

Usage:
    python run_cycle_once.py            # live trading
    python run_cycle_once.py --dry-run  # evaluate only, no trades

Windows Task Scheduler calls this script directly — no long-running process,
no time.sleep(), nothing to freeze or get stuck after wake-from-sleep.
"""

import argparse
import logging
import logging.handlers
import os
import sys

# ── Resolve paths ─────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from core.env_loader import load_env, build_clients
load_env(_ROOT)

# ── Logging ───────────────────────────────────────────────────────────────────
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


def main():
    parser = argparse.ArgumentParser(description="Run one trading cycle and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate strategies but do not execute trades")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info(f"CYCLE START  dry_run={args.dry_run}")

    rh, alpaca = build_clients(log)
    if not rh and not alpaca:
        log.error("No clients available — check .env for RH_API_KEY / ALPACA_API_KEY")
        sys.exit(1)

    try:
        from core.strategy_orchestrator import StrategyOrchestrator
        orch   = StrategyOrchestrator(rh_client=rh, alpaca_client=alpaca)
        result = orch.run(dry_run=args.dry_run)

        selected = result.get("selected", [])
        actions  = result.get("actions",  [])
        pv_b     = result.get("pv_before", 0)
        pv_a     = result.get("pv_after",  0)
        delta    = pv_a - pv_b

        log.info(f"Strategies selected: {[s['name'] for s in selected]}")
        log.info(f"Actions executed:    {len(actions)}")
        if pv_b:
            log.info(f"Portfolio:           ${pv_b:,.2f} -> ${pv_a:,.2f}  ({delta:+.2f})")

        lr = result.get("learning_result")
        if lr and lr.get("ran"):
            log.info(f"Learning cycle #{lr['cycle']} ran — {len(lr.get('changes', {}))} weights updated")

    except Exception as e:
        log.exception(f"Orchestrator cycle failed: {e}")
        sys.exit(1)

    log.info("CYCLE END")


if __name__ == "__main__":
    main()
