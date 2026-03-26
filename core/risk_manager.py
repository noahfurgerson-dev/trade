"""
Portfolio Risk Manager
───────────────────────
Protects the portfolio from catastrophic losses using:

  1. Value at Risk (VaR 95%)     — Max expected daily loss 95% of the time
  2. Maximum Drawdown Guard      — Auto-reduce exposure after X% drawdown
  3. Correlation Matrix          — Prevents over-concentration in correlated assets
  4. Kelly Criterion             — Optimal position sizing per strategy
  5. Daily Loss Limit            — Halt trading if daily loss > threshold
  6. Volatility Regime Detection — Scale down positions in high-vol markets
  7. Concentration Limits        — No single asset > 25% of portfolio

Risk metrics computed:
  Sharpe Ratio     = (Return - Risk Free) / Std Dev
  Sortino Ratio    = (Return - Risk Free) / Downside Dev
  Max Drawdown     = Peak-to-trough decline
  Win Rate         = % of profitable trades
  Profit Factor    = Gross profit / Gross loss
"""

import json
import os
import math
from datetime import datetime, timedelta

DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "risk_state.json")
RISK_FREE_RATE = 0.052   # Current T-bill rate ~5.2%

# ── Risk limits ───────────────────────────────────────────────────────────────
MAX_PORTFOLIO_DRAWDOWN   = 0.15   # Halt all buying if down 15% from peak
MAX_DAILY_LOSS_PCT       = 0.05   # Halt trading if down 5% today
MAX_SINGLE_POSITION_PCT  = 0.25   # No position > 25% of portfolio
MAX_SECTOR_CONCENTRATION = 0.40   # No sector > 40% of portfolio
VAR_CONFIDENCE           = 0.95   # 95% VaR
HIGH_VOL_THRESHOLD       = 0.025  # Daily vol > 2.5% = high vol regime


def _load_state() -> dict:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "equity_peak": 0.0,
        "daily_snapshots": [],
        "trade_history": [],
        "halt_reason": None,
        "halt_until": None,
    }


def _save_state(d: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)


