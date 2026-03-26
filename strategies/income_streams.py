"""
Multi-Income Stream Manager
────────────────────────────
Tracks and manages multiple revenue streams toward the $100k/year goal.
Beyond just trading — this is how real wealth compounds:

  1. Crypto Staking      — ETH/SOL staking yields (4–8% APY)
  2. Covered Calls       — Sell premium on held crypto ETFs
  3. T-Bills / HY Savings— Risk-free ~5% on uninvested cash
  4. DeFi Yield          — Liquidity pools, lending protocols
  5. Dividend Capture    — Stocks with high dividend yield
  6. AI SaaS Income      — Tracked external revenue logged manually

Each stream is tracked with estimated monthly income and ROI.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

STREAMS_FILE = Path(__file__).parent.parent / "data" / "income_streams.json"

# ── Stream definitions ─────────────────────────────────────────────────────────

DEFAULT_STREAMS = [
    {
        "id": "rh_trading",
        "name": "RH Crypto Trading",
        "description": "Active momentum + mean-reversion crypto strategies",
        "category": "Trading",
        "target_monthly": 3000,
        "current_apy": None,  # dynamic
        "capital_allocated": 0,
        "active": True,
        "icon": "📈",
        "color": "#58a6ff",
    },
    {
        "id": "eth_staking",
        "name": "ETH Staking",
        "description": "Ethereum validator/liquid staking (Lido, Coinbase CBeth)",
        "category": "Staking",
        "target_monthly": 500,
        "current_apy": 4.2,
        "capital_allocated": 0,
        "active": False,
        "icon": "🔷",
        "color": "#627eea",
        "setup_url": "https://stake.lido.fi",
        "notes": "4–5% APY, liquid via stETH. No lockup.",
    },
    {
        "id": "sol_staking",
        "name": "SOL Staking",
        "description": "Solana native staking (~7% APY)",
        "category": "Staking",
        "target_monthly": 400,
        "current_apy": 6.8,
        "capital_allocated": 0,
        "active": False,
        "icon": "🟣",
        "color": "#9945ff",
        "notes": "Delegate to high-uptime validators. Unstaking: 2–3 epoch delay.",
    },
    {
        "id": "defi_yield",
        "name": "DeFi Yield Farming",
        "description": "Liquidity pools on Uniswap/Curve/Aave (stable pairs)",
        "category": "DeFi",
        "target_monthly": 1200,
        "current_apy": 12.0,
        "capital_allocated": 0,
        "active": False,
        "icon": "🌾",
        "color": "#3fb950",
        "notes": "USDC/USDT Curve pool ~8–15% APY. Low impermanent loss risk.",
    },
    {
        "id": "tbills",
        "name": "T-Bills / SGOV ETF",
        "description": "Park uninvested cash in 3-month T-Bills or SGOV ETF",
        "category": "Fixed Income",
        "target_monthly": 400,
        "current_apy": 5.25,
        "capital_allocated": 0,
        "active": False,
        "icon": "🏦",
        "color": "#f0883e",
        "notes": "Risk-free 5.25% APY on idle cash. Buy SGOV in RH brokerage.",
    },
    {
        "id": "dividends",
        "name": "Dividend Stocks",
        "description": "High-yield dividend portfolio (JEPI, SCHD, O, MAIN)",
        "category": "Dividends",
        "target_monthly": 800,
        "current_apy": 8.5,
        "capital_allocated": 0,
        "active": False,
        "icon": "💰",
        "color": "#f7931a",
        "notes": "JEPI: ~9% monthly distributions. SCHD: quality dividend growth.",
    },
    {
        "id": "covered_calls",
        "name": "Covered Call Writing",
        "description": "Sell OTM calls on BTC/ETH ETF positions (IBIT, ETHA)",
        "category": "Options",
        "target_monthly": 1500,
        "current_apy": 18.0,
        "capital_allocated": 0,
        "active": False,
        "icon": "📋",
        "color": "#d29922",
        "notes": "Sell 30-45 DTE calls ~10% OTM. Close at 50% profit.",
    },
    {
        "id": "saas_income",
        "name": "Digital / SaaS Income",
        "description": "External: Gumroad products, API subscriptions, automation tools",
        "category": "Digital",
        "target_monthly": 2000,
        "current_apy": None,
        "capital_allocated": 0,
        "active": False,
        "icon": "💻",
        "color": "#58a6ff",
        "notes": "Non-trading income. Log manually. Scale via Claude-powered tools.",
    },
]


def _load() -> dict:
    STREAMS_FILE.parent.mkdir(exist_ok=True)
    if STREAMS_FILE.exists():
        try:
            return json.loads(STREAMS_FILE.read_text())
        except Exception:
            pass
    return {"streams": DEFAULT_STREAMS, "log": []}


def _save(data: dict):
    STREAMS_FILE.parent.mkdir(exist_ok=True)
    STREAMS_FILE.write_text(json.dumps(data, indent=2, default=str))


def get_streams() -> list[dict]:
    return _load()["streams"]


def update_stream(stream_id: str, **kwargs):
    data = _load()
    for s in data["streams"]:
        if s["id"] == stream_id:
            s.update(kwargs)
            break
    _save(data)


def log_income(stream_id: str, amount: float, note: str = ""):
    data = _load()
    data["log"].append({
        "date": str(date.today()),
        "stream_id": stream_id,
        "amount": amount,
        "note": note,
    })
    _save(data)


def get_income_log(days: int = 30) -> list[dict]:
    data = _load()
    cutoff = str(date.today() - timedelta(days=days))
    return [e for e in data["log"] if e["date"] >= cutoff]


def estimate_monthly_income(capital_by_stream: dict[str, float]) -> dict:
    """
    Given allocated capital per stream, estimate monthly income.
    capital_by_stream: {stream_id: dollar_amount}
    """
    streams = get_streams()
    result = {}
    total_monthly = 0.0
    for s in streams:
        cap = capital_by_stream.get(s["id"], s.get("capital_allocated", 0))
        apy = s.get("current_apy")
        if apy and cap:
            monthly = cap * (apy / 100) / 12
        else:
            monthly = 0.0
        result[s["id"]] = {
            "name": s["name"],
            "monthly_estimate": monthly,
            "annual_estimate": monthly * 12,
            "apy": apy,
            "capital": cap,
            "active": s["active"],
        }
        if s["active"]:
            total_monthly += monthly
    result["_total_monthly"] = total_monthly
    result["_total_annual"] = total_monthly * 12
    return result
