"""
Strategy Orchestrator
─────────────────────
The automated brain of the trading platform.

Every time you click "Auto-Run" or the scheduler fires, the orchestrator:

  1. Reads the environment  — market hours, cash level, portfolio state,
                              Fear & Greed index, recent AI signals
  2. Scores every available strategy  — each strategy has condition
                                         functions that return a 0-100 score
  3. Selects the top strategies       — filters by minimum score threshold,
                                         respects cooldown timers, avoids
                                         conflicting strategies
  4. Executes them in the right order  — always SELL before BUY to free cash
  5. Returns a full decision log       — "WHY" each strategy was (or wasn't) chosen

Strategies known to the orchestrator:

  CRYPTO (Robinhood)
  ──────────────────
  momentum          Ride trends upward
  mean_reversion    Buy oversold dips
  dca               Time-agnostic accumulation
  fear_greed        Contrarian extreme moves
  trending          CoinGecko social momentum
  ai_signals        Claude AI market intelligence
  rebalancer        Drift-based portfolio rebalancing

  STOCKS (Alpaca)
  ───────────────
  stock_momentum    US equity swing trading
  dividend          Dividend ETF accumulation
  options_income    Covered calls + CSPs
  treasury          T-Bill/bond yield on idle cash
  news_sentiment    Reuters/CNBC/Reddit scraping + Claude AI signals
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional

from core.performance_tracker import log_cycle
from core.adaptive_learner    import load_weights, run_learning_cycle, should_run_cycle

# ── Score definitions ─────────────────────────────────────────────────────────

# Minimum score for a strategy to be selected (0–100)
MIN_SCORE_THRESHOLD = 30

# How long to wait before running a strategy again (minutes)
COOLDOWN_MINUTES = {
    "momentum":                   30,
    "mean_reversion":             30,
    "dca":                       720,    # 12 hours
    "fear_greed":                240,    # 4 hours
    "trending":                  120,    # 2 hours
    "ai_signals":                180,    # 3 hours — API cost conscious
    "rebalancer":                360,    # 6 hours
    "cross_platform_rebalancer": 240,    # 4 hours
    "stock_momentum":             60,
    "dividend":                 1440,    # 24 hours
    "options_income":           1440,
    "treasury":                 1440,
    "technical_analysis":         45,    # 45 min
    "whale_copy":               1440,    # 24 hours — 13F quarterly
    "sector_rotation":           480,    # 8 hours
    "pairs_trading":              60,    # 1 hour
    "earnings_play":             120,    # 2 hours
    "news_sentiment":             30,    # 30 min — RSS cache is 15 min
    # New intelligent strategies
    "pattern_recognition":        60,    # 1 hour — yfinance cached
    "ml_signals":                240,    # 4 hours — model retrained daily
    "agent_swarm":               180,    # 3 hours — 4 AI calls
}

# Max strategies to run per cycle (avoids decision paralysis + cost)
MAX_STRATEGIES_PER_CYCLE = 5

# State file for cooldowns and history
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "orchestrator_state.json")


def _load_state() -> dict:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_run": {}, "history": []}


def _save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


class StrategyOrchestrator:
    """
    Evaluates market conditions and selects the optimal strategy mix.
    """

    def __init__(self, rh_client=None, alpaca_client=None):
        self.rh      = rh_client
        self.alpaca  = alpaca_client
        self.state   = _load_state()
        self.decision_log: list[dict] = []

    # ── Logging ────────────────────────────────────────────────────────────────

    def _log(self, msg: str, level: str = "INFO"):
        entry = {
            "time":  datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "msg":   msg,
        }
        self.decision_log.append(entry)
        print(f"[ORCH {entry['time']}] [{level}] {msg}")

    # ── Environment reading ────────────────────────────────────────────────────

    def _get_market_context(self) -> dict:
        """Gather all environment signals into one context dict."""
        ctx = {
            "timestamp":       datetime.now().isoformat(),
            "hour":            datetime.now().hour,
            "weekday":         datetime.now().weekday(),   # 0=Mon
            "market_open":     False,
            "rh_configured":   False,
            "alpaca_configured": False,
            "cash_pct":        0.0,
            "portfolio_value": 0.0,
            "cash":            0.0,
            "holding_count":   0,
            "fear_greed":      50,   # Default neutral
            "fear_greed_label":"Neutral",
            "recent_pnl_pct":  0.0,
            "error":           None,
        }

        # ── Robinhood state ──────────────────────────────────────────
        if self.rh and self.rh.is_configured():
            ctx["rh_configured"] = True
            try:
                equity   = self.rh.get_total_equity()
                cash     = self.rh.get_cash()
                holdings = self.rh.get_holdings()
                ctx["portfolio_value"] = equity
                ctx["cash"]            = cash
                ctx["cash_pct"]        = (cash / equity * 100) if equity else 0
                ctx["holding_count"]   = len(holdings)
            except Exception as e:
                ctx["error"] = str(e)

        # ── Alpaca state ──────────────────────────────────────────────
        if self.alpaca and self.alpaca.is_configured():
            ctx["alpaca_configured"] = True
            try:
                ctx["market_open"] = self.alpaca.is_market_open()
            except Exception:
                pass

        # ── Fear & Greed (cached — shared with all strategy callers) ─────
        try:
            from strategies.fear_greed import fetch_fear_greed
            fng = fetch_fear_greed()
            ctx["fear_greed"]       = fng.get("value", 50)
            ctx["fear_greed_label"] = fng.get("label", "Neutral")
        except Exception:
            pass   # Use default 50 (neutral)

        # ── Seasonality score modifiers ───────────────────────────────────
        try:
            from core.seasonality import get_seasonal_score
            ctx["seasonal_crypto"] = get_seasonal_score("crypto")
            ctx["seasonal_stocks"] = get_seasonal_score("stocks")
        except Exception:
            ctx["seasonal_crypto"] = 1.0
            ctx["seasonal_stocks"] = 1.0

        return ctx

    # ── Individual strategy scorers ────────────────────────────────────────────

    def _score_momentum(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"]:
            return 0, "Robinhood not configured"
        score = 40   # Base score
        reasons = []

        # Boost if market is in greed territory (momentum works well)
        fg = ctx["fear_greed"]
        if 45 <= fg <= 74:
            score += 20
            reasons.append(f"F&G neutral/greed ({fg}) favours momentum")
        elif fg >= 75:
            score -= 10
            reasons.append(f"Extreme greed ({fg}) — risk of reversal")
        elif fg <= 25:
            score -= 20
            reasons.append(f"Extreme fear ({fg}) — momentum may fail")

        # Boost during market hours (approx 9:30–16:00 ET = 14:30–21:00 UTC)
        if 14 <= ctx["hour"] <= 21:
            score += 10
            reasons.append("US market hours")

        # Reduce if very low cash
        if ctx["cash_pct"] < 10:
            score -= 30
            reasons.append(f"Low cash {ctx['cash_pct']:.0f}% — limited buying power")

        return min(score, 100), "; ".join(reasons) or "Standard conditions"

    def _score_mean_reversion(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"]:
            return 0, "Robinhood not configured"
        score = 35
        reasons = []

        fg = ctx["fear_greed"]
        if fg <= 30:
            score += 35
            reasons.append(f"Fear/Extreme Fear ({fg}) — dips likely, mean reversion ideal")
        elif fg <= 45:
            score += 15
            reasons.append(f"Fear zone ({fg}) — some dip opportunities")
        elif fg >= 75:
            score -= 15
            reasons.append(f"Extreme greed ({fg}) — fewer oversold opportunities")

        if ctx["cash_pct"] < 10:
            score -= 25
            reasons.append("Low cash")

        return min(score, 100), "; ".join(reasons) or "Standard conditions"

    def _score_dca(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"]:
            return 0, "Robinhood not configured"
        score = 50   # DCA is almost always valid
        reasons = ["DCA is always a valid long-term approach"]

        if ctx["cash_pct"] > 20:
            score += 20
            reasons.append(f"High cash {ctx['cash_pct']:.0f}% — good time to deploy")

        if ctx["fear_greed"] <= 30:
            score += 15
            reasons.append("Fear zone — DCA at lower prices")

        return min(score, 100), "; ".join(reasons)

    def _score_fear_greed(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"]:
            return 0, "Robinhood not configured"
        fg = ctx["fear_greed"]
        score = 0
        reasons = []

        if fg <= 25:
            score = 90
            reasons.append(f"EXTREME FEAR ({fg}) — contrarian buy signal is very strong")
        elif fg <= 44:
            score = 65
            reasons.append(f"Fear ({fg}) — moderate buy signal")
        elif fg >= 80:
            score = 85
            reasons.append(f"EXTREME GREED ({fg}) — profit-taking signal")
        elif fg >= 65:
            score = 50
            reasons.append(f"Greed ({fg}) — consider reducing exposure")
        else:
            score = 15
            reasons.append(f"Neutral ({fg}) — no contrarian edge")

        return min(score, 100), "; ".join(reasons)

    def _score_trending(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"]:
            return 0, "Robinhood not configured"
        score = 40
        reasons = []

        fg = ctx["fear_greed"]
        if fg >= 55:
            score += 20
            reasons.append("Market appetite up — social trending more reliable")
        elif fg <= 30:
            score -= 10
            reasons.append("Fear conditions — trending less predictive")

        if ctx["cash_pct"] < 5:
            score -= 40
            reasons.append("No cash for trending buys")
        elif ctx["cash_pct"] > 15:
            score += 10
            reasons.append("Sufficient cash for opportunistic entries")

        return min(score, 100), "; ".join(reasons) or "Standard conditions"

    def _score_ai_signals(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"]:
            return 0, "Robinhood not configured"
        # AI signals are always useful but have API cost
        score = 60
        reasons = ["Claude AI provides structured market analysis"]

        if ctx["portfolio_value"] < 500:
            score -= 20
            reasons.append("Portfolio too small — API cost disproportionate")

        if ctx["cash_pct"] < 5:
            score -= 20
            reasons.append("Low cash — buy signals won't auto-execute anyway")

        return min(score, 100), "; ".join(reasons)

    def _score_rebalancer(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"]:
            return 0, "Robinhood not configured"
        score = 45
        reasons = []

        # Better to rebalance after big moves
        fg = ctx["fear_greed"]
        if fg <= 25 or fg >= 80:
            score += 25
            reasons.append(f"Extreme market ({fg}) — portfolio likely drifted significantly")
        else:
            reasons.append("Regular drift check")

        if ctx["holding_count"] < 2:
            score -= 20
            reasons.append("Too few holdings to meaningfully rebalance")

        return min(score, 100), "; ".join(reasons)

    def _score_stock_momentum(self, ctx: dict) -> tuple[int, str]:
        if not ctx["alpaca_configured"]:
            return 0, "Alpaca not configured"
        if not ctx["market_open"]:
            return 5, "Market closed — can't trade stocks"

        score = 50
        reasons = []

        fg = ctx["fear_greed"]
        if 45 <= fg <= 74:
            score += 25
            reasons.append("Neutral-to-greed environment suits stock momentum")
        elif fg >= 75:
            score += 10
            reasons.append("Strong market appetite")
        elif fg <= 25:
            score -= 15
            reasons.append("Fear conditions — stocks may gap down")

        # Best during early/mid-session
        if 14 <= ctx["hour"] <= 18:
            score += 15
            reasons.append("Optimal trading hours (US session)")

        return min(score, 100), "; ".join(reasons) or "Standard conditions"

    def _score_dividend(self, ctx: dict) -> tuple[int, str]:
        if not ctx["alpaca_configured"]:
            return 0, "Alpaca not configured"
        if not ctx["market_open"]:
            return 5, "Market closed — dividend buy deferred"

        score = 55   # Dividend buying is always good
        reasons = ["Dividend ETFs generate passive income regardless of conditions"]

        # Extra good when fear is high (buy at discount)
        if ctx["fear_greed"] <= 35:
            score += 20
            reasons.append("Market dip — buying dividend ETFs at discount")

        return min(score, 100), "; ".join(reasons)

    def _score_options_income(self, ctx: dict) -> tuple[int, str]:
        if not ctx["alpaca_configured"]:
            return 0, "Alpaca not configured"
        if not ctx["market_open"]:
            return 5, "Market closed — options deferred"

        score = 45
        reasons = []

        # Covered calls are best in neutral/slightly bearish markets (IV elevated)
        fg = ctx["fear_greed"]
        if 35 <= fg <= 65:
            score += 20
            reasons.append("Neutral conditions — premium selling ideal")
        elif fg <= 25:
            score += 10
            reasons.append("High fear = high IV = richer premiums")
        elif fg >= 80:
            score -= 10
            reasons.append("Extreme greed — calls at risk of being called away")

        return min(score, 100), "; ".join(reasons) or "Standard"

    def _score_treasury(self, ctx: dict) -> tuple[int, str]:
        if not ctx["alpaca_configured"]:
            return 0, "Alpaca not configured"
        if not ctx["market_open"]:
            return 5, "Market closed — treasury buy deferred"

        score = 60
        reasons = ["T-Bills generate risk-free yield on idle capital"]

        if ctx["fear_greed"] <= 30:
            score += 15
            reasons.append("Fear conditions — parking cash in treasuries while waiting")

        return min(score, 100), "; ".join(reasons)

    def _score_cross_platform_rebalancer(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"] and not ctx["alpaca_configured"]:
            return 0, "Neither platform configured"
        if not (ctx["rh_configured"] and ctx["alpaca_configured"]):
            return 10, "Only one platform connected — cross-platform rebalance needs both"

        score = 55
        reasons = ["Both platforms connected — unified rebalance available"]

        fg = ctx["fear_greed"]
        if fg <= 25 or fg >= 80:
            score += 25
            reasons.append(f"Extreme market ({fg}) — portfolios likely drifted across platforms")
        elif fg <= 40 or fg >= 65:
            score += 10
            reasons.append(f"Market at extremes ({fg}) — worth checking cross-platform drift")

        return min(score, 100), "; ".join(reasons)

    def _score_technical_analysis(self, ctx: dict) -> tuple[int, str]:
        if not ctx["alpaca_configured"]:
            return 0, "Alpaca not configured"
        if not ctx["market_open"]:
            return 5, "Market closed — TA deferred"
        score   = 55
        reasons = ["Multi-indicator confluence provides high-quality signals"]
        if 14 <= ctx["hour"] <= 20:
            score += 15
            reasons.append("Active US session — optimal for TA signals")
        fg = ctx["fear_greed"]
        if fg < 30 or fg > 70:
            score += 10
            reasons.append(f"Extreme F&G ({fg}) increases signal clarity")
        return min(score, 100), "; ".join(reasons)

    def _score_whale_copy(self, ctx: dict) -> tuple[int, str]:
        if not ctx["alpaca_configured"]:
            return 0, "Alpaca not configured"
        if not ctx["market_open"]:
            return 5, "Market closed — whale copy deferred"
        score   = 50
        reasons = ["Mirrors Buffett/Ackman/Tepper 13F conviction picks"]
        if ctx["cash_pct"] > 15:
            score += 20
            reasons.append(f"Good cash level {ctx['cash_pct']:.0f}% to deploy into institutional picks")
        return min(score, 100), "; ".join(reasons)

    def _score_sector_rotation(self, ctx: dict) -> tuple[int, str]:
        if not ctx["alpaca_configured"]:
            return 0, "Alpaca not configured"
        if not ctx["market_open"]:
            return 5, "Market closed"
        score   = 50
        reasons = ["Sector rotation captures economic cycle momentum"]
        fg = ctx["fear_greed"]
        if fg >= 60:
            score += 15
            reasons.append("Risk-on environment favours sector momentum")
        elif fg <= 30:
            score += 10
            reasons.append("Rotation into defensive sectors (XLV, XLU)")
        return min(score, 100), "; ".join(reasons)

    def _score_pairs_trading(self, ctx: dict) -> tuple[int, str]:
        if not ctx["alpaca_configured"]:
            return 0, "Alpaca not configured"
        if not ctx["market_open"]:
            return 5, "Market closed"
        score   = 45
        reasons = ["Statistical arbitrage is market-neutral — works in any environment"]
        if 45 <= ctx["fear_greed"] <= 65:
            score += 10
            reasons.append("Neutral market ideal for mean-reversion pairs")
        return min(score, 100), "; ".join(reasons)

    def _score_news_sentiment(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"] and not ctx["alpaca_configured"]:
            return 0, "No platform configured"
        score   = 55
        reasons = ["News sentiment provides real-time edge on market-moving events"]

        # Higher value when market is open (actionable signals)
        if ctx["market_open"]:
            score += 15
            reasons.append("Market open — news signals immediately actionable")

        fg = ctx["fear_greed"]
        if fg <= 25 or fg >= 80:
            score += 15
            reasons.append(f"Extreme market sentiment ({fg}) — news catalysts have outsized impact")
        elif fg <= 40 or fg >= 65:
            score += 8
            reasons.append(f"Directional market ({fg}) — news amplifies existing trend")

        # Very useful when cash available to act on buy signals
        if ctx["cash_pct"] > 15:
            score += 10
            reasons.append(f"Good cash level {ctx['cash_pct']:.0f}% to act on buy signals")
        elif ctx["cash_pct"] < 5:
            score -= 15
            reasons.append("Low cash — buy signals may not execute")

        return min(score, 100), "; ".join(reasons)

    def _score_earnings_play(self, ctx: dict) -> tuple[int, str]:
        if not ctx["alpaca_configured"]:
            return 0, "Alpaca not configured"
        if not ctx["market_open"]:
            return 5, "Market closed"
        score   = 55
        reasons = ["Earnings plays exploit pre-drift and post-earnings momentum"]
        if ctx["fear_greed"] >= 50:
            score += 15
            reasons.append("Positive sentiment amplifies earnings beats")
        return min(score, 100), "; ".join(reasons)

    def _score_pattern_recognition(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"]:
            return 0, "Robinhood not configured"
        score   = 55
        reasons = ["Chart pattern detection provides high-probability technical entries"]
        if ctx["cash_pct"] < 5:
            score -= 25
            reasons.append("Low cash — pattern buys won't execute")
        # Patterns are more reliable in trending markets (moderate F&G)
        fg = ctx["fear_greed"]
        if 35 <= fg <= 75:
            score += 15
            reasons.append(f"Moderate F&G ({fg}) — trends and patterns more reliable")
        seasonal = ctx.get("seasonal_crypto", 1.0)
        if seasonal >= 1.10:
            score += 10
            reasons.append(f"Seasonally favorable period (x{seasonal:.2f})")
        elif seasonal <= 0.90:
            score -= 10
            reasons.append(f"Seasonally unfavorable period (x{seasonal:.2f})")
        return min(score, 100), "; ".join(reasons)

    def _score_ml_signals(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"]:
            return 0, "Robinhood not configured"
        score   = 60
        reasons = ["ML models trained on 2y of historical indicators"]
        if ctx["cash_pct"] < 5:
            score -= 20
            reasons.append("Low cash — buy signals can't execute")
        seasonal = ctx.get("seasonal_crypto", 1.0)
        if seasonal >= 1.10:
            score += 10
            reasons.append(f"Seasonally favorable (x{seasonal:.2f})")
        return min(score, 100), "; ".join(reasons)

    def _score_agent_swarm(self, ctx: dict) -> tuple[int, str]:
        if not ctx["rh_configured"] and not ctx["alpaca_configured"]:
            return 0, "No platform configured"
        # Requires Anthropic key
        import os
        if not os.getenv("ANTHROPIC_API_KEY", "").strip():
            return 0, "ANTHROPIC_API_KEY not set"
        score   = 65
        reasons = ["4 specialized AI agents: Sentiment, Technicals, Risk, Macro"]
        if ctx["cash_pct"] < 5:
            score -= 20
            reasons.append("Low cash — buy signals can't execute")
        fg = ctx["fear_greed"]
        if fg <= 25 or fg >= 80:
            score += 15
            reasons.append(f"Extreme F&G ({fg}) — agent consensus most valuable at extremes")
        return min(score, 100), "; ".join(reasons)

    # ── Main orchestration ─────────────────────────────────────────────────────

    def _is_on_cooldown(self, strategy_name: str) -> tuple[bool, str]:
        """Check if a strategy is still in its cooldown window."""
        last_run_str = self.state["last_run"].get(strategy_name)
        if not last_run_str:
            return False, "Never run"
        last_run = datetime.fromisoformat(last_run_str)
        cooldown_mins = COOLDOWN_MINUTES.get(strategy_name, 60)
        next_run = last_run + timedelta(minutes=cooldown_mins)
        if datetime.now() < next_run:
            mins_left = int((next_run - datetime.now()).total_seconds() / 60)
            return True, f"Cooldown: {mins_left}m remaining"
        return False, f"Cooldown expired (last ran {last_run.strftime('%H:%M')})"

    def evaluate(self) -> list[dict]:
        """
        Score all strategies and return a ranked list with reasoning.
        Does NOT execute anything — pure evaluation.
        """
        self._log("=== STRATEGY EVALUATION ===")
        ctx = self._get_market_context()

        self._log(
            f"Context: F&G={ctx['fear_greed']} ({ctx['fear_greed_label']})  "
            f"Market={'OPEN' if ctx['market_open'] else 'CLOSED'}  "
            f"Cash={ctx['cash_pct']:.1f}%  "
            f"Portfolio=${ctx['portfolio_value']:,.0f}"
        )

        scorers = {
            # Crypto — Robinhood
            "momentum":                   self._score_momentum,
            "mean_reversion":             self._score_mean_reversion,
            "dca":                        self._score_dca,
            "fear_greed":                 self._score_fear_greed,
            "trending":                   self._score_trending,
            "ai_signals":                 self._score_ai_signals,
            "rebalancer":                 self._score_rebalancer,
            # Cross-platform
            "cross_platform_rebalancer":  self._score_cross_platform_rebalancer,
            # Stocks — Alpaca
            "stock_momentum":             self._score_stock_momentum,
            "technical_analysis":         self._score_technical_analysis,
            "sector_rotation":            self._score_sector_rotation,
            "pairs_trading":              self._score_pairs_trading,
            "earnings_play":              self._score_earnings_play,
            "whale_copy":                 self._score_whale_copy,
            "dividend":                   self._score_dividend,
            "options_income":             self._score_options_income,
            "treasury":                   self._score_treasury,
            # News / AI
            "news_sentiment":             self._score_news_sentiment,
            # New intelligent strategies
            "pattern_recognition":        self._score_pattern_recognition,
            "ml_signals":                 self._score_ml_signals,
            "agent_swarm":                self._score_agent_swarm,
        }

        # Load adaptive weights (1.0 = neutral, >1 = boosted, <1 = penalised)
        weights = load_weights()

        results = []
        for name, scorer in scorers.items():
            raw_score, reason = scorer(ctx)
            on_cooldown, cooldown_reason = self._is_on_cooldown(name)

            # Apply learned weight to base score
            weight      = weights.get(name, 1.0)
            adj_score   = int(min(100, raw_score * weight))
            weight_note = f" [w={weight:.2f}]" if abs(weight - 1.0) > 0.05 else ""

            final_score = adj_score
            if on_cooldown:
                final_score = 0
                reason = f"[COOLDOWN] {cooldown_reason} | {reason}{weight_note}"
            elif weight_note:
                reason = reason + weight_note

            results.append({
                "name":         name,
                "score":        final_score,
                "raw_score":    raw_score,
                "weight":       weight,
                "reason":       reason,
                "on_cooldown":  on_cooldown,
                "selected":     False,
            })

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)

        # Mark top strategies as selected (above threshold, up to max)
        selected_count = 0
        for r in results:
            if selected_count >= MAX_STRATEGIES_PER_CYCLE:
                break
            if r["score"] >= MIN_SCORE_THRESHOLD:
                r["selected"] = True
                selected_count += 1
                self._log(
                    f"  [SELECT] {r['name']:20} score={r['score']:3d} -- {r['reason'][:80]}",
                    "TRADE"
                )
            else:
                self._log(
                    f"  [SKIP]   {r['name']:20} score={r['score']:3d} -- {r['reason'][:80]}"
                )

        return results

    def run(
        self,
        rh_client=None,
        alpaca_client=None,
        dry_run: bool = False,
    ) -> dict:
        """
        Full orchestration cycle:
        1. Evaluate strategies
        2. Execute selected ones
        3. Record results
        """
        if rh_client:
            self.rh = rh_client
        if alpaca_client:
            self.alpaca = alpaca_client

        self.decision_log = []
        self._log(f"Orchestrator cycle started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # ── Determine active trading mode ──────────────────────────────────
        try:
            from core.mode_manager import get_current_mode, get_strategies_for_mode, MODES
            active_mode      = get_current_mode()
            mode_strategies  = get_strategies_for_mode(active_mode)
            mode_name        = MODES[active_mode]["name"]
            self._log(f"Active mode: [{active_mode}] {mode_name}", "INFO")
        except Exception as _me:
            active_mode     = "algo_strategies"
            mode_strategies = ["all"]
            self._log(f"Mode manager unavailable ({_me}) — defaulting to algo_strategies", "WARN")

        evaluation = self.evaluate()

        # ── Mode-based strategy selection ──────────────────────────────────
        if mode_strategies == ["all"]:
            # Full orchestrator selection (algo_strategies mode)
            selected = [r for r in evaluation if r["selected"]]
        else:
            # Force only the mode's designated strategies to run,
            # regardless of their scores — the mode IS the decision.
            selected = []
            all_names = [r["name"] for r in evaluation]
            for strat_name in mode_strategies:
                if strat_name in all_names:
                    r = next(r for r in evaluation if r["name"] == strat_name)
                    r["selected"] = True
                    selected.append(r)

        self._log(f"Mode [{active_mode}] selected {len(selected)} strategy/strategies to run.")

        if dry_run:
            self._log("DRY RUN — no strategies executed.")
            return {
                "evaluation":    evaluation,
                "selected":      selected,
                "actions":       [],
                "decision_log":  self.decision_log,
                "dry_run":       True,
            }

        # ── Snapshot portfolio value BEFORE execution ──────────────────────
        pv_before = 0.0
        try:
            if self.rh and self.rh.is_configured():
                pv_before += self.rh.get_total_equity() or 0.0
            if self.alpaca and self.alpaca.is_configured():
                pv_before += self.alpaca.get_portfolio_value() or 0.0
        except Exception:
            pass

        all_actions = []

        # Load strategy classes
        from strategies.momentum        import MomentumStrategy
        from strategies.mean_reversion  import MeanReversionStrategy
        from strategies.dca             import DCAStrategy
        from strategies.fear_greed      import FearGreedStrategy
        from strategies.trending_scanner import TrendingScannerStrategy
        from strategies.ai_signals              import AISignalStrategy
        from strategies.rebalancer              import RebalancerStrategy
        from strategies.stock_momentum          import StockMomentumStrategy
        from strategies.dividend_collector      import DividendCollectorStrategy
        from strategies.options_income          import OptionsIncomeStrategy
        from strategies.treasury_income         import TreasuryIncomeStrategy
        from strategies.cross_platform_rebalancer import CrossPlatformRebalancer
        from strategies.technical_engine         import TechnicalAnalysisStrategy
        from strategies.whale_copy               import WhaleCopyStrategy
        from strategies.sector_rotation          import SectorRotationStrategy
        from strategies.pairs_trading            import PairsTradingStrategy
        from strategies.earnings_play            import EarningsPlayStrategy
        from strategies.news_sentiment           import NewsSentimentStrategy
        from strategies.pattern_recognition     import PatternRecognitionStrategy
        from strategies.ml_signals              import MLSignalStrategy

        # Build strategy instances (lazy — only for selected)
        def _build(name: str):
            """Instantiate a strategy by name."""
            if name == "momentum"              and self.rh:     return MomentumStrategy(self.rh)
            if name == "mean_reversion"        and self.rh:     return MeanReversionStrategy(self.rh)
            if name == "dca"                   and self.rh:     return DCAStrategy(self.rh)
            if name == "fear_greed"            and self.rh:     return FearGreedStrategy(self.rh)
            if name == "trending"              and self.rh:     return TrendingScannerStrategy(self.rh)
            if name == "ai_signals"            and self.rh:     return AISignalStrategy(self.rh)
            if name == "rebalancer"            and self.rh:     return RebalancerStrategy(self.rh)
            if name == "cross_platform_rebalancer":             return CrossPlatformRebalancer(self.rh, self.alpaca)
            if name == "stock_momentum"        and self.alpaca: return StockMomentumStrategy(self.alpaca)
            if name == "technical_analysis"    and self.alpaca: return TechnicalAnalysisStrategy(self.alpaca)
            if name == "sector_rotation"       and self.alpaca: return SectorRotationStrategy(self.alpaca)
            if name == "pairs_trading"         and self.alpaca: return PairsTradingStrategy(self.alpaca)
            if name == "earnings_play"         and self.alpaca: return EarningsPlayStrategy(self.alpaca)
            if name == "whale_copy"            and self.alpaca: return WhaleCopyStrategy(self.alpaca)
            if name == "dividend"              and self.alpaca: return DividendCollectorStrategy(self.alpaca)
            if name == "options_income"        and self.alpaca: return OptionsIncomeStrategy(self.alpaca)
            if name == "treasury"              and self.alpaca: return TreasuryIncomeStrategy(self.alpaca)
            if name == "news_sentiment":                        return NewsSentimentStrategy(self.rh, self.alpaca)
            if name == "pattern_recognition"   and self.rh:     return PatternRecognitionStrategy(self.rh)
            if name == "ml_signals"            and self.rh:     return MLSignalStrategy(self.rh)
            if name == "agent_swarm":
                # AgentSwarm is not a BaseStrategy — run inline
                return None   # handled below
            return None

        # Execute: SELL strategies first (free up cash), then BUY
        sell_priority = ["cross_platform_rebalancer", "rebalancer", "sector_rotation",
                          "mean_reversion", "fear_greed", "momentum", "pairs_trading"]

        ordered = (
            [r for r in selected if r["name"] in sell_priority] +
            [r for r in selected if r["name"] not in sell_priority]
        )

        for result in ordered:
            name = result["name"]

            # ── Agent Swarm: runs inline (not a BaseStrategy) ──────────────
            if name == "agent_swarm":
                self._log(f">>> Running agent_swarm (score={result['score']})...", "TRADE")
                try:
                    from core.agent_swarm import run_swarm, build_technicals_context
                    from strategies.news_sentiment import fetch_all_news, analyse_articles

                    rh_holdings = ""
                    if self.rh and self.rh.is_configured():
                        holdings = self.rh.get_holdings()
                        cash     = self.rh.get_cash()
                        equity   = self.rh.get_total_equity()
                        rh_holdings = (
                            f"Cash: ${cash:,.0f}  Equity: ${equity:,.0f}\n"
                            + "\n".join(
                                f"  {h['pair']}: qty={h['quantity']:.4f}  "
                                f"value=${h['market_value']:,.0f}  pnl={h['pnl_pct']:+.1f}%"
                                for h in holdings
                            )
                        )

                    market_ctx = (
                        f"F&G={ctx['fear_greed']} ({ctx['fear_greed_label']})  "
                        f"Market={'OPEN' if ctx['market_open'] else 'CLOSED'}  "
                        f"Seasonal crypto={ctx.get('seasonal_crypto',1.0):.2f}"
                    )
                    news_arts  = fetch_all_news(use_cache=True)
                    news_ctx   = "\n".join(
                        f"- {a['title']} ({a['source']})"
                        for a in analyse_articles(news_arts)[:8]
                    )
                    tech_ctx   = build_technicals_context(
                        ["BTC-USD", "ETH-USD", "SOL-USD", "NVDA", "SPY", "QQQ"]
                    )
                    swarm_tickers = ["BTC", "ETH", "SOL", "DOGE", "ADA",
                                     "NVDA", "MSFT", "AAPL", "SPY", "QQQ"]

                    swarm_result = run_swarm(
                        market_context=market_ctx,
                        portfolio_context=rh_holdings,
                        tickers=swarm_tickers,
                        news_context=news_ctx,
                        technicals_context=tech_ctx,
                    )

                    # Execute STRONG BUY signals from swarm on crypto
                    if self.rh and self.rh.is_configured():
                        holdings_map = {h["pair"]: h for h in self.rh.get_holdings()}
                        cash         = self.rh.get_cash()
                        equity       = self.rh.get_total_equity()

                        for ticker, cons in swarm_result.get("consensus", {}).items():
                            if cons["action"] == "BUY" and cons["strength"] == "STRONG":
                                pair = f"{ticker}-USD"
                                if pair in holdings_map:
                                    continue
                                notional = min(equity * 0.05, cash * 0.20)
                                if notional < 10:
                                    continue
                                quote = self.rh.get_quote(pair)
                                price = quote.get("price", 0)
                                if not price:
                                    continue
                                qty = notional / price
                                self._log(
                                    f"  SWARM BUY {pair} ${notional:.0f} "
                                    f"(score={cons['score']:.0%}, {cons['agent_count']} agents agree)",
                                    "TRADE"
                                )
                                order = self.rh.buy_market(pair, qty)
                                all_actions.append({
                                    "pair": pair, "action": "BUY",
                                    "quantity": qty, "price": price, "notional": notional,
                                    "strategy": "agent_swarm",
                                    "reason": f"Agent swarm STRONG consensus BUY (score={cons['score']:.0%})",
                                    "order_id": order.get("id"),
                                })

                    verdicts = swarm_result.get("agent_verdicts", [])
                    for v in verdicts:
                        self._log(f"  {v}")

                    self.state["last_run"][name] = datetime.now().isoformat()
                    result["swarm_result"] = swarm_result

                except Exception as e:
                    self._log(f"  agent_swarm FAILED: {e}", "WARN")
                    result["error"] = str(e)
                continue  # skip _build() path below

            strat = _build(name)
            if not strat:
                self._log(f"  Cannot build {name} — client missing", "WARN")
                continue

            self._log(f">>> Running {name} (score={result['score']})...", "TRADE")
            try:
                actions = strat.run()
                for a in actions:
                    a["strategy"] = name
                all_actions.extend(actions)

                # Forward strategy log to orchestrator log
                for entry in strat.log:
                    self.decision_log.append({
                        "time":  entry["time"],
                        "level": entry["level"],
                        "msg":   f"[{name}] {entry['message']}",
                    })

                # Update cooldown
                self.state["last_run"][name] = datetime.now().isoformat()

            except Exception as e:
                self._log(f"  {name} FAILED: {e}", "WARN")
                result["error"] = str(e)

        # ── Snapshot portfolio value AFTER execution ───────────────────────
        pv_after = 0.0
        try:
            if self.rh and self.rh.is_configured():
                pv_after += self.rh.get_total_equity() or 0.0
            if self.alpaca and self.alpaca.is_configured():
                pv_after += self.alpaca.get_portfolio_value() or 0.0
        except Exception:
            pass

        # ── Log cycle to performance tracker ───────────────────────────────
        ran_strategies = [r["name"] for r in selected]
        if ran_strategies:
            try:
                log_cycle(
                    strategies=ran_strategies,
                    pv_before=pv_before,
                    pv_after=pv_after,
                    actions=len(all_actions),
                )
            except Exception as _le:
                self._log(f"  Perf log error: {_le}", "WARN")

        # ── Log mode performance ────────────────────────────────────────────
        try:
            from core.mode_performance import log_mode_cycle
            log_mode_cycle(
                mode=active_mode,
                pv_before=pv_before,
                pv_after=pv_after,
                actions=len(all_actions),
                strategies_run=ran_strategies,
            )
        except Exception as _mpe:
            self._log(f"  Mode perf log error: {_mpe}", "WARN")

        # ── Trigger adaptive learning cycle if due ──────────────────────────
        learning_result = None
        try:
            if should_run_cycle():
                self._log("12-hour learning cycle triggered — updating strategy weights...", "INFO")
                learning_result = run_learning_cycle()
                if learning_result.get("ran"):
                    self._log(
                        f"  Weights updated (cycle #{learning_result.get('cycle', '?')}): "
                        f"{len(learning_result.get('changes', {}))} strategies adjusted",
                        "INFO",
                    )
        except Exception as _ae:
            self._log(f"  Adaptive learner error: {_ae}", "WARN")

        # Record history
        self.state["history"].append({
            "timestamp": datetime.now().isoformat(),
            "selected":  [r["name"] for r in selected],
            "actions":   len(all_actions),
            "pv_before": pv_before,
            "pv_after":  pv_after,
        })
        # Keep last 200 history records
        self.state["history"] = self.state["history"][-200:]
        _save_state(self.state)

        delta = pv_after - pv_before
        self._log(
            f"=== CYCLE COMPLETE: {len(selected)} strategies, "
            f"{len(all_actions)} total action(s)"
            + (f", P&L ${delta:+.2f}" if pv_before else "")
            + " ==="
        )

        return {
            "evaluation":       evaluation,
            "selected":         selected,
            "actions":          all_actions,
            "decision_log":     self.decision_log,
            "dry_run":          False,
            "pv_before":        pv_before,
            "pv_after":         pv_after,
            "learning_result":  learning_result,
        }

    def get_schedule_recommendation(self) -> dict:
        """
        Suggest when to run next based on cooldowns and market hours.
        """
        suggestions = []

        for name, cooldown in COOLDOWN_MINUTES.items():
            last_str = self.state["last_run"].get(name)
            if last_str:
                last = datetime.fromisoformat(last_str)
                next_run = last + timedelta(minutes=cooldown)
                suggestions.append({
                    "strategy":   name,
                    "next_run":   next_run.strftime("%H:%M"),
                    "in_minutes": max(0, int((next_run - datetime.now()).total_seconds() / 60)),
                })
            else:
                suggestions.append({
                    "strategy":   name,
                    "next_run":   "Now",
                    "in_minutes": 0,
                })

        suggestions.sort(key=lambda x: x["in_minutes"])
        return {"schedule": suggestions}
