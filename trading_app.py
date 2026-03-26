"""
$100K Digital Wealth Platform
Multi-Income Stream Dashboard

Income engines:
  - Robinhood Crypto Trading (momentum, DCA, mean reversion, fear/greed, trending, AI)
  - Alpaca Stocks/ETFs (momentum, dividends, treasury, options income)
  - ETH/SOL Staking
  - DeFi Yield Farming
  - T-Bills / Fixed Income
  - Dividend Stocks
  - Covered Calls
  - Digital / SaaS Income

Strategy Orchestrator: automatically selects the best strategies to run
based on market conditions, Fear & Greed index, portfolio state, and timing.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import time
import os
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

load_dotenv(override=True)  # override=True picks up .env changes without restart

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="RH Trading — $100K Goal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
  /* Dark trading theme */
  .main { background-color: #0d1117; }
  .stApp { background-color: #0d1117; }

  /* Metric cards */
  .metric-card {
    background: linear-gradient(135deg, #1a1f2e, #161b27);
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 20px;
    text-align: center;
  }
  .metric-label {
    color: #8b949e;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 4px;
  }
  .metric-value {
    color: #e6edf3;
    font-size: 1.8rem;
    font-weight: 700;
    font-family: 'Courier New', monospace;
  }
  .metric-value.positive { color: #3fb950; }
  .metric-value.negative { color: #f85149; }
  .metric-value.accent { color: #58a6ff; }

  /* Goal ring container */
  .goal-header {
    background: linear-gradient(135deg, #0d1117, #161b27);
    border: 1px solid #21262d;
    border-radius: 16px;
    padding: 24px;
    margin-bottom: 24px;
  }

  /* Section headers */
  .section-title {
    color: #58a6ff;
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin: 20px 0 12px 0;
    border-bottom: 1px solid #21262d;
    padding-bottom: 8px;
  }

  /* Trade log */
  .trade-row {
    background: #161b27;
    border-radius: 6px;
    padding: 8px 12px;
    margin: 4px 0;
    font-family: monospace;
    font-size: 0.85rem;
    border-left: 3px solid #30363d;
  }
  .trade-row.buy { border-left-color: #3fb950; }
  .trade-row.sell { border-left-color: #f85149; }

  /* Hide Streamlit chrome */
  #MainMenu { visibility: hidden; }
  footer { visibility: hidden; }
  .stDeployButton { display: none; }
</style>
""", unsafe_allow_html=True)

# ── Session state init ─────────────────────────────────────────────────────────

def init_state():
    defaults = {
        "client": None,
        "alpaca_client": None,
        "logged_in": False,
        "auto_refresh": False,
        "strategy_log": [],
        "active_strategies": set(),
        "last_refresh": None,
        "demo_mode": False,
        "orch_result": None,
        "orch_evaluation": [],
        "intelligence_tab": "Orchestrator",
        # ── Scheduler ──────────────────────────────────────────────
        "auto_orchestrate": False,       # master on/off switch
        "orch_cadence_minutes": 30,      # how often to fire
        "last_orch_run": None,           # datetime of last auto-run
        "orch_run_count": 0,             # total auto-runs this session
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ── Robinhood client ───────────────────────────────────────────────────────────

def get_client():
    from core.robinhood import RobinhoodClient
    return RobinhoodClient()

# ── Demo data (when not connected) ────────────────────────────────────────────

def demo_stats():
    """Realistic demo data for UI preview."""
    from core import goal_tracker
    today = date.today()
    days = [(today - timedelta(days=x)).isoformat() for x in range(119, -1, -1)]
    import random, math
    random.seed(42)
    equity = 28500.0
    curve = []
    for i, d in enumerate(days):
        delta = equity * 0.0035 * (1 + math.sin(i / 10) * 0.5) * random.uniform(0.3, 1.7)
        delta *= random.choice([1, 1, 1, -0.6])
        equity = max(equity + delta, 20000)
        curve.append({"date": d, "equity": equity})

    total_gain = equity - 28500
    daily_avg = total_gain / 120
    return {
        "annual_goal": 100_000,
        "starting_balance": 28500.0,
        "current_equity": equity,
        "total_gain": total_gain,
        "total_gain_pct": total_gain / 28500 * 100,
        "goal_progress_pct": max(0, min(total_gain / 100_000 * 100, 100)),
        "days_elapsed": 120,
        "days_to_goal": int((100_000 - total_gain) / daily_avg) if daily_avg > 0 else 999,
        "today_pnl": random.uniform(180, 620),
        "week_pnl": random.uniform(800, 2400),
        "month_pnl": random.uniform(3000, 8500),
        "daily_avg": daily_avg,
        "daily_goal": 100_000 / 365,
        "weekly_goal": 100_000 / 52,
        "monthly_goal": 100_000 / 12,
        "projected_annual": daily_avg * 365,
        "equity_curve": curve,
        "trades_pnl": [
            {"date": (today - timedelta(days=x)).isoformat(),
             "pnl": round(random.uniform(-300, 900), 2),
             "ticker": random.choice(["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD"]),
             "note": random.choice(["Momentum buy", "DCA", "Mean reversion", "Stop loss"])}
            for x in range(20)
        ],
    }

def demo_holdings():
    return [
        {"symbol": "BTC", "pair": "BTC-USD", "quantity": 0.42, "avg_cost": 61200,
         "current_price": 68450, "market_value": 28749, "unrealized_pnl": 3045, "pnl_pct": 11.8},
        {"symbol": "ETH", "pair": "ETH-USD", "quantity": 4.8, "avg_cost": 3200,
         "current_price": 3580, "market_value": 17184, "unrealized_pnl": 1824, "pnl_pct": 11.9},
        {"symbol": "SOL", "pair": "SOL-USD", "quantity": 42, "avg_cost": 148,
         "current_price": 172, "market_value": 7224, "unrealized_pnl": 1008, "pnl_pct": 16.2},
        {"symbol": "DOGE", "pair": "DOGE-USD", "quantity": 12000, "avg_cost": 0.115,
         "current_price": 0.132, "market_value": 1584, "unrealized_pnl": 204, "pnl_pct": 14.8},
    ]

# ── Helpers ────────────────────────────────────────────────────────────────────

def fmt_usd(val: float) -> str:
    sign = "+" if val > 0 else ""
    return f"{sign}${val:,.2f}"

def fmt_pct(val: float) -> str:
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.2f}%"

def color_class(val: float) -> str:
    return "positive" if val >= 0 else "negative"