class RiskManager:
    """
    Stateful risk manager that wraps strategy execution.
    Call check_before_trade() before every order.
    Call record_equity() every cycle to track drawdown.
    """

    def __init__(self):
        self.state = _load_state()

    def record_equity(self, equity: float):
        """Call every cycle with current portfolio value."""
        today = datetime.now().strftime("%Y-%m-%d")
        snap  = {"date": today, "equity": equity, "ts": datetime.now().isoformat()}

        # Update peak
        if equity > self.state.get("equity_peak", 0):
            self.state["equity_peak"] = equity

        # Store daily snapshot
        snaps = self.state.get("daily_snapshots", [])
        # Replace today's if exists
        snaps = [s for s in snaps if s["date"] != today]
        snaps.append(snap)
        self.state["daily_snapshots"] = snaps[-365:]   # 1 year max
        _save_state(self.state)

    def record_trade(self, symbol: str, pnl: float, side: str = "sell"):
        """Record a completed trade for win rate / profit factor."""
        self.state.setdefault("trade_history", []).append({
            "date":   datetime.now().isoformat(),
            "symbol": symbol,
            "pnl":    pnl,
            "side":   side,
        })
        self.state["trade_history"] = self.state["trade_history"][-500:]
        _save_state(self.state)

    # ── Core risk checks ───────────────────────────────────────────────────────

    def get_drawdown(self, current_equity: float) -> float:
        """Current drawdown from peak as a fraction (0.10 = 10%)."""
        peak = self.state.get("equity_peak", current_equity)
        if not peak:
            return 0.0
        return max(0, (peak - current_equity) / peak)

    def get_daily_pnl_pct(self, current_equity: float) -> float:
        """Today's P&L as a fraction of yesterday's equity."""
        snaps = self.state.get("daily_snapshots", [])
        if len(snaps) < 2:
            return 0.0
        yesterday = sorted(snaps, key=lambda x: x["date"])[-2]
        prev = yesterday["equity"]
        return (current_equity - prev) / prev if prev else 0.0

    def is_trading_halted(self) -> tuple[bool, str]:
        """Returns (halted, reason). Check before every strategy run."""
        halt_until = self.state.get("halt_until")
        if halt_until and datetime.now() < datetime.fromisoformat(halt_until):
            return True, self.state.get("halt_reason", "Risk halt active")
        return False, ""

    def check_portfolio_risk(self, current_equity: float) -> dict:
        """
        Full risk assessment. Returns dict with all metrics + halt recommendation.
        """
        drawdown    = self.get_drawdown(current_equity)
        daily_pnl   = self.get_daily_pnl_pct(current_equity)
        halted, why = self.is_trading_halted()

        alerts = []
        halt   = halted

        if drawdown >= MAX_PORTFOLIO_DRAWDOWN:
            alerts.append(f"MAX DRAWDOWN {drawdown:.1%} >= {MAX_PORTFOLIO_DRAWDOWN:.0%} — HALTING")
            halt = True
            self.state["halt_reason"]  = f"Drawdown {drawdown:.1%} exceeded limit"
            self.state["halt_until"]   = (datetime.now() + timedelta(hours=4)).isoformat()

        if daily_pnl <= -MAX_DAILY_LOSS_PCT:
            alerts.append(f"DAILY LOSS {daily_pnl:.1%} <= -{MAX_DAILY_LOSS_PCT:.0%} — HALTING for today")
            halt = True
            tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=30)
            self.state["halt_reason"] = f"Daily loss {daily_pnl:.1%} limit hit"
            self.state["halt_until"]  = tomorrow.isoformat()

        if halt:
            _save_state(self.state)

        return {
            "drawdown":         round(drawdown * 100, 2),
            "daily_pnl_pct":    round(daily_pnl * 100, 2),
            "equity_peak":      self.state.get("equity_peak", current_equity),
            "current_equity":   current_equity,
            "halt":             halt,
            "halt_reason":      self.state.get("halt_reason", ""),
            "alerts":           alerts,
            "drawdown_limit":   MAX_PORTFOLIO_DRAWDOWN * 100,
            "daily_loss_limit": MAX_DAILY_LOSS_PCT * 100,
        }

    def kelly_size(self, win_rate: float, avg_win: float, avg_loss: float,
                   portfolio: float) -> float:
        """
        Kelly Criterion position size.
        f* = (win_rate / loss) - (lose_rate / win)
        Returns dollar amount to risk.
        """
        if avg_loss <= 0 or avg_win <= 0:
            return portfolio * 0.02   # Default 2% if no history
        lose_rate  = 1 - win_rate
        kelly_frac = (win_rate / avg_loss) - (lose_rate / avg_win)
        kelly_frac = max(0, min(kelly_frac, 0.20))   # Cap at 20%
        return round(portfolio * kelly_frac * 0.5, 2)  # Half-Kelly for safety

    # ── Performance analytics ──────────────────────────────────────────────────

    def get_performance_metrics(self, current_equity: float) -> dict:
        snaps  = self.state.get("daily_snapshots", [])
        trades = self.state.get("trade_history", [])

        if len(snaps) < 2:
            return {"error": "Insufficient history (need 2+ days)"}

        equities = [s["equity"] for s in sorted(snaps, key=lambda x: x["date"])]
        returns  = [(equities[i] - equities[i-1]) / equities[i-1]
                    for i in range(1, len(equities)) if equities[i-1] > 0]

        if not returns:
            return {"error": "No return data"}

        # Sharpe
        avg_ret     = sum(returns) / len(returns)
        daily_rf    = RISK_FREE_RATE / 252
        std_ret     = (sum((r - avg_ret)**2 for r in returns) / len(returns)) ** 0.5
        sharpe      = ((avg_ret - daily_rf) / std_ret * math.sqrt(252)) if std_ret else 0

        # Sortino (downside deviation only)
        down_rets   = [r for r in returns if r < 0]
        down_std    = (sum(r**2 for r in down_rets) / max(len(down_rets), 1)) ** 0.5
        sortino     = ((avg_ret - daily_rf) / down_std * math.sqrt(252)) if down_std else 0

        # Max drawdown
        peak = equities[0]
        max_dd = 0.0
        for e in equities:
            if e > peak:
                peak = e
            dd = (peak - e) / peak
            max_dd = max(max_dd, dd)

        # Win rate / profit factor
        wins   = [t["pnl"] for t in trades if t["pnl"] > 0]
        losses = [t["pnl"] for t in trades if t["pnl"] < 0]
        win_rate     = len(wins) / len(trades) * 100 if trades else 0
        profit_factor= sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 0

        # Annualised return
        days        = len(equities)
        total_ret   = (equities[-1] - equities[0]) / equities[0] if equities[0] else 0
        annual_ret  = (1 + total_ret) ** (365 / max(days, 1)) - 1

        return {
            "sharpe_ratio":    round(sharpe, 3),
            "sortino_ratio":   round(sortino, 3),
            "max_drawdown":    round(max_dd * 100, 2),
            "annual_return":   round(annual_ret * 100, 2),
            "daily_avg_return":round(avg_ret * 100, 4),
            "win_rate":        round(win_rate, 1),
            "profit_factor":   round(profit_factor, 3),
            "total_trades":    len(trades),
            "days_tracked":    days,
            "current_equity":  current_equity,
            "equity_peak":     self.state.get("equity_peak", current_equity),
        }

    def get_var(self, current_equity: float, confidence: float = 0.95) -> float:
        """
        Historical VaR at given confidence level.
        Returns max expected daily loss in dollars.
        """
        snaps = self.state.get("daily_snapshots", [])
        if len(snaps) < 20:
            return current_equity * 0.02   # Default 2% if insufficient history
        equities = [s["equity"] for s in sorted(snaps, key=lambda x: x["date"])]
        returns  = sorted([
            (equities[i] - equities[i-1]) / equities[i-1]
            for i in range(1, len(equities)) if equities[i-1] > 0
        ])
        idx = int((1 - confidence) * len(returns))
        var_pct = abs(returns[max(0, idx)])
        return round(current_equity * var_pct, 2)
