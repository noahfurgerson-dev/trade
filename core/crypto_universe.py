"""
Crypto Universe — Dynamic Robinhood Pair Registry
───────────────────────────────────────────────────
Fetches every tradeable pair from the Robinhood API and provides
filtered views for each strategy type.  Result is cached to disk
for 6 hours so strategies don't each hit the API at startup.

Usage:
    from core.crypto_universe import get_all_pairs, get_pairs_for

    all_pairs  = get_all_pairs()          # ['BTC-USD', 'ETH-USD', ...]
    dca_pairs  = get_pairs_for("dca")     # Major coins only
    meme_pairs = get_pairs_for("meme")    # Meme/high-volatility coins
    full_map   = get_ticker_map()         # {'BTC': 'BTC-USD', ...}
"""

import os
import json
from datetime import datetime, timedelta

_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_FILE = os.path.join(_ROOT, "data", "crypto_pairs_cache.json")
_CACHE_TTL  = timedelta(hours=6)

# ── Hardcoded fallback in case the API is unreachable ─────────────────────────
_FALLBACK_PAIRS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD", "DOGE-USD",
    "AVAX-USD", "LINK-USD", "BNB-USD", "LTC-USD", "BCH-USD", "DOT-USD",
    "UNI-USD", "AAVE-USD", "SHIB-USD", "PEPE-USD", "MATIC-USD",
]

# ── Token category classifications ────────────────────────────────────────────

# Never trade — stablecoins / wrapped assets / non-price-moving
_EXCLUDE = {"USDC-USD", "PAXG-USD"}

# Tier 1 — High-cap, liquid, suitable for all strategies
MAJOR_PAIRS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD",
    "ADA-USD", "AVAX-USD", "DOT-USD", "LTC-USD", "BCH-USD",
    "LINK-USD", "UNI-USD", "AAVE-USD", "HBAR-USD", "XLM-USD",
]

# Tier 2 — Mid-cap DeFi / L2 / infrastructure
DEFI_L2_PAIRS = [
    "LINK-USD", "AAVE-USD", "UNI-USD", "COMP-USD", "CRV-USD",
    "SNX-USD", "LDO-USD", "GRT-USD", "ARB-USD", "OP-USD",
    "IMX-USD", "RENDER-USD", "PYTH-USD", "EIGEN-USD", "QNT-USD",
    "SUI-USD", "SEI-USD", "TON-USD", "ONDO-USD", "W-USD", "ZRO-USD",
]

# Tier 3 — Meme / high-volatility / speculative
MEME_PAIRS = [
    "DOGE-USD", "SHIB-USD", "PEPE-USD", "BONK-USD", "FLOKI-USD",
    "WIF-USD", "POPCAT-USD", "MEW-USD", "PNUT-USD", "MOODENG-USD",
    "TRUMP-USD", "PENGU-USD",
]

# ── Strategy-specific views ───────────────────────────────────────────────────

_STRATEGY_FILTERS = {
    # DCA: only blue-chip coins worth accumulating long-term
    "dca":         ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "XRP-USD"],

    # Momentum: broad — includes mid-caps where trends are strongest
    "momentum":    MAJOR_PAIRS + DEFI_L2_PAIRS[:8],

    # Mean reversion: liquid coins with reliable reversion behaviour
    "mean_reversion": MAJOR_PAIRS[:8] + ["XRP-USD", "LINK-USD", "ARB-USD"],

    # Fear & Greed contrarian: major coins only — predictable correlation
    "fear_greed":  ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "AVAX-USD"],

    # Trending scanner: ALL pairs — catches viral momentum early
    "trending":    None,   # None = all tradeable pairs

    # AI / news signals: major + defi — meaningful enough for AI to reason about
    "ai_signals":  MAJOR_PAIRS + DEFI_L2_PAIRS[:10],

    # News sentiment: same as AI signals
    "news_sentiment": MAJOR_PAIRS + DEFI_L2_PAIRS[:10],

    # Meme play: dedicated high-volatility list
    "meme":        MEME_PAIRS,

    # Rebalancer: core long-term holdings only
    "rebalancer":  ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD"],
}


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache() -> list[str] | None:
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE) as f:
                d = json.load(f)
            cached_at = datetime.fromisoformat(d["timestamp"])
            if datetime.now() - cached_at < _CACHE_TTL:
                return d["pairs"]
    except Exception:
        pass
    return None


def _save_cache(pairs: list[str]):
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump({"timestamp": datetime.now().isoformat(), "pairs": pairs}, f)
    except Exception:
        pass


# ── Main fetch function ───────────────────────────────────────────────────────

def get_all_pairs(rh_client=None, force_refresh: bool = False) -> list[str]:
    """
    Return all currently tradeable Robinhood crypto pairs.
    Uses 6-hour disk cache. Pass rh_client to do a live refresh.
    Returns fallback list if the API is unreachable.
    """
    if not force_refresh:
        cached = _load_cache()
        if cached:
            return cached

    if rh_client and rh_client.is_configured():
        try:
            data    = rh_client._get("/api/v1/crypto/trading/trading_pairs/")
            results = data.get("results", [])
            pairs   = sorted([
                r["symbol"] for r in results
                if r.get("status") == "tradable"
                and r.get("symbol") not in _EXCLUDE
                and r.get("quote_code") == "USD"
            ])
            if pairs:
                _save_cache(pairs)
                print(f"[CryptoUniverse] Fetched {len(pairs)} tradeable pairs from Robinhood")
                return pairs
        except Exception as e:
            print(f"[CryptoUniverse] API fetch failed: {e} — using fallback list")

    return [p for p in _FALLBACK_PAIRS if p not in _EXCLUDE]


def get_ticker_map(rh_client=None) -> dict[str, str]:
    """
    Returns {'BTC': 'BTC-USD', 'ETH': 'ETH-USD', ...} for all tradeable pairs.
    """
    pairs = get_all_pairs(rh_client)
    return {p.split("-")[0]: p for p in pairs}


def get_pairs_for(strategy: str, rh_client=None) -> list[str]:
    """
    Return the appropriate pair list for a given strategy.
    Falls back to all tradeable pairs if strategy not found.
    """
    filtered = _STRATEGY_FILTERS.get(strategy)
    if filtered is None:
        # "None" means use the full live list
        return get_all_pairs(rh_client)

    # Intersect with live tradeable pairs to avoid trading delisted tokens
    live = set(get_all_pairs(rh_client))
    return [p for p in filtered if p in live]


def get_tickers_for(strategy: str, rh_client=None) -> list[str]:
    """
    Like get_pairs_for() but returns bare tickers: ['BTC', 'ETH', ...]
    """
    return [p.split("-")[0] for p in get_pairs_for(strategy, rh_client)]


def refresh_cache(rh_client) -> list[str]:
    """Force a live refresh of the tradeable pairs cache."""
    return get_all_pairs(rh_client, force_refresh=True)