def pnl_color(val: float) -> str:
    return "#3fb950" if val >= 0 else "#f85149"

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## RH Trading Platform")
    st.markdown("---")

    # Connection
    st.markdown("### Connection")
    demo_toggle = st.toggle("Demo Mode", value=st.session_state.demo_mode)
    st.session_state.demo_mode = demo_toggle

    if not demo_toggle:
        api_key = st.text_input("API Key", value=os.getenv("RH_API_KEY", ""),
                                 type="password", placeholder="rh-api-key-...")
        private_key = st.text_input("Private Key (Base64)", value=os.getenv("RH_PRIVATE_KEY", ""),
                                     type="password", placeholder="base64-encoded Ed25519 key")

        if st.button("Connect to Robinhood", type="primary"):
            if api_key and private_key:
                # Write keys to .env so they survive restarts
                env_path = os.path.join(os.path.dirname(__file__), ".env")
                lines = []
                if os.path.exists(env_path):
                    with open(env_path) as f:
                        lines = [l for l in f.readlines()
                                 if not l.startswith("RH_API_KEY=")
                                 and not l.startswith("RH_PRIVATE_KEY=")]
                lines += [f"RH_API_KEY={api_key}\n", f"RH_PRIVATE_KEY={private_key}\n"]
                with open(env_path, "w") as f:
                    f.writelines(lines)
                # Reload env and create fresh client
                load_dotenv(override=True)
                os.environ["RH_API_KEY"] = api_key
                os.environ["RH_PRIVATE_KEY"] = private_key
                from core.robinhood import RobinhoodClient
                client = RobinhoodClient()
                # Check if the private key failed to parse
                key_err = client.get_key_error()
                if key_err:
                    st.error(f"Private key error: {key_err}")
                    st.info("Make sure you're using the **PRIVATE KEY** printed by `generate_keys.py` — not the public key.")
                elif client.is_configured():
                    with st.spinner("Connecting to Robinhood..."):
                        try:
                            acct = client.get_account()
                            if "error" not in acct:
                                st.session_state.client = client
                                st.session_state.logged_in = True
                                st.session_state.demo_mode = False
                                st.success("Connected! Loading your portfolio...")
                                st.rerun()
                            else:
                                st.error(f"Robinhood API error: {acct['error']}")
                        except Exception as e:
                            err_str = str(e)
                            st.error(f"Connection failed: {err_str}")
                            if "missing required headers" in err_str.lower():
                                st.warning(
                                    "**Tip:** This usually means your API Key ID has extra spaces or newlines. "
                                    "Try re-copying it directly from the Robinhood API Keys page and paste it again."
                                )
                            elif "401" in err_str or "403" in err_str:
                                st.warning("**Tip:** Double-check that the API Key ID from Robinhood matches the public key you registered.")
                else:
                    st.error("Could not initialise signing key — check your Private Key")
            else:
                st.warning("Enter both your API Key and Private Key")
    else:
        st.info("Running in Demo Mode\nNo real trades executed")
        st.session_state.logged_in = True

    st.markdown("---")

    # ── Alpaca Connection ─────────────────────────────────────────────────
    st.markdown("### Alpaca Markets (Stocks/ETFs)")
    alpaca_key    = st.text_input("Alpaca API Key", value=os.getenv("ALPACA_API_KEY",""),
                                   type="password", placeholder="PK...", key="alpaca_key_input")
    alpaca_secret = st.text_input("Alpaca Secret",  value=os.getenv("ALPACA_API_SECRET",""),
                                   type="password", placeholder="secret...", key="alpaca_sec_input")
    alpaca_paper  = st.toggle("Paper Trading", value=True, key="alpaca_paper_toggle")
    if st.button("Connect Alpaca", key="connect_alpaca"):
        if alpaca_key and alpaca_secret:
            env_path = os.path.join(os.path.dirname(__file__), ".env")
            lines = []
            if os.path.exists(env_path):
                with open(env_path) as f:
                    lines = [l for l in f.readlines()
                             if not l.startswith("ALPACA_API_KEY=")
                             and not l.startswith("ALPACA_API_SECRET=")
                             and not l.startswith("ALPACA_PAPER=")]
            lines += [
                f"ALPACA_API_KEY={alpaca_key}\n",
                f"ALPACA_API_SECRET={alpaca_secret}\n",
                f"ALPACA_PAPER={'false' if not alpaca_paper else 'true'}\n",
            ]
            with open(env_path, "w") as f:
                f.writelines(lines)
            load_dotenv(override=True)
            os.environ["ALPACA_API_KEY"]    = alpaca_key
            os.environ["ALPACA_API_SECRET"] = alpaca_secret
            os.environ["ALPACA_PAPER"]      = "false" if not alpaca_paper else "true"
            from core.alpaca_client import AlpacaClient
            ac = AlpacaClient()
            if ac.is_configured():
                test = ac.get_account()
                if "error" not in test:
                    st.session_state.alpaca_client = ac
                    st.success("Alpaca connected!")
                    st.rerun()
                else:
                    st.error(f"Alpaca error: {test['error']}")
            else:
                st.error("Invalid Alpaca credentials")
        else:
            st.warning("Enter both Alpaca API Key and Secret")

    # Show status
    if st.session_state.alpaca_client:
        st.success("✅ Alpaca connected")
    elif os.getenv("ALPACA_API_KEY"):
        from core.alpaca_client import AlpacaClient
        _ac = AlpacaClient()
        if _ac.is_configured():
            st.session_state.alpaca_client = _ac
            st.caption("✅ Alpaca loaded from .env")

    st.markdown("---")
    st.markdown("### Strategy Control")
    st.caption("Manual strategy toggles or use **Auto-Orchestrate** on the main panel.")

    strategies_available = {
        "Momentum":       "🔥 Crypto trend following",
        "Mean Reversion": "📉 Buy oversold dips",
        "DCA":            "📅 Dollar cost averaging",
        "Fear & Greed":   "😱 Contrarian extremes",
        "AI Signals":     "🤖 Claude market analysis",
        "Rebalancer":     "⚖️  Portfolio drift fix",
        "Trending":       "📡 CoinGecko hot coins",
        "Stock Momentum": "📈 Alpaca swing trades",
        "Dividend":       "💰 ETF income buying",
        "Treasury":       "🏦 T-Bill yield",
        "Options Income": "📋 CC/CSP premiums",
    }
    for name, desc in strategies_available.items():
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption(f"**{name}** — {desc}")
        with col2:
            is_active = name in st.session_state.active_strategies
            if st.button("ON" if is_active else "OFF",
                         key=f"strat_{name}",
                         type="primary" if is_active else "secondary"):
                if is_active:
                    st.session_state.active_strategies.discard(name)
                else:
                    st.session_state.active_strategies.add(name)
                st.rerun()

    st.markdown("---")
    st.markdown("### Quick Trade")
    trade_pair = st.selectbox("Pair", ["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "ADA-USD"])
    trade_side = st.radio("Side", ["Buy", "Sell"], horizontal=True)
    trade_usd = st.number_input("USD Amount", min_value=10.0, value=100.0, step=10.0)

    if st.button(f"{'Buy' if trade_side == 'Buy' else 'Sell'} {trade_pair}",
                  type="primary" if trade_side == "Buy" else "secondary",
                  width='stretch'):
        if demo_toggle:
            st.success(f"[DEMO] {trade_side} ${trade_usd:.0f} of {trade_pair}")
        elif st.session_state.logged_in and st.session_state.client:
            client = st.session_state.client
            price = client.get_quote(trade_pair).get("price", 0)
            qty = round(trade_usd / price, 8) if price else 0
            if qty > 0:
                if trade_side == "Buy":
                    result = client.buy_market(trade_pair, qty)
                else:
                    result = client.sell_market(trade_pair, qty)
                if "error" in result:
                    st.error(result["error"])
                else:
                    st.success(f"Order placed: {result.get('id', 'submitted')}")
            else:
                st.error("Could not get price")
        else:
            st.warning("Connect to Robinhood first")

    st.markdown("---")
    st.markdown("### Auto-Orchestrator")

    auto_orch = st.toggle(
        "Run Automatically",
        value=st.session_state.auto_orchestrate,
        help="Runs the orchestrator on the selected cadence. Keep this tab open.",
    )
    st.session_state.auto_orchestrate = auto_orch

    cadence_label = st.selectbox(
        "Cadence",
        options=["Every 15 min", "Every 30 min", "Every 1 hour", "Every 2 hours", "Every 4 hours"],
        index=["Every 15 min", "Every 30 min", "Every 1 hour", "Every 2 hours", "Every 4 hours"].index(
            {15: "Every 15 min", 30: "Every 30 min", 60: "Every 1 hour",
             120: "Every 2 hours", 240: "Every 4 hours"}.get(
                st.session_state.orch_cadence_minutes, "Every 30 min")),
        disabled=not auto_orch,
    )
    cadence_map = {
        "Every 15 min": 15, "Every 30 min": 30, "Every 1 hour": 60,
        "Every 2 hours": 120, "Every 4 hours": 240,
    }
    st.session_state.orch_cadence_minutes = cadence_map[cadence_label]

    # ── Countdown display ────────────────────────────────────────
    if auto_orch and st.session_state.last_orch_run:
        elapsed   = (datetime.now() - st.session_state.last_orch_run).total_seconds()
        remaining = max(0, st.session_state.orch_cadence_minutes * 60 - elapsed)
        mins_left = int(remaining // 60)
        secs_left = int(remaining % 60)
        bar_pct   = 1 - (remaining / (st.session_state.orch_cadence_minutes * 60))
        st.markdown(f"""
        <div style="margin-top:8px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span style="color:#8b949e;font-size:0.75rem">Next run in</span>
            <span style="color:#58a6ff;font-size:0.75rem;font-weight:700">
              {mins_left:02d}:{secs_left:02d}
            </span>
          </div>
          <div style="background:#21262d;border-radius:999px;height:5px">
            <div style="width:{min(bar_pct*100,100):.1f}%;height:100%;
                        background:linear-gradient(90deg,#58a6ff,#3fb950);
                        border-radius:999px"></div>
          </div>
          <div style="color:#4d5566;font-size:0.7rem;margin-top:4px">
            Runs: {st.session_state.orch_run_count} this session &nbsp;·&nbsp;
            Last: {st.session_state.last_orch_run.strftime('%H:%M:%S')}
          </div>
        </div>
        """, unsafe_allow_html=True)
    elif auto_orch:
        st.caption("🟡 First run will fire shortly...")

    st.markdown("---")
    auto = st.toggle("Page Refresh (30s)", value=st.session_state.auto_refresh,
                     help="Keeps the page live so the scheduler can fire. Enable with Auto-Orchestrator.")
    st.session_state.auto_refresh = auto_orch or auto   # always refresh when scheduler is on

# ── Main dashboard ─────────────────────────────────────────────────────────────

# Get data
if st.session_state.demo_mode or not st.session_state.logged_in:
    stats = demo_stats()
    holdings = demo_holdings()
    cash = 4200.0
    orders = []
    is_live = False
else:
    client = st.session_state.client
    with st.spinner("Loading portfolio..."):
        from core import goal_tracker
        equity = client.get_total_equity()
        goal_tracker.initialize(equity)
        goal_tracker.record_snapshot(equity)
        stats = goal_tracker.get_stats(equity)
        holdings = client.get_holdings()
        cash = client.get_cash()
        orders = client.get_orders(20)
    is_live = True

# ── System Health Check (live mode only) ──────────────────────────────────────

if is_live:
    with st.expander("System Health", expanded=False):
        checks = {}

        # 1. API connectivity
        try:
            acct = client.get_account()
            checks["API Connected"] = (True, f"Account {acct.get('account_number')} · {acct.get('status')}")
        except Exception as e:
            checks["API Connected"] = (False, str(e))

        # 2. Market data
        try:
            q = client.get_quote("BTC-USD")
            checks["Market Data (BTC-USD)"] = (bool(q.get("price")), f"${float(q.get('price', 0)):,.2f}")
        except Exception as e:
            checks["Market Data (BTC-USD)"] = (False, str(e))

        # 3. Holdings readable
        try:
            h_count = len(holdings)
            checks["Holdings"] = (True, f"{h_count} position(s) loaded")
        except Exception as e:
            checks["Holdings"] = (False, str(e))

        # 4. Goal tracker
        try:
            checks["Goal Tracker"] = (bool(stats.get("current_equity")),
                                      f"Equity ${stats['current_equity']:,.2f} · Day {stats['days_elapsed']}")
        except Exception as e:
            checks["Goal Tracker"] = (False, str(e))

        # 5. Order history
        try:
            checks["Order History"] = (True, f"{len(orders)} recent order(s)")
        except Exception as e:
            checks["Order History"] = (False, str(e))

        # 6. Strategy imports
        try:
            from strategies.momentum           import MomentumStrategy
            from strategies.mean_reversion     import MeanReversionStrategy
            from strategies.dca                import DCAStrategy
            from strategies.fear_greed         import FearGreedStrategy
            from strategies.ai_signals         import AISignalStrategy
            from strategies.rebalancer         import RebalancerStrategy
            from strategies.trending_scanner   import TrendingScannerStrategy
            from strategies.stock_momentum     import StockMomentumStrategy
            from strategies.dividend_collector import DividendCollectorStrategy
            from strategies.options_income     import OptionsIncomeStrategy
            from strategies.treasury_income    import TreasuryIncomeStrategy
            from core.strategy_orchestrator    import StrategyOrchestrator
            checks["Strategies Loaded"] = (True, "11 strategies + Orchestrator")
        except Exception as e:
            checks["Strategies Loaded"] = (False, str(e))

        # 7. Alpaca connection
        if st.session_state.alpaca_client:
            try:
                alpaca_acct = st.session_state.alpaca_client.get_account()
                checks["Alpaca Markets"] = (
                    "error" not in alpaca_acct,
                    f"${float(alpaca_acct.get('portfolio_value', 0)):,.2f} portfolio"
                    if "error" not in alpaca_acct else alpaca_acct["error"]
                )
            except Exception as e:
                checks["Alpaca Markets"] = (False, str(e))
        else:
            checks["Alpaca Markets"] = (False, "Not connected (optional)")

        all_ok = all(ok for ok, _ in checks.values())
        col_l, col_r = st.columns([1, 3])
        col_l.markdown(
            f"<div style='font-size:2rem;text-align:center'>{'✅' if all_ok else '⚠️'}</div>",
            unsafe_allow_html=True)
        col_r.markdown(
            f"**{'All systems operational' if all_ok else 'Some checks failed'}**  \n"
            f"Last checked: {datetime.now().strftime('%H:%M:%S')}")

        for name, (ok, detail) in checks.items():
            dot = "🟢" if ok else "🔴"
            st.markdown(
                f"{dot} &nbsp; **{name}** &nbsp; <span style='color:#8b949e;font-size:0.85rem'>{detail}</span>",
                unsafe_allow_html=True)

# ── Header: Goal Progress ─────────────────────────────────────────────────────

st.markdown(f"""
<div style="background:linear-gradient(135deg,#0d1117,#161b27);
            border:1px solid #21262d;border-radius:16px;padding:24px;margin-bottom:20px">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px">
    <div>
      <div style="color:#8b949e;font-size:0.7rem;text-transform:uppercase;letter-spacing:2px">
        {'🔴 DEMO MODE' if not is_live else '🟢 LIVE'} &nbsp;|&nbsp; 2026 GOAL
      </div>
      <div style="color:#e6edf3;font-size:2rem;font-weight:800;margin:4px 0">$100,000</div>
      <div style="color:#8b949e;font-size:0.85rem">
        {stats['days_elapsed']} days in &nbsp;·&nbsp;
        {'∞' if not stats.get('days_to_goal') else stats['days_to_goal']} days remaining at current pace
      </div>
    </div>
    <div style="text-align:right">
      <div style="color:#8b949e;font-size:0.7rem;text-transform:uppercase;letter-spacing:2px">Portfolio Value</div>
      <div style="color:#58a6ff;font-size:2rem;font-weight:800">${stats['current_equity']:,.2f}</div>
      <div style="color:{'#3fb950' if stats['total_gain'] >= 0 else '#f85149'};font-size:0.9rem">
        {fmt_usd(stats['total_gain'])} ({fmt_pct(stats['total_gain_pct'])}) since start
      </div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# Progress bar
progress = min(stats["goal_progress_pct"] / 100, 1.0)
bar_color = "#3fb950" if progress > 0.5 else "#f0883e" if progress > 0.2 else "#58a6ff"
st.markdown(f"""
<div style="margin-bottom:24px">
  <div style="display:flex;justify-content:space-between;margin-bottom:6px">
    <span style="color:#8b949e;font-size:0.8rem">Goal Progress</span>
    <span style="color:#e6edf3;font-weight:700">{stats['goal_progress_pct']:.1f}%</span>
  </div>
  <div style="background:#21262d;border-radius:999px;height:14px;overflow:hidden">
    <div style="width:{min(stats['goal_progress_pct'], 100):.1f}%;height:100%;
                background:linear-gradient(90deg,{bar_color},{bar_color}cc);
                border-radius:999px;transition:width 0.5s ease">
    </div>
  </div>
  <div style="display:flex;justify-content:space-between;margin-top:4px">
    <span style="color:#8b949e;font-size:0.7rem">${stats['total_gain']:,.0f} earned</span>
    <span style="color:#8b949e;font-size:0.7rem">${100000 - stats['total_gain']:,.0f} remaining</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ── KPI Row ───────────────────────────────────────────────────────────────────

c1, c2, c3, c4, c5 = st.columns(5)

kpis = [
    ("Today's P&L", stats["today_pnl"], True),
    ("This Week", stats["week_pnl"], True),
    ("This Month", stats["month_pnl"], True),
    ("Projected Annual", stats["projected_annual"], False),
    ("Daily Avg vs Goal", stats["daily_avg"] - stats["daily_goal"], True),
]

for col, (label, val, show_sign) in zip([c1, c2, c3, c4, c5], kpis):
    with col:
        css = color_class(val)
        st.markdown(f"""
        <div class="metric-card">
          <div class="metric-label">{label}</div>
          <div class="metric-value {css}">{fmt_usd(val) if show_sign else f'${val:,.0f}'}</div>
        </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Charts Row ────────────────────────────────────────────────────────────────

chart_col, pace_col = st.columns([2, 1])

with chart_col:
    st.markdown('<div class="section-title">Equity Curve</div>', unsafe_allow_html=True)
    curve = stats["equity_curve"]
    if curve:
        df = pd.DataFrame(curve)
        df["date"] = pd.to_datetime(df["date"])

        # Goal line
        start_eq = stats["starting_balance"]
        n = len(df)
        goal_line = [start_eq + (100_000 / 365) * i for i in range(n)]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["equity"],
            mode="lines", name="Portfolio",
            line=dict(color="#58a6ff", width=2.5),
            fill="tozeroy",
            fillcolor="rgba(88,166,255,0.08)",
        ))
        fig.add_trace(go.Scatter(
            x=df["date"], y=goal_line,
            mode="lines", name="$100K Pace",
            line=dict(color="#3fb950", width=1.5, dash="dot"),
        ))
        fig.update_layout(
            paper_bgcolor="#0d1117",
            plot_bgcolor="#0d1117",
            font=dict(color="#8b949e", size=11),
            xaxis=dict(gridcolor="#21262d", showgrid=True),
            yaxis=dict(gridcolor="#21262d", showgrid=True,
                       tickformat="$,.0f"),
            legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="#21262d"),
            margin=dict(l=0, r=0, t=10, b=0),
            height=280,
        )
        st.plotly_chart(fig)
    else:
        st.info("No equity history yet — connect and start trading.")

with pace_col:
    st.markdown('<div class="section-title">Pacing vs Goal</div>', unsafe_allow_html=True)

    pacing = {
        "Daily": (stats["daily_avg"], stats["daily_goal"]),
        "Weekly": (stats["week_pnl"] / 7 * 7, stats["weekly_goal"]),
        "Monthly": (stats["month_pnl"], stats["monthly_goal"]),
    }

    for period, (actual, goal) in pacing.items():
        ratio = min((actual / goal * 100) if goal else 0, 150)
        bar_c = "#3fb950" if ratio >= 100 else "#f0883e" if ratio >= 60 else "#f85149"
        st.markdown(f"""
        <div style="margin-bottom:14px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span style="color:#8b949e;font-size:0.8rem">{period}</span>
            <span style="color:{bar_c};font-size:0.8rem;font-weight:700">
              {fmt_usd(actual)} <span style="color:#4d5566">/ {fmt_usd(goal)}</span>
            </span>
          </div>
          <div style="background:#21262d;border-radius:999px;height:8px">
            <div style="width:{min(ratio,100):.1f}%;height:100%;
                        background:{bar_c};border-radius:999px"></div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    # Projected annual gauge
    proj = stats["projected_annual"]
    gauge_pct = min(proj / 100_000 * 100, 100)
    st.markdown(f"""
    <div style="background:#161b27;border:1px solid #21262d;border-radius:10px;
                padding:16px;margin-top:8px;text-align:center">
      <div style="color:#8b949e;font-size:0.7rem;text-transform:uppercase;letter-spacing:1px">
        Projected Annual
      </div>
      <div style="color:{'#3fb950' if proj >= 100000 else '#f0883e'};font-size:1.6rem;
                  font-weight:800;font-family:monospace">
        ${proj:,.0f}
      </div>
      <div style="color:#4d5566;font-size:0.75rem">goal: $100,000</div>
    </div>
    """, unsafe_allow_html=True)

# ── Holdings Table ─────────────────────────────────────────────────────────────

st.markdown('<div class="section-title">Holdings</div>', unsafe_allow_html=True)

if holdings:
    total_value = sum(h["market_value"] for h in holdings) + cash
    alloc_labels, alloc_values, alloc_colors = [], [], []
    CRYPTO_COLORS = {
        "BTC": "#f7931a", "ETH": "#627eea", "SOL": "#9945ff",
        "DOGE": "#c3a634", "ADA": "#0033ad", "AVAX": "#e84142",
    }

    h_col, alloc_col = st.columns([3, 1])

    with h_col:
        # Header row
        cols = st.columns([1.5, 1, 1, 1, 1, 1.2])
        for c, label in zip(cols, ["Asset", "Qty", "Avg Cost", "Price", "Value", "P&L"]):
            c.markdown(f"<span style='color:#8b949e;font-size:0.75rem;text-transform:uppercase'>{label}</span>",
                       unsafe_allow_html=True)
        st.markdown("<hr style='border-color:#21262d;margin:4px 0'>", unsafe_allow_html=True)

        for h in holdings:
            cols = st.columns([1.5, 1, 1, 1, 1, 1.2])
            pnl_c = "#3fb950" if h["unrealized_pnl"] >= 0 else "#f85149"
            symbol_color = CRYPTO_COLORS.get(h["symbol"], "#58a6ff")
            cols[0].markdown(f"<span style='color:{symbol_color};font-weight:700'>{h['symbol']}</span>", unsafe_allow_html=True)
            cols[1].markdown(f"<span style='color:#e6edf3'>{h['quantity']:.4f}</span>", unsafe_allow_html=True)
            cols[2].markdown(f"<span style='color:#8b949e'>${h['avg_cost']:,.2f}</span>", unsafe_allow_html=True)
            cols[3].markdown(f"<span style='color:#e6edf3'>${h['current_price']:,.2f}</span>", unsafe_allow_html=True)
            cols[4].markdown(f"<span style='color:#e6edf3'>${h['market_value']:,.2f}</span>", unsafe_allow_html=True)
            cols[5].markdown(
                f"<span style='color:{pnl_c}'>{fmt_usd(h['unrealized_pnl'])} ({fmt_pct(h['pnl_pct'])})</span>",
                unsafe_allow_html=True)

            alloc_labels.append(h["symbol"])
            alloc_values.append(h["market_value"])
            alloc_colors.append(symbol_color)

        # Cash row
        st.markdown("<hr style='border-color:#21262d;margin:4px 0'>", unsafe_allow_html=True)
        cols = st.columns([1.5, 1, 1, 1, 1, 1.2])
        cols[0].markdown("<span style='color:#8b949e'>USD Cash</span>", unsafe_allow_html=True)
        cols[4].markdown(f"<span style='color:#e6edf3'>${cash:,.2f}</span>", unsafe_allow_html=True)
        alloc_labels.append("Cash")
        alloc_values.append(cash)
        alloc_colors.append("#4d5566")

    with alloc_col:
        fig_pie = go.Figure(go.Pie(
            labels=alloc_labels, values=alloc_values,
            marker=dict(colors=alloc_colors),
            hole=0.6,
            textinfo="label+percent",
            textfont=dict(color="#8b949e", size=10),
            hovertemplate="<b>%{label}</b><br>$%{value:,.0f}<extra></extra>",
        ))
        fig_pie.update_layout(
            paper_bgcolor="#0d1117",
            plot_bgcolor="#0d1117",
            showlegend=False,
            margin=dict(l=0, r=0, t=0, b=0),
            height=200,
            annotations=[dict(
                text=f"${total_value:,.0f}",
                x=0.5, y=0.5, font_size=13,
                font_color="#e6edf3",
                showarrow=False,
            )],
        )
        st.plotly_chart(fig_pie)
else:
    st.info("No holdings. Buy some crypto to get started!")

# ══════════════════════════════════════════════════════════════════════════════
# INTELLIGENCE HUB
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="section-title">Intelligence Hub — Strategy Orchestrator</div>',
            unsafe_allow_html=True)
st.caption(
    "The orchestrator reads market conditions (Fear & Greed, portfolio state, time of day) "
    "and automatically selects the best strategies to run. Each decision includes a full reason."
)

# ── Orchestrator control bar ───────────────────────────────────────────────────
orch_c1, orch_c2, orch_c3, orch_c4 = st.columns([1, 1, 1, 2])

run_auto  = orch_c1.button("🤖 Auto-Run All",   type="primary",    key="orch_auto_run")
run_eval  = orch_c2.button("🔍 Evaluate Only",  type="secondary",  key="orch_eval_only")
run_manual= orch_c3.button("▶️  Run Selected",   type="secondary",  key="orch_manual_run")
clear_log = orch_c4.button("🗑  Clear Log",       type="secondary",  key="orch_clear")

if clear_log:
    st.session_state.strategy_log = []
    st.session_state.orch_result  = None
    st.session_state.orch_evaluation = []
    st.rerun()

# ── Execute orchestrator ────────────────────────────────────────────────────────
def _poll_order_status(rh_client, order_id: str, status_widget, max_polls: int = 6):
    """Poll a Robinhood order until filled/rejected or max_polls reached."""
    TERMINAL = {"filled", "canceled", "rejected", "failed"}
    for i in range(max_polls):
        time.sleep(2)
        o = rh_client.get_order(order_id)
        state = o.get("state", "unknown")
        filled = o.get("filled_qty", 0)
        price  = o.get("avg_price", 0)
        if state in TERMINAL:
            if state == "filled":
                status_widget.write(
                    f"    ✅ **FILLED** {filled:.6f} @ ${price:,.4f} "
                    f"= **${filled * price:,.2f}**"
                )
            else:
                status_widget.write(f"    ❌ Order {state.upper()}")
            return state
        status_widget.write(f"    ⏳ [{i+1}/{max_polls}] Status: `{state}` — waiting...")
    status_widget.write("    ⏱ Timed out polling — check Recent Orders for final status")
    return "unknown"


def _push_log(msg: str, level: str = "INFO"):
    st.session_state.strategy_log.insert(0, {
        "time":  datetime.now().strftime("%H:%M:%S"),
        "msg":   msg,
        "level": level,
    })


def _run_orchestrator(dry_run: bool = False):
    from core.strategy_orchestrator import StrategyOrchestrator
    rh     = st.session_state.client if st.session_state.logged_in else None
    alpaca = st.session_state.alpaca_client

    if not rh and not alpaca:
        st.warning("Connect to Robinhood or Alpaca first.")
        return

    label = "Evaluating strategies..." if dry_run else "Running orchestrator..."
    with st.status(label, expanded=True) as status:

        st.write("**Step 1 — Reading market conditions**")
        try:
            import requests as _req
            fg_raw = _req.get("https://api.alternative.me/fng/?limit=1", timeout=4).json()
            fg_val = int(fg_raw["data"][0]["value"])
            fg_lbl = fg_raw["data"][0]["value_classification"]
        except Exception:
            fg_val, fg_lbl = 50, "Neutral"

        cash    = rh.get_cash() if rh else 0
        equity  = rh.get_total_equity() if rh else 0
        st.write(
            f"  📊 Fear & Greed: **{fg_val}** ({fg_lbl})  |  "
            f"RH Cash: **${cash:,.2f}**  |  Portfolio: **${equity:,.2f}**"
        )

        if cash < 10 and rh and not dry_run:
            st.warning(
                "⚠️ **Buying power is very low ($" + f"{cash:.2f}).** "
                "Strategies will HOLD — add funds to your Robinhood account to enable trades."
            )

        st.write("**Step 2 — Scoring all strategies**")
        orch   = StrategyOrchestrator(rh_client=rh, alpaca_client=alpaca)
        result = orch.run(dry_run=dry_run)

        selected = result["selected"]
        st.write(f"  🏆 Selected **{len(selected)}** strategy/strategies: "
                 f"{', '.join(r['name'] for r in selected) or 'none (scores too low)'}")

        for r in result["evaluation"]:
            icon = "✅" if r["selected"] else ("⏸" if r["on_cooldown"] else "⬜")
            st.write(f"  {icon} `{r['name']:20}` score={r['score']:3d} — {r['reason'][:70]}")

        if dry_run:
            status.update(label="Evaluation complete ✓", state="complete")
        else:
            st.write("**Step 3 — Executing strategies**")
            actions = result.get("actions", [])
            trades  = [a for a in actions if a.get("action") in ("BUY","SELL")]

            if not trades:
                reasons = []
                if cash < 10:
                    reasons.append(f"insufficient cash (${cash:.2f})")
                if not selected:
                    reasons.append("no strategy scored above threshold")
                for r in selected:
                    reasons.append(f"{r['name']} found no entry signal")
                st.write(
                    "  ℹ️ No orders placed — "
                    + ("; ".join(reasons) if reasons else "all strategies returned HOLD")
                )
            else:
                st.write(f"  📋 **{len(trades)} order(s) placed:**")

            # Show each trade with live polling
            for a in trades:
                sym   = a.get("pair") or a.get("symbol", "?")
                side  = a.get("action", "?")
                qty   = a.get("quantity", 0)
                price = a.get("price", 0)
                oid   = a.get("order_id")
                strat = a.get("strategy", "")
                color = "🟢" if side == "BUY" else "🔴"
                st.write(
                    f"  {color} **{side} {qty:.6f} {sym}** @ ${price:,.4f} "
                    f"≈ ${qty * price:,.2f}  _(via {strat})_"
                )
                if oid and rh:
                    _poll_order_status(rh, oid, st, max_polls=5)

            # Save to session state
            if "trade_executions" not in st.session_state:
                st.session_state.trade_executions = []
            st.session_state.trade_executions.insert(0, {
                "time":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "run_type": "auto" if not dry_run else "eval",
                "selected": [r["name"] for r in selected],
                "trades":   len(trades),
                "actions":  trades,
                "fg_val":   fg_val,
                "fg_lbl":   fg_lbl,
                "cash":     cash,
            })
            st.session_state.trade_executions = st.session_state.trade_executions[:50]

            summary = (
                f"✅ Cycle complete — {len(selected)} strategies ran, "
                f"{len(trades)} order(s) placed"
            )
            status.update(label=summary, state="complete")

        # Push to activity log
        for entry in result["decision_log"]:
            _push_log(entry["msg"], entry["level"])

    st.session_state.orch_result     = result
    st.session_state.orch_evaluation = result["evaluation"]
    return result


def _run_manual_strategies():
    """Run only the manually toggled strategies with live status display."""
    active_strats = st.session_state.active_strategies
    if not active_strats:
        st.warning("Toggle at least one strategy ON in the sidebar.")
        return

    from strategies.momentum           import MomentumStrategy
    from strategies.mean_reversion     import MeanReversionStrategy
    from strategies.dca                import DCAStrategy
    from strategies.fear_greed         import FearGreedStrategy
    from strategies.ai_signals         import AISignalStrategy
    from strategies.rebalancer         import RebalancerStrategy
    from strategies.trending_scanner   import TrendingScannerStrategy
    from strategies.stock_momentum     import StockMomentumStrategy
    from strategies.dividend_collector import DividendCollectorStrategy
    from strategies.options_income     import OptionsIncomeStrategy
    from strategies.treasury_income    import TreasuryIncomeStrategy

    rh     = st.session_state.client if st.session_state.logged_in else None
    alpaca = st.session_state.alpaca_client

    strat_map = {
        "Momentum":       (MomentumStrategy,         rh),
        "Mean Reversion": (MeanReversionStrategy,    rh),
        "DCA":            (DCAStrategy,               rh),
        "Fear & Greed":   (FearGreedStrategy,         rh),
        "AI Signals":     (AISignalStrategy,          rh),
        "Rebalancer":     (RebalancerStrategy,        rh),
        "Trending":       (TrendingScannerStrategy,   rh),
        "Stock Momentum": (StockMomentumStrategy,     alpaca),
        "Dividend":       (DividendCollectorStrategy, alpaca),
        "Options Income": (OptionsIncomeStrategy,     alpaca),
        "Treasury":       (TreasuryIncomeStrategy,    alpaca),
    }

    total_trades = 0
    with st.status(f"Running {len(active_strats)} selected strategy/strategies...",
                   expanded=True) as status:
        for strat_name in active_strats:
            if strat_name not in strat_map:
                continue
            cls, client_inst = strat_map[strat_name]
            if not client_inst:
                st.write(f"  ⚠️ **{strat_name}** — no client connected, skipping")
                continue
            st.write(f"**Running: {strat_name}**")
            try:
                strat   = cls(client_inst)
                actions = strat.run()
                trades  = [a for a in actions if a.get("action") in ("BUY","SELL")]
                total_trades += len(trades)

                for entry in strat.log:
                    lvl_icon = {"TRADE": "💱", "WARN": "⚠️", "INFO": "ℹ️"}.get(entry["level"], "·")
                    st.write(f"  {lvl_icon} {entry['message'][:100]}")
                    _push_log(f"[{entry['strategy']}] {entry['message']}", entry["level"])

                # Poll order status for any live orders
                for a in trades:
                    oid = a.get("order_id")
                    if oid and rh:
                        _poll_order_status(rh, oid, st, max_polls=4)

                if not trades:
                    st.write(f"  ✓ {strat_name} — HOLD (no signal or insufficient cash)")
                else:
                    st.write(f"  ✓ {strat_name} — **{len(trades)} order(s) placed**")

            except Exception as e:
                st.write(f"  ❌ {strat_name} error: {e}")
                _push_log(f"[{strat_name}] ERROR: {e}", "WARN")

        status.update(
            label=f"Done — {total_trades} order(s) placed across {len(active_strats)} strategy/strategies",
            state="complete" if total_trades > 0 else "complete"
        )


if run_auto:
    if st.session_state.demo_mode:
        _push_log("[DEMO] Auto-orchestrator simulated — no real trades", "INFO")
        st.info("Demo mode — enable Live mode and connect Robinhood to place real trades.")
    else:
        _run_orchestrator(dry_run=False)
    st.rerun()

if run_eval:
    _run_orchestrator(dry_run=True)
    st.rerun()

if run_manual:
    if st.session_state.demo_mode:
        _push_log("[DEMO] Manual strategy cycle simulated", "INFO")
        st.info("Demo mode — enable Live mode and connect Robinhood to place real trades.")
    else:
        _run_manual_strategies()
    st.rerun()

# ── Strategy Scorecard ─────────────────────────────────────────────────────────
evaluation = st.session_state.orch_evaluation
if evaluation:
    st.markdown("**Strategy Scorecard** — ranked by current market fit")

    # Build a compact score table
    score_rows = []
    for r in evaluation:
        icon = "✅" if r["selected"] else ("⏸️" if r["on_cooldown"] else "⬜")
        badge_color = "#3fb950" if r["selected"] else ("#f0883e" if r["on_cooldown"] else "#4d5566")
        score_rows.append({
            "Strategy": f"{icon} {r['name'].replace('_',' ').title()}",
            "Score":    r["score"],
            "Raw":      r["raw_score"],
            "Status":   "Selected" if r["selected"] else ("Cooldown" if r["on_cooldown"] else "—"),
            "Reason":   r["reason"][:90],
        })

    df_scores = pd.DataFrame(score_rows)

    # Colour-code the Score column
    def _score_color(val):
        if val >= 70: return "color: #3fb950"
        if val >= 40: return "color: #f0883e"
        return "color: #f85149"

    st.dataframe(
        df_scores,
        column_config={
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
        },
        hide_index=True,
        use_container_width=True,
    )
else:
    # Prompt cards when no evaluation yet
    st.markdown("""
    <div style="background:#161b27;border:1px dashed #30363d;border-radius:12px;
                padding:24px;text-align:center;margin:12px 0">
      <div style="font-size:2rem">🤖</div>
      <div style="color:#e6edf3;font-weight:700;margin:8px 0">Orchestrator Ready</div>
      <div style="color:#8b949e;font-size:0.85rem">
        Click <b>Auto-Run All</b> to let the AI pick the best strategies,<br>
        or <b>Evaluate Only</b> to see the scorecard without executing.
      </div>
    </div>
    """, unsafe_allow_html=True)

# ── System Diagnostics ────────────────────────────────────────────────────────

st.markdown('<div class="section-title">System Diagnostics — Why Are / Aren\'t Trades Happening?</div>',
            unsafe_allow_html=True)

diag_cols = st.columns(5)

# 1. Robinhood connection
rh_ok    = is_live and st.session_state.client is not None
diag_cols[0].markdown(f"""
<div style="background:#161b27;border:1px solid {'#3fb950' if rh_ok else '#f85149'};
            border-radius:10px;padding:12px;text-align:center">
  <div style="font-size:1.4rem">{'✅' if rh_ok else '❌'}</div>
  <div style="color:#8b949e;font-size:0.7rem;margin-top:4px">Robinhood</div>
  <div style="color:{'#3fb950' if rh_ok else '#f85149'};font-size:0.75rem;font-weight:700">
    {'Connected' if rh_ok else 'Not Connected'}
  </div>
</div>""", unsafe_allow_html=True)

# 2. Alpaca connection
alp_ok   = st.session_state.alpaca_client is not None
diag_cols[1].markdown(f"""
<div style="background:#161b27;border:1px solid {'#3fb950' if alp_ok else '#4d5566'};
            border-radius:10px;padding:12px;text-align:center">
  <div style="font-size:1.4rem">{'✅' if alp_ok else '⚫'}</div>
  <div style="color:#8b949e;font-size:0.7rem;margin-top:4px">Alpaca</div>
  <div style="color:{'#3fb950' if alp_ok else '#4d5566'};font-size:0.75rem;font-weight:700">
    {'Connected' if alp_ok else 'Optional'}
  </div>
</div>""", unsafe_allow_html=True)

# 3. Buying power
rh_cash  = cash if is_live else 0
cash_ok  = rh_cash >= 10
diag_cols[2].markdown(f"""
<div style="background:#161b27;border:1px solid {'#3fb950' if cash_ok else '#f85149'};
            border-radius:10px;padding:12px;text-align:center">
  <div style="font-size:1.4rem">{'✅' if cash_ok else '⚠️'}</div>
  <div style="color:#8b949e;font-size:0.7rem;margin-top:4px">RH Cash</div>
  <div style="color:{'#3fb950' if cash_ok else '#f85149'};font-size:0.75rem;font-weight:700">
    ${rh_cash:,.2f}
  </div>
  {'<div style="color:#f85149;font-size:0.65rem">Fund account to trade</div>' if not cash_ok else ''}
</div>""", unsafe_allow_html=True)

# 4. Scheduler
sched_ok = st.session_state.auto_orchestrate
diag_cols[3].markdown(f"""
<div style="background:#161b27;border:1px solid {'#3fb950' if sched_ok else '#4d5566'};
            border-radius:10px;padding:12px;text-align:center">
  <div style="font-size:1.4rem">{'✅' if sched_ok else '⏸️'}</div>
  <div style="color:#8b949e;font-size:0.7rem;margin-top:4px">Auto-Scheduler</div>
  <div style="color:{'#3fb950' if sched_ok else '#8b949e'};font-size:0.75rem;font-weight:700">
    {'ON — every ' + str(st.session_state.orch_cadence_minutes) + 'min' if sched_ok else 'OFF'}
  </div>
</div>""", unsafe_allow_html=True)

# 5. Last orchestrator run
last_run = st.session_state.get("last_orch_run")
run_count = st.session_state.get("orch_run_count", 0)
diag_cols[4].markdown(f"""
<div style="background:#161b27;border:1px solid {'#58a6ff' if last_run else '#4d5566'};
            border-radius:10px;padding:12px;text-align:center">
  <div style="font-size:1.4rem">{'🤖' if last_run else '💤'}</div>
  <div style="color:#8b949e;font-size:0.7rem;margin-top:4px">Last Run</div>
  <div style="color:{'#58a6ff' if last_run else '#4d5566'};font-size:0.75rem;font-weight:700">
    {last_run.strftime('%H:%M:%S') if last_run else 'Never'}
  </div>
  <div style="color:#4d5566;font-size:0.65rem">{run_count} run(s) this session</div>
</div>""", unsafe_allow_html=True)

# Explain why no trades if relevant
if rh_ok and not cash_ok:
    st.error(
        "🚫 **No trades are executing because your Robinhood buying power is $0.**  \n"
        "To fix: open the Robinhood app → tap **Transfer** → deposit funds. "
        "Even $50–100 is enough to start. Once funds settle (instantly for debit), trades will fire automatically."
    )
elif not rh_ok and not st.session_state.demo_mode:
    st.warning("Connect your Robinhood account in the sidebar to enable live trading.")
elif rh_ok and cash_ok and not sched_ok:
    st.info("✅ Account funded and connected. Turn on **Auto-Orchestrator** in the sidebar to automate trades.")
elif rh_ok and cash_ok and sched_ok:
    st.success(
        f"✅ **Everything is live.** Orchestrator runs every {st.session_state.orch_cadence_minutes} min. "
        f"Runs so far this session: {run_count}."
    )

# ── Live Trade Execution Feed ──────────────────────────────────────────────────

executions = st.session_state.get("trade_executions", [])
if executions:
    st.markdown('<div class="section-title">Live Trade Execution Feed</div>', unsafe_allow_html=True)
    for ex in executions[:10]:
        trade_count = ex.get("trades", 0)
        border_c = "#3fb950" if trade_count > 0 else "#4d5566"
        strategies_str = ", ".join(ex.get("selected", [])) or "none selected"
        st.markdown(f"""
        <div style="background:#161b27;border:1px solid {border_c};border-radius:10px;
                    padding:14px 18px;margin:6px 0">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
              <span style="color:#8b949e;font-size:0.7rem">{ex['time']}</span>
              <span style="color:#58a6ff;font-size:0.75rem;margin-left:10px;
                           background:#1a2a3a;border-radius:4px;padding:2px 6px">
                F&G {ex.get('fg_val','?')} · {ex.get('fg_lbl','?')}
              </span>
            </div>
            <span style="color:{'#3fb950' if trade_count > 0 else '#8b949e'};
                          font-weight:700;font-size:0.85rem">
              {trade_count} order(s) placed
            </span>
          </div>
          <div style="color:#e6edf3;font-size:0.8rem;margin-top:6px">
            <b>Strategies:</b> {strategies_str}
          </div>
          <div style="color:#8b949e;font-size:0.75rem;margin-top:2px">
            Cash at run time: ${ex.get('cash', 0):,.2f}
          </div>
          {''.join(
            f"""<div style="color:{'#3fb950' if a['action']=='BUY' else '#f85149'};
                            font-family:monospace;font-size:0.78rem;margin-top:4px">
                  {"▲ BUY" if a["action"]=="BUY" else "▼ SELL"}
                  {a.get("quantity",0):.6f} {a.get("pair") or a.get("symbol","?")}
                  @ ${a.get("price",0):,.4f}
                  ≈ ${a.get("quantity",0) * a.get("price",0):,.2f}
                </div>"""
            for a in ex.get("actions", [])
          )}
        </div>
        """, unsafe_allow_html=True)

# ── Activity Log + Orders ─────────────────────────────────────────────────────

log_col, orders_col = st.columns(2)

with log_col:
    st.markdown('<div class="section-title">Activity Log</div>', unsafe_allow_html=True)
    log_entries = st.session_state.strategy_log[:20]
    if log_entries:
        for entry in log_entries:
            level_color = {
                "TRADE": "#3fb950", "WARN": "#f0883e", "INFO": "#58a6ff"
            }.get(entry.get("level", "INFO"), "#8b949e")
            st.markdown(f"""
            <div style="background:#161b27;border-radius:6px;padding:8px 12px;margin:3px 0;
                        font-family:monospace;font-size:0.78rem;border-left:3px solid {level_color}">
              <span style="color:#4d5566">{entry['time']}</span>
              <span style="color:#c9d1d9;margin-left:8px">{entry['msg']}</span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.caption("No activity yet — run a strategy cycle.")

with orders_col:
    st.markdown('<div class="section-title">Recent Orders</div>', unsafe_allow_html=True)

    display_orders = orders if orders else [
        {"symbol": "BTC-USD", "side": "buy", "state": "filled",
         "filled_qty": 0.008, "avg_price": 67200, "created_at": "2026-03-24T14:32:00Z"},
        {"symbol": "ETH-USD", "side": "buy", "state": "filled",
         "filled_qty": 0.5, "avg_price": 3540, "created_at": "2026-03-23T09:15:00Z"},
        {"symbol": "SOL-USD", "side": "sell", "state": "filled",
         "filled_qty": 5.0, "avg_price": 168, "created_at": "2026-03-22T16:44:00Z"},
        {"symbol": "DOGE-USD", "side": "buy", "state": "filled",
         "filled_qty": 2000, "avg_price": 0.128, "created_at": "2026-03-21T11:03:00Z"},
    ]

    for o in display_orders[:10]:
        side = o.get("side", "")
        state = o.get("state", "")
        symbol = o.get("symbol", "")
        qty = o.get("filled_qty", 0)
        price = o.get("avg_price", 0)
        ts = o.get("created_at", "")[:10]
        side_color = "#3fb950" if side == "buy" else "#f85149"
        state_color = "#3fb950" if state == "filled" else "#f0883e"
        st.markdown(f"""
        <div style="background:#161b27;border-radius:6px;padding:8px 12px;margin:3px 0;
                    display:flex;justify-content:space-between;align-items:center;
                    border-left:3px solid {side_color}">
          <div>
            <span style="color:{side_color};font-weight:700;text-transform:uppercase;font-size:0.75rem">
              {side}
            </span>
            <span style="color:#e6edf3;margin-left:8px;font-family:monospace">{symbol}</span>
          </div>
          <div style="text-align:right">
            <div style="color:#8b949e;font-size:0.78rem">{qty:.4f} @ ${price:,.2f}</div>
            <div style="color:{state_color};font-size:0.7rem">{state} · {ts}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

# ── Trade P&L History chart ────────────────────────────────────────────────────

st.markdown('<div class="section-title">Trade P&L History</div>', unsafe_allow_html=True)

trades = stats.get("trades_pnl", [])
if trades:
    df_trades = pd.DataFrame(trades)
    df_trades["date"] = pd.to_datetime(df_trades["date"])
    df_trades["color"] = df_trades["pnl"].apply(lambda x: "#3fb950" if x >= 0 else "#f85149")
    df_trades["cumulative"] = df_trades["pnl"].cumsum()

    t1, t2 = st.columns([1, 1])
    with t1:
        fig_bar = go.Figure(go.Bar(
            x=df_trades["date"], y=df_trades["pnl"],
            marker_color=df_trades["color"],
            hovertemplate="<b>%{x|%b %d}</b><br>P&L: $%{y:,.2f}<extra></extra>",
        ))
        fig_bar.update_layout(
            paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
            font=dict(color="#8b949e", size=11),
            xaxis=dict(gridcolor="#21262d"),
            yaxis=dict(gridcolor="#21262d", tickformat="$,.0f"),
            margin=dict(l=0, r=0, t=10, b=0),
            height=200,
            title=dict(text="Per-Trade P&L", font=dict(color="#8b949e", size=12)),
        )
        st.plotly_chart(fig_bar)

    with t2:
        fig_cum = go.Figure(go.Scatter(
            x=df_trades["date"], y=df_trades["cumulative"],
            mode="lines+markers",
            line=dict(color="#58a6ff", width=2),
            fill="tozeroy", fillcolor="rgba(88,166,255,0.1)",
            hovertemplate="<b>%{x|%b %d}</b><br>Cumulative: $%{y:,.2f}<extra></extra>",
        ))
        fig_cum.update_layout(
            paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
            font=dict(color="#8b949e", size=11),
            xaxis=dict(gridcolor="#21262d"),
            yaxis=dict(gridcolor="#21262d", tickformat="$,.0f"),
            margin=dict(l=0, r=0, t=10, b=0),
            height=200,
            title=dict(text="Cumulative P&L", font=dict(color="#8b949e", size=12)),
        )
        st.plotly_chart(fig_cum)

# ── Income Streams ────────────────────────────────────────────────────────────

st.markdown('<div class="section-title">Income Streams — Beyond Trading</div>', unsafe_allow_html=True)
st.caption("Diversify capital across multiple engines. Compound everything toward $100K.")

from strategies.income_streams import get_streams, update_stream, log_income, estimate_monthly_income

streams = get_streams()
CATEGORY_ORDER = ["Trading", "Staking", "DeFi", "Fixed Income", "Dividends", "Options", "Digital"]
CATEGORY_ICONS = {
    "Trading": "📈", "Staking": "🔒", "DeFi": "🌾",
    "Fixed Income": "🏦", "Dividends": "💰", "Options": "📋", "Digital": "💻",
}

# Capital allocator expander
with st.expander("Configure Capital Allocation & Activate Streams", expanded=False):
    total_capital = stats["current_equity"]
    st.markdown(f"**Total Capital Available:** ${total_capital:,.2f}")
    st.markdown("Set how much to allocate to each stream. Allocations are advisory — execute externally.")
    st.markdown("---")
    alloc_inputs = {}
    cols_per_row = 2
    stream_chunks = [streams[i:i+cols_per_row] for i in range(0, len(streams), cols_per_row)]
    for chunk in stream_chunks:
        row_cols = st.columns(cols_per_row)
        for col, s in zip(row_cols, chunk):
            with col:
                alloc_inputs[s["id"]] = st.number_input(
                    f"{s['icon']} {s['name']} ($)",
                    min_value=0.0,
                    value=float(s.get("capital_allocated", 0)),
                    step=100.0,
                    key=f"alloc_{s['id']}",
                )
    if st.button("Save Allocation", type="primary"):
        for sid, amt in alloc_inputs.items():
            update_stream(sid, capital_allocated=amt)
        st.success("Allocation saved!")
        st.rerun()

# Income stream income logger
with st.expander("Log Income Received", expanded=False):
    log_cols = st.columns([2, 1, 2, 1])
    stream_names = {s["id"]: f"{s['icon']} {s['name']}" for s in streams}
    sel_stream = log_cols[0].selectbox("Stream", options=list(stream_names.keys()),
                                        format_func=lambda x: stream_names[x])
    log_amount = log_cols[1].number_input("Amount ($)", min_value=0.01, value=50.0)
    log_note = log_cols[2].text_input("Note", placeholder="Staking reward, dividend payment...")
    if log_cols[3].button("Log", type="primary"):
        log_income(sel_stream, log_amount, log_note)
        st.success(f"Logged ${log_amount:.2f} from {stream_names[sel_stream]}")
        st.rerun()

# Stream cards
alloc_map = {s["id"]: s.get("capital_allocated", 0) for s in streams}
estimates = estimate_monthly_income(alloc_map)
total_monthly_est = estimates.get("_total_monthly", 0)
total_annual_est = estimates.get("_total_annual", 0)

# Summary bar
st.markdown(f"""
<div style="background:#161b27;border:1px solid #21262d;border-radius:10px;
            padding:16px 24px;margin:12px 0;display:flex;justify-content:space-between;align-items:center">
  <div>
    <span style="color:#8b949e;font-size:0.75rem;text-transform:uppercase;letter-spacing:1px">
      All Streams — Est. Monthly Income
    </span>
    <div style="color:#3fb950;font-size:1.8rem;font-weight:800;font-family:monospace">
      ${total_monthly_est:,.0f}/mo
    </div>
  </div>
  <div style="text-align:right">
    <span style="color:#8b949e;font-size:0.75rem;text-transform:uppercase;letter-spacing:1px">Annual Run Rate</span>
    <div style="color:{'#3fb950' if total_annual_est >= 100000 else '#f0883e'};font-size:1.8rem;font-weight:800;font-family:monospace">
      ${total_annual_est:,.0f}/yr
    </div>
    <div style="color:#4d5566;font-size:0.75rem">goal: $100,000</div>
  </div>
</div>
""", unsafe_allow_html=True)

# Stream cards grid
s_cols = st.columns(4)
for i, stream in enumerate(streams):
    col = s_cols[i % 4]
    est = estimates.get(stream["id"], {})
    monthly = est.get("monthly_estimate", 0)
    apy = stream.get("current_apy")
    capital = stream.get("capital_allocated", 0)
    active = stream.get("active", False)
    color = stream.get("color", "#58a6ff")
    border_color = color if active else "#21262d"

    with col:
        st.markdown(f"""
        <div style="background:#161b27;border:1px solid {border_color};border-radius:10px;
                    padding:14px;margin-bottom:12px;min-height:130px">
          <div style="display:flex;justify-content:space-between;margin-bottom:6px">
            <span style="font-size:1.2rem">{stream['icon']}</span>
            <span style="color:{'#3fb950' if active else '#4d5566'};font-size:0.65rem;
                          background:{'#1f3a1f' if active else '#1a1a1a'};
                          border-radius:4px;padding:2px 6px;font-weight:700">
              {'● ACTIVE' if active else '○ IDLE'}
            </span>
          </div>
          <div style="color:#e6edf3;font-weight:700;font-size:0.85rem;margin-bottom:2px">
            {stream['name']}
          </div>
          <div style="color:#8b949e;font-size:0.7rem;margin-bottom:8px;line-height:1.3">
            {stream['description'][:60]}{'...' if len(stream['description']) > 60 else ''}
          </div>
          <div style="display:flex;justify-content:space-between">
            <div>
              <div style="color:#4d5566;font-size:0.65rem">APY</div>
              <div style="color:{color};font-weight:700;font-size:0.85rem">
                {f'{apy:.1f}%' if apy else '—'}
              </div>
            </div>
            <div style="text-align:center">
              <div style="color:#4d5566;font-size:0.65rem">Capital</div>
              <div style="color:#8b949e;font-size:0.85rem">${capital:,.0f}</div>
            </div>
            <div style="text-align:right">
              <div style="color:#4d5566;font-size:0.65rem">Est/Mo</div>
              <div style="color:#3fb950;font-weight:700;font-size:0.85rem">
                ${monthly:,.0f}
              </div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        # Activate toggle
        btn_label = "Deactivate" if active else "Activate"
        if st.button(btn_label, key=f"activate_{stream['id']}", width='stretch',
                     type="secondary" if active else "primary"):
            update_stream(stream["id"], active=not active)
            st.rerun()

# Income breakdown chart
active_streams = [s for s in streams if s.get("active") and s.get("capital_allocated", 0) > 0]
if active_streams:
    st.markdown('<div class="section-title">Active Income Breakdown</div>', unsafe_allow_html=True)
    names = [s["name"] for s in active_streams]
    monthlies = [estimates.get(s["id"], {}).get("monthly_estimate", 0) for s in active_streams]
    colors_list = [s.get("color", "#58a6ff") for s in active_streams]
    fig_income = go.Figure(go.Bar(
        x=names, y=monthlies,
        marker_color=colors_list,
        text=[f"${v:,.0f}/mo" for v in monthlies],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>$%{y:,.0f}/month<extra></extra>",
    ))
    fig_income.update_layout(
        paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        font=dict(color="#8b949e", size=11),
        xaxis=dict(gridcolor="#21262d"),
        yaxis=dict(gridcolor="#21262d", tickformat="$,.0f", title="Monthly Income"),
        margin=dict(l=0, r=0, t=20, b=0),
        height=220,
        showlegend=False,
    )
    st.plotly_chart(fig_income)


# ── Live Trade Monitor ────────────────────────────────────────────────────────

st.markdown('<div class="section-title">Live Trade Monitor</div>', unsafe_allow_html=True)

def _render_order_status(symbol, side, state, qty, price, ts, platform, order_id=""):
    side_color  = "#3fb950" if side in ("buy","BUY")  else "#f85149"
    state_color = {"filled": "#3fb950", "partially_filled": "#f0883e",
                   "pending_new": "#58a6ff", "new": "#58a6ff",
                   "accepted": "#58a6ff", "canceled": "#4d5566",
                   "rejected": "#f85149"}.get(state, "#8b949e")
    state_icon  = {"filled": "✅", "partially_filled": "⏳", "pending_new": "🔄",
                   "new": "🔄", "accepted": "🔄", "canceled": "❌",
                   "rejected": "❌"}.get(state, "⏳")
    plat_color  = "#8566ff" if platform == "Robinhood" else "#f0883e"
    st.markdown(f"""
    <div style="background:#161b27;border:1px solid #21262d;border-radius:8px;
                padding:12px 16px;margin:4px 0;
                border-left:3px solid {side_color}">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div style="display:flex;align-items:center;gap:12px">
          <span style="font-size:1.1rem">{state_icon}</span>
          <div>
            <span style="color:{side_color};font-weight:700;font-size:0.8rem;
                          text-transform:uppercase">{side}</span>
            <span style="color:#e6edf3;font-weight:700;margin-left:8px;
                          font-family:monospace">{symbol}</span>
            <span style="color:{plat_color};font-size:0.65rem;margin-left:8px;
                          background:#1a1f2e;border-radius:4px;padding:2px 6px">{platform}</span>
          </div>
        </div>
        <div style="text-align:right">
          <div style="color:{state_color};font-weight:700;font-size:0.85rem">{state.replace('_',' ').upper()}</div>
          <div style="color:#8b949e;font-size:0.75rem">
            {f'{qty:.6f}' if qty < 1 else f'{qty:,.4f}'} @ ${f'{price:.6f}' if price < 1 else f'{price:,.2f}'}
          </div>
          <div style="color:#4d5566;font-size:0.7rem">{str(ts)[:16].replace('T',' ')}</div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

lm_rh_col, lm_alp_col = st.columns(2)

with lm_rh_col:
    st.caption("**Robinhood — Crypto Orders**")
    if is_live and st.session_state.client:
        try:
            rh_orders = st.session_state.client.get_orders(10)
            if rh_orders:
                for o in rh_orders[:6]:
                    _render_order_status(
                        symbol   = o.get("symbol",""),
                        side     = o.get("side",""),
                        state    = o.get("state",""),
                        qty      = float(o.get("filled_qty") or 0),
                        price    = float(o.get("avg_price") or 0),
                        ts       = o.get("created_at",""),
                        platform = "Robinhood",
                        order_id = o.get("id",""),
                    )
            else:
                st.caption("No recent orders.")
        except Exception as e:
            st.caption(f"Could not load orders: {e}")
    else:
        # Demo orders
        demo_orders = [
            ("BTC-USD","buy","filled",0.00119,84200,"2026-03-26T14:32:00Z"),
            ("ETH-USD","buy","filled",0.05556,1800,"2026-03-26T13:15:00Z"),
            ("SOL-USD","sell","filled",0.76923,130,"2026-03-26T11:44:00Z"),
        ]
        for sym,side,state,qty,price,ts in demo_orders:
            _render_order_status(sym,side,state,qty,price,ts,"Robinhood")

with lm_alp_col:
    st.caption("**Alpaca — Stocks & ETFs Orders**")
    if st.session_state.alpaca_client:
        try:
            alp_orders = st.session_state.alpaca_client.get_orders(10)
            if alp_orders:
                for o in alp_orders[:6]:
                    _render_order_status(
                        symbol   = o.get("symbol",""),
                        side     = o.get("side",""),
                        state    = o.get("status",""),
                        qty      = float(o.get("filled_qty") or 0),
                        price    = float(o.get("avg_price") or 0),
                        ts       = o.get("created_at",""),
                        platform = "Alpaca",
                        order_id = o.get("id",""),
                    )
            else:
                st.caption("No recent orders.")
        except Exception as e:
            st.caption(f"Could not load Alpaca orders: {e}")
    else:
        # Demo
        demo_alp = [
            ("JEPI","buy","filled",2.1,55.40,"2026-03-26T14:01:00Z"),
            ("SGOV","buy","filled",10.0,100.12,"2026-03-26T09:32:00Z"),
        ]
        for sym,side,state,qty,price,ts in demo_alp:
            _render_order_status(sym,side,state,qty,price,ts,"Alpaca")

# Refresh button
if st.button("🔄 Refresh Orders", key="refresh_orders"):
    st.rerun()

# ── Cross-Platform Rebalancer ─────────────────────────────────────────────────

st.markdown('<div class="section-title">Cross-Platform Rebalancer — Robinhood + Alpaca</div>',
            unsafe_allow_html=True)
st.caption(
    "Treats your Robinhood crypto and Alpaca stocks as **one unified portfolio**. "
    "Set target allocations and let the rebalancer keep everything on track automatically."
)

from strategies.cross_platform_rebalancer import (
    CrossPlatformRebalancer, load_targets, save_targets,
    DEFAULT_TARGETS, CRYPTO_PAIRS, DRIFT_THRESHOLD
)

rh_client_xp    = st.session_state.client if st.session_state.logged_in else None
alpaca_client_xp = st.session_state.alpaca_client

xp_rebal = CrossPlatformRebalancer(rh_client_xp, alpaca_client_xp)
current_targets = load_targets()

# ── Unified snapshot ──────────────────────────────────────────────────────────
xp_snap = None
if rh_client_xp or alpaca_client_xp:
    with st.spinner("Loading unified portfolio snapshot..."):
        try:
            xp_snap = xp_rebal.get_unified_snapshot()
        except Exception as e:
            st.warning(f"Snapshot error: {e}")

if xp_snap:
    total_combined = xp_snap["total_equity"]

    # Summary KPIs
    xp_c1, xp_c2, xp_c3, xp_c4 = st.columns(4)
    xp_c1.metric("Total Combined", f"${total_combined:,.2f}")
    xp_c2.metric("Robinhood",      f"${xp_snap['rh_equity']:,.2f}",
                  f"{xp_snap['rh_equity']/total_combined*100:.1f}% of total" if total_combined else "")
    xp_c3.metric("Alpaca",         f"${xp_snap['alpaca_equity']:,.2f}",
                  f"{xp_snap['alpaca_equity']/total_combined*100:.1f}% of total" if total_combined else "")
    xp_c4.metric("Max Drift",
                  f"{xp_snap['max_drift']:.1f}%",
                  "⚠️ Rebalance needed" if xp_snap["needs_rebalance"] else "✅ Balanced",
                  delta_color="inverse")

    # ── Drift table ───────────────────────────────────────────────────────────
    if xp_snap["drift"]:
        st.markdown("**Allocation Drift**")
        drift_cols = st.columns([1.2, 1, 1, 1, 1, 1.2, 1])
        for lbl in ["Asset","Platform","Target %","Current %","Drift","Gap ($)","Action"]:
            drift_cols[["Asset","Platform","Target %","Current %","Drift","Gap ($)","Action"].index(lbl)].markdown(
                f"<span style='color:#8b949e;font-size:0.7rem;text-transform:uppercase'>{lbl}</span>",
                unsafe_allow_html=True)
        st.markdown("<hr style='border-color:#21262d;margin:4px 0 8px 0'>", unsafe_allow_html=True)

        for d in xp_snap["drift"]:
            drift_cols = st.columns([1.2, 1, 1, 1, 1, 1.2, 1])
            drift_c = "#f85149" if d["drift_pct"] > DRIFT_THRESHOLD else (
                      "#3fb950" if d["drift_pct"] < -DRIFT_THRESHOLD else "#8b949e")
            act_c   = "#f85149" if d["action"]=="SELL" else "#3fb950"
            plat_c  = "#8566ff" if d["platform"]=="Robinhood" else "#f0883e"
            flag    = " ⚠️" if d["needs_action"] else ""

            drift_cols[0].markdown(f"<span style='color:#e6edf3;font-weight:600;font-family:monospace'>{d['symbol']}</span>", unsafe_allow_html=True)
            drift_cols[1].markdown(f"<span style='color:{plat_c};font-size:0.75rem'>{d['platform']}</span>", unsafe_allow_html=True)
            drift_cols[2].markdown(f"<span style='color:#8b949e'>{d['target_pct']:.1f}%</span>", unsafe_allow_html=True)
            drift_cols[3].markdown(f"<span style='color:#e6edf3'>{d['current_pct']:.1f}%</span>", unsafe_allow_html=True)
            drift_cols[4].markdown(f"<span style='color:{drift_c};font-weight:700'>{d['drift_pct']:+.1f}%{flag}</span>", unsafe_allow_html=True)
            drift_cols[5].markdown(f"<span style='color:#e6edf3'>${abs(d['gap_usd']):,.0f}</span>", unsafe_allow_html=True)
            drift_cols[6].markdown(f"<span style='color:{act_c};font-weight:700'>{d['action'] if d['needs_action'] else '—'}</span>", unsafe_allow_html=True)

    # ── Rebalance controls ────────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    xp_btn1, xp_btn2 = st.columns(2)

    if xp_snap["needs_rebalance"]:
        if xp_btn1.button("⚖️ Execute Rebalance Now", type="primary", key="xp_rebal_run"):
            if st.session_state.demo_mode:
                st.success("[DEMO] Cross-platform rebalance simulated.")
            else:
                with st.spinner("Rebalancing across Robinhood + Alpaca..."):
                    try:
                        rebal_actions = xp_rebal.run()
                        for entry in xp_rebal.log:
                            st.session_state.strategy_log.insert(0, {
                                "time":  entry["time"],
                                "msg":   f"[XP-REBAL] {entry['message']}",
                                "level": entry["level"],
                            })
                        buys  = [a for a in rebal_actions if a.get("action")=="BUY"]
                        sells = [a for a in rebal_actions if a.get("action")=="SELL"]
                        needs = [a for a in rebal_actions if a.get("action")=="NEEDS_CASH"]
                        st.success(f"Rebalance complete — {len(sells)} sell(s), {len(buys)} buy(s)")
                        if needs:
                            for n in needs:
                                st.warning(f"⚠️ {n['symbol']} ({n['platform']}): {n['reason']}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Rebalance error: {e}")
    else:
        xp_btn1.success("✅ Portfolio is balanced — no action needed")

    if xp_btn2.button("🔄 Refresh Snapshot", key="xp_refresh"):
        st.rerun()

    # ── Transfer guidance ─────────────────────────────────────────────────────
    needs_cash = [d for d in xp_snap["drift"]
                  if d["action"]=="BUY" and d["needs_action"]
                  and d["current_val"]==0 and d["gap_usd"] > 50]
    if needs_cash:
        with st.expander("💱 Cash Transfer Guidance", expanded=True):
            st.markdown(
                "Since Robinhood and Alpaca are separate platforms, cash cannot move automatically. "
                "Here's what to do:"
            )
            for item in needs_cash:
                plat = item["platform"]
                st.markdown(
                    f"- **{item['symbol']}** needs **${item['gap_usd']:,.0f}** on **{plat}** — "
                    f"{'deposit via Robinhood → Transfers' if plat=='Robinhood' else 'deposit via Alpaca → Banking'}"
                )

else:
    st.info("Connect Robinhood and/or Alpaca to see your unified portfolio.")

# ── Target Allocation Editor ──────────────────────────────────────────────────
with st.expander("⚙️ Edit Target Allocations", expanded=False):
    st.caption(
        f"Set target % for each asset. Total should sum to ~85% (remaining ~15% stays as cash buffer). "
        f"Drift threshold: **{DRIFT_THRESHOLD:.0f}%** — rebalance fires when any asset drifts beyond this."
    )
    edited_targets = {}
    t_cols = st.columns(3)
    for i, (sym, pct) in enumerate(current_targets.items()):
        col = t_cols[i % 3]
        plat = "🟣 RH" if sym in CRYPTO_PAIRS else "🟠 ALP"
        edited_targets[sym] = col.number_input(
            f"{plat} {sym}", min_value=0.0, max_value=50.0,
            value=float(pct), step=0.5, key=f"target_{sym}"
        )
    total_targeted = sum(edited_targets.values())
    color = "#3fb950" if total_targeted <= 90 else "#f0883e"
    st.markdown(
        f"<span style='color:{color}'>Total allocated: **{total_targeted:.1f}%** "
        f"(cash buffer: ~{100-total_targeted:.1f}%)</span>",
        unsafe_allow_html=True
    )
    if st.button("💾 Save Targets", type="primary", key="save_xp_targets"):
        save_targets(edited_targets)
        st.success("Targets saved! Next rebalance cycle will use these.")
        st.rerun()

# ── Alpaca Portfolio Summary ──────────────────────────────────────────────────

if st.session_state.alpaca_client:
    st.markdown('<div class="section-title">Alpaca Portfolio — Stocks & ETFs</div>',
                unsafe_allow_html=True)
    alpaca = st.session_state.alpaca_client
    alpaca_col1, alpaca_col2, alpaca_col3 = st.columns(3)

    try:
        a_positions = alpaca.get_positions()
        a_cash      = alpaca.get_cash()
        a_portfolio = alpaca.get_portfolio_value()
        a_bp        = alpaca.get_buying_power()
        a_open      = alpaca.is_market_open()

        alpaca_col1.metric("Portfolio Value", f"${a_portfolio:,.2f}")
        alpaca_col2.metric("Cash",           f"${a_cash:,.2f}",
                           f"BP: ${a_bp:,.2f}")
        alpaca_col3.metric("Market",         "OPEN" if a_open else "CLOSED",
                           f"{len(a_positions)} positions")

        if a_positions:
            st.markdown("**Positions**")
            a_cols = st.columns([1.5, 1, 1, 1, 1.2])
            for lbl in ["Symbol", "Qty", "Price", "Value", "P&L"]:
                a_cols[["Symbol","Qty","Price","Value","P&L"].index(lbl)].markdown(
                    f"<span style='color:#8b949e;font-size:0.75rem;text-transform:uppercase'>{lbl}</span>",
                    unsafe_allow_html=True)
            st.markdown("<hr style='border-color:#21262d;margin:4px 0'>", unsafe_allow_html=True)
            for p in a_positions:
                a_cols = st.columns([1.5, 1, 1, 1, 1.2])
                pnl_c = "#3fb950" if p["unrealized_pnl"] >= 0 else "#f85149"
                a_cols[0].markdown(f"<span style='color:#58a6ff;font-weight:700'>{p['symbol']}</span>", unsafe_allow_html=True)
                a_cols[1].markdown(f"<span style='color:#e6edf3'>{p['qty']:.4f}</span>", unsafe_allow_html=True)
                a_cols[2].markdown(f"<span style='color:#e6edf3'>${p['current_price']:,.2f}</span>", unsafe_allow_html=True)
                a_cols[3].markdown(f"<span style='color:#e6edf3'>${p['market_value']:,.2f}</span>", unsafe_allow_html=True)
                a_cols[4].markdown(
                    f"<span style='color:{pnl_c}'>{fmt_usd(p['unrealized_pnl'])} ({fmt_pct(p['pnl_pct'])})</span>",
                    unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"Alpaca data error: {e}")

# ── Opportunity Map ────────────────────────────────────────────────────────────

st.markdown('<div class="section-title">Revenue Opportunity Map</div>', unsafe_allow_html=True)
st.caption("All active and available income engines across every asset class.")

opp_data = [
    # (name, category, description, annual_yield_est, risk, status)
    ("Crypto Momentum",    "Crypto",       "Trend-following breakouts on BTC/ETH/SOL",           "15-40%",  "HIGH",  "RH"),
    ("Crypto DCA",         "Crypto",       "Systematic accumulation into BTC/ETH",                "12-25%",  "MED",   "RH"),
    ("Fear & Greed",       "Crypto",       "Contrarian buy extreme fear, sell extreme greed",     "20-50%",  "HIGH",  "RH"),
    ("AI Signals",         "Crypto",       "Claude analyses portfolio + macro for signals",       "varies",  "MED",   "RH"),
    ("Trending Scanner",   "Crypto",       "Social momentum via CoinGecko top-7 trending",        "15-60%",  "HIGH",  "RH"),
    ("Portfolio Rebalance","Crypto",       "Drift-based rebalancing to target allocation",        "2-8%",    "LOW",   "RH"),
    ("Stock Momentum",     "Equities",     "Swing-trade momentum stocks + ETFs (NVDA/QQQ/SPY)",  "15-40%",  "MED",   "ALP"),
    ("Dividend ETFs",      "Dividends",    "JEPI/JEPQ/SCHD/QYLD — monthly passive income",       "7-11%",   "LOW",   "ALP"),
    ("Treasury T-Bills",   "Fixed Income", "SGOV/BIL — risk-free cash yield on idle capital",    "4.8-5.2%","ZERO",  "ALP"),
    ("Corp Bonds",         "Fixed Income", "LQD/HYG — investment grade + high yield bonds",      "5-7%",    "LOW",   "ALP"),
    ("Covered Calls",      "Options",      "Sell calls against held stocks for premium income",   "12-24%",  "MED",   "ALP"),
    ("Cash-Secured Puts",  "Options",      "Sell puts on quality stocks, earn premium",           "12-36%",  "MED",   "ALP"),
    ("ETH Staking",        "Staking",      "Ethereum proof-of-stake rewards via Lido/Coinbase",   "3.5-5%",  "LOW",   "EXT"),
    ("SOL Staking",        "Staking",      "Solana validator staking rewards",                    "6-8%",    "MED",   "EXT"),
    ("DeFi Lending",       "DeFi",         "AAVE/Compound — lend stablecoins for yield",          "8-15%",   "MED",   "EXT"),
    ("REITs",              "Real Estate",  "VNQ/O/MAIN — real estate income via Alpaca",          "5-8%",    "MED",   "ALP"),
]

risk_colors = {"ZERO": "#58a6ff", "LOW": "#3fb950", "MED": "#f0883e", "HIGH": "#f85149"}
source_colors = {"RH": "#8566ff", "ALP": "#f0883e", "EXT": "#4d5566"}

# Group by category
from itertools import groupby
opp_by_cat = {}
for row in opp_data:
    cat = row[1]
    opp_by_cat.setdefault(cat, []).append(row)

cat_icons = {
    "Crypto": "🪙", "Equities": "📈", "Dividends": "💰",
    "Fixed Income": "🏦", "Options": "📋", "Staking": "🔒",
    "DeFi": "🌾", "Real Estate": "🏠",
}

for cat, rows in opp_by_cat.items():
    icon = cat_icons.get(cat, "📌")
    with st.expander(f"{icon} {cat} ({len(rows)} strategies)", expanded=(cat in ["Crypto","Dividends","Fixed Income"])):
        o_cols = st.columns([2, 2.5, 1, 1, 1])
        for lbl in ["Strategy", "Description", "Est. Yield", "Risk", "Platform"]:
            o_cols[["Strategy","Description","Est. Yield","Risk","Platform"].index(lbl)].markdown(
                f"<span style='color:#8b949e;font-size:0.7rem;text-transform:uppercase'>{lbl}</span>",
                unsafe_allow_html=True)
        st.markdown("<hr style='border-color:#21262d;margin:2px 0 8px 0'>", unsafe_allow_html=True)
        for (name, _, desc, yield_est, risk, src) in rows:
            o_cols = st.columns([2, 2.5, 1, 1, 1])
            rc = risk_colors.get(risk, "#8b949e")
            sc = source_colors.get(src, "#4d5566")
            o_cols[0].markdown(f"<span style='color:#e6edf3;font-weight:600;font-size:0.85rem'>{name}</span>", unsafe_allow_html=True)
            o_cols[1].markdown(f"<span style='color:#8b949e;font-size:0.8rem'>{desc}</span>", unsafe_allow_html=True)
            o_cols[2].markdown(f"<span style='color:#3fb950;font-size:0.8rem;font-weight:700'>{yield_est}</span>", unsafe_allow_html=True)
            o_cols[3].markdown(f"<span style='color:{rc};font-size:0.75rem;font-weight:700'>{risk}</span>", unsafe_allow_html=True)
            o_cols[4].markdown(f"<span style='color:{sc};font-size:0.75rem;background:#1a1f2e;border-radius:4px;padding:2px 6px'>{src}</span>", unsafe_allow_html=True)

# ── Footer ─────────────────────────────────────────────────────────────────────

st.markdown("---")
last = datetime.now().strftime("%H:%M:%S")
rh_status   = "🟢 RH Live"   if is_live else "🔴 RH Demo"
alp_status  = "🟢 Alpaca"    if st.session_state.alpaca_client else "⚫ No Alpaca"
st.markdown(f"""
<div style="display:flex;justify-content:space-between;color:#4d5566;font-size:0.75rem">
  <span>$100K Digital Wealth Platform · RH Crypto + Alpaca Stocks + 16 Income Streams</span>
  <span>Updated: {last} &nbsp; {rh_status} &nbsp; {alp_status}</span>
</div>
""", unsafe_allow_html=True)

# ── Scheduler engine ───────────────────────────────────────────────────────────

def _should_run_orchestrator() -> bool:
    """Return True if the cadence has elapsed since the last auto-run."""
    if not st.session_state.auto_orchestrate:
        return False
    last = st.session_state.last_orch_run
    if last is None:
        return True   # Never run — fire immediately
    elapsed_mins = (datetime.now() - last).total_seconds() / 60
    return elapsed_mins >= st.session_state.orch_cadence_minutes

if _should_run_orchestrator():
    if not st.session_state.demo_mode:
        with st.spinner(
            f"⚙️ Auto-Orchestrator firing "
            f"(run #{st.session_state.orch_run_count + 1})..."
        ):
            from core.strategy_orchestrator import StrategyOrchestrator
            rh     = st.session_state.client if st.session_state.logged_in else None
            alpaca = st.session_state.alpaca_client
            if rh or alpaca:
                orch   = StrategyOrchestrator(rh_client=rh, alpaca_client=alpaca)
                result = orch.run(dry_run=False)
                for entry in result["decision_log"]:
                    st.session_state.strategy_log.insert(0, {
                        "time":  entry["time"],
                        "msg":   f"[AUTO] {entry['msg']}",
                        "level": entry["level"],
                    })
                st.session_state.orch_result     = result
                st.session_state.orch_evaluation = result["evaluation"]
    # Always update the timer (even in demo mode, so countdown works)
    st.session_state.last_orch_run  = datetime.now()
    st.session_state.orch_run_count += 1

# ── Page auto-refresh (keeps scheduler alive) ──────────────────────────────────

if st.session_state.auto_refresh:
    time.sleep(30)
    st.rerun()
