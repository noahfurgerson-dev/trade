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
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional

# ── Score definitions ─────────────────────────────────────────────────────────

# Minimum score for a strategy to be selected (0–100)
MIN_SCORE_THRESHOLD = 30

# How long to wait before running a strategy again (minutes)
COOLDOWN_MINUTES = {
    "momentum":       30,
    "mean_reversion": 30,
    "dca":            720,    # 12 hours
    "fear_greed":     240,    # 4 hours
    "trending":       120,    # 2 hours
    "ai_signals":     180,    # 3 hours — API cost conscious
    "rebalancer":     360,    # 6 hours
    "stock_momentum": 60,
    "dividend":       1440,   # 24 hours
    "options_income": 1440,
    "treasury":       1440,
}

# Max strategies to run per cycle (avoids decision paralysis + cost)
MAX_STRATEGIES_PER_CYCLE = 4

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

        # ── Fear & Greed ──────────────────────────────────────────────
        try:
            import requests
            resp = requests.get(
                "https://api.alternative.me/fng/?limit=1&format=json",
                timeout=5
            )
            data = resp.json().get("data", [{}])[0]
            ctx["fear_greed"]       = int(data.get("value", 50))
            ctx["fear_greed_label"] = data.get("value_classification", "Neutral")
        except Exception:
            pass   # Use default 50 (neutral)

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
            "momentum":       self._score_momentum,
            "mean_reversion": self._score_mean_reversion,
            "dca":            self._score_dca,
            "fear_greed":     self._score_fear_greed,
            "trending":       self._score_trending,
            "ai_signals":     self._score_ai_signals,
            "rebalancer":     self._score_rebalancer,
            "stock_momentum": self._score_stock_momentum,
            "dividend":       self._score_dividend,
            "options_income": self._score_options_income,
            "treasury":       self._score_treasury,
        }

        results = []
        for name, scorer in scorers.items():
            raw_score, reason = scorer(ctx)
            on_cooldown, cooldown_reason = self._is_on_cooldown(name)

            final_score = raw_score
            if on_cooldown:
                final_score = 0
                reason = f"[COOLDOWN] {cooldown_reason} | {reason}"

            results.append({
                "name":         name,
                "score":        final_score,
                "raw_score":    raw_score,
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

        evaluation = self.evaluate()
        selected   = [r for r in evaluation if r["selected"]]

        self._log(f"Selected {len(selected)} strategy/strategies to run.")

        if dry_run:
            self._log("DRY RUN — no strategies executed.")
            return {
                "evaluation":    evaluation,
                "selected":      selected,
                "actions":       [],
                "decision_log":  self.decision_log,
                "dry_run":       True,
            }

        all_actions = []

        # Load strategy classes
        from strategies.momentum        import MomentumStrategy
        from strategies.mean_reversion  import MeanReversionStrategy
        from strategies.dca             import DCAStrategy
        from strategies.fear_greed      import FearGreedStrategy
        from strategies.trending_scanner import TrendingScannerStrategy
        from strategies.ai_signals      import AISignalStrategy
        from strategies.rebalancer      import RebalancerStrategy
        from strategies.stock_momentum  import StockMomentumStrategy
        from strategies.dividend_collector import DividendCollectorStrategy
        from strategies.options_income  import OptionsIncomeStrategy
        from strategies.treasury_income import TreasuryIncomeStrategy

        # Build strategy instances (lazy — only for selected)
        def _build(name: str):
            """Instantiate a strategy by name."""
            if name == "momentum"       and self.rh:
                return MomentumStrategy(self.rh)
            if name == "mean_reversion" and self.rh:
                return MeanReversionStrategy(self.rh)
            if name == "dca"            and self.rh:
                return DCAStrategy(self.rh)
            if name == "fear_greed"     and self.rh:
                return FearGreedStrategy(self.rh)
            if name == "trending"       and self.rh:
                return TrendingScannerStrategy(self.rh)
            if name == "ai_signals"     and self.rh:
                return AISignalStrategy(self.rh)
            if name == "rebalancer"     and self.rh:
                return RebalancerStrategy(self.rh)
            if name == "stock_momentum" and self.alpaca:
                return StockMomentumStrategy(self.alpaca)
            if name == "dividend"       and self.alpaca:
                return DividendCollectorStrategy(self.alpaca)
            if name == "options_income" and self.alpaca:
                return OptionsIncomeStrategy(self.alpaca)
            if name == "treasury"       and self.alpaca:
                return TreasuryIncomeStrategy(self.alpaca)
            return None

        # Execute: SELL strategies first, then BUY strategies
        sell_priority = ["rebalancer", "mean_reversion", "fear_greed", "momentum"]
        buy_priority  = ["dca", "trending", "ai_signals", "fear_greed",
                          "dividend", "treasury", "options_income", "stock_momentum"]

        ordered = (
            [r for r in selected if r["name"] in sell_priority] +
            [r for r in selected if r["name"] not in sell_priority]
        )

        for result in ordered:
            name = result["name"]
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

        # Record history
        self.state["history"].append({
            "timestamp": datetime.now().isoformat(),
            "selected":  [r["name"] for r in selected],
            "actions":   len(all_actions),
        })
        # Keep last 200 history records
        self.state["history"] = self.state["history"][-200:]
        _save_state(self.state)

        self._log(
            f"=== CYCLE COMPLETE: {len(selected)} strategies, "
            f"{len(all_actions)} total action(s) ==="
        )

        return {
            "evaluation":   evaluation,
            "selected":     selected,
            "actions":      all_actions,
            "decision_log": self.decision_log,
            "dry_run":      False,
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
