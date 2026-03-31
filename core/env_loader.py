"""
Shared environment bootstrapper and client factory.

Centralises the .env loading + os.environ push that was previously duplicated
in run_scheduler.py and run_cycle_once.py.  Import and call these two functions
at the top of any entry-point script.
"""

import logging
import os

# Resolve the repo root relative to this file (core/ is one level down)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(root_dir: str = None) -> None:
    """
    Load .env file and push every key into os.environ.
    Safe to call multiple times — subsequent calls are no-ops unless the file
    changes.  Pass root_dir to override the default repo-root location.
    """
    if root_dir is None:
        root_dir = _ROOT
    env_file = os.path.join(root_dir, ".env")

    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_file, override=True)
    except Exception:
        pass

    # Belt-and-suspenders: force every key in regardless of dotenv behaviour
    try:
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k and v:
                    os.environ[k] = v
    except Exception:
        pass


def build_clients(log: logging.Logger = None):
    """
    Initialise Robinhood and Alpaca clients from environment variables.
    Returns (rh_client, alpaca_client) — either may be None if keys are absent
    or initialisation fails.
    """
    if log is None:
        log = logging.getLogger(__name__)

    rh, alpaca = None, None

    rh_key  = os.getenv("RH_API_KEY",     "").strip()
    rh_priv = os.getenv("RH_PRIVATE_KEY", "").strip()
    if rh_key and rh_priv:
        try:
            from core.robinhood import RobinhoodClient
            rh = RobinhoodClient()
            if rh.is_configured():
                log.info("Robinhood client ready")
            else:
                log.warning("Robinhood keys present but client not configured")
                rh = None
        except Exception as e:
            log.error(f"Robinhood init failed: {e}")

    alp_key    = os.getenv("ALPACA_API_KEY",    "").strip()
    alp_secret = os.getenv("ALPACA_API_SECRET", "").strip()
    if alp_key and alp_secret:
        try:
            from core.alpaca_client import AlpacaClient
            alpaca = AlpacaClient()
            if alpaca.is_configured():
                log.info("Alpaca client ready")
            else:
                log.warning("Alpaca keys present but client not configured")
                alpaca = None
        except Exception as e:
            log.error(f"Alpaca init failed: {e}")

    return rh, alpaca
