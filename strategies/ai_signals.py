"""
Multi-AI Signal Strategy
─────────────────────────
Queries Claude, GPT-4o, Gemini, and Groq simultaneously with the same
market prompt. Aggregates responses into a consensus BUY/SELL/HOLD signal.

Signals with >= 2 providers agreeing are considered HIGH CONVICTION and
auto-execute. Single-provider signals are logged but not executed.

API keys needed in .env (add whichever you have — works with 1 or all 4):
  ANTHROPIC_API_KEY  — Claude (already present)
  OPENAI_API_KEY     — GPT-4o-mini
  GOOGLE_API_KEY     — Gemini 1.5 Flash
  GROQ_API_KEY       — Llama 3.3 70B (free tier)
"""

import os
import json
from datetime import datetime
from strategies.base import BaseStrategy
from core.multi_ai_signals import run_multi_ai_analysis

CRYPTO_UNIVERSE = ["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "ADA-USD", "AVAX-USD"]
CRYPTO_TICKERS  = {p.split("-")[0]: p for p in CRYPTO_UNIVERSE}

# Minimum agreement fraction to auto-execute (0.67 = 2/3 providers must agree)
MIN_AGREEMENT_TO_EXECUTE = 0.50
MIN_CONFIDENCE_TO_EXECUTE = 0.72


class AISignalStrategy(BaseStrategy):

    def __init__(self, client, alpaca_client=None, max_position_pct=0.15):
        super().__init__(client, max_position_pct)
        self.rh          = client
        self.alpaca      = alpaca_client
        self.last_result: dict = {}

    def describe(self) -> str:
        return "Multi-AI consensus: Claude + GPT-4o + Gemini + Groq vote on market signals."

    def run(self) -> list[dict]:
        actions = []
        self._log("Multi-AI analysis starting — querying all configured providers...")

        # ── Build market context ───────────────────────────────────────
        ctx_parts = []
        cash = equity = 0.0

        if self.rh and self.rh.is_configured():
            try:
                holdings = self.rh.get_holdings()
                cash   = self.rh.get_cash()
                equity = self.rh.get_total_equity()

                quotes = {}
                for pair in CRYPTO_UNIVERSE:
                    q = self.rh.get_quote(pair)
                    if q.get("price"):
                        quotes[pair] = round(float(q["price"]), 4)

                ctx_parts.append(json.dumps({
                    "platform":      "Robinhood (crypto)",
                    "equity_usd":    round(equity, 2),
                    "cash_usd":      round(cash, 2),
                    "holdings": [
                        {
                            "symbol":   h["pair"],
                            "qty":      h["quantity"],
                            "price":    h["current_price"],
                            "value":    round(h["market_value"], 2),
                            "pnl_pct":  round(h["pnl_pct"], 2),
                        }
                        for h in holdings
                    ],
                    "live_prices":   quotes,
                }, indent=2))
            except Exception as e:
                ctx_parts.append(f"Robinhood data unavailable: {e}")

        if self.alpaca and self.alpaca.is_configured():
            try:
                alp_equity = self.alpaca.get_portfolio_value()
                alp_cash   = self.alpaca.get_cash()
                alp_pos    = self.alpaca.get_positions()
                equity += alp_equity

                ctx_parts.append(json.dumps({
                    "platform":    "Alpaca (stocks/ETFs)",
                    "equity_usd":  round(alp_equity, 2),
                    "cash_usd":    round(alp_cash, 2),
                    "positions": [
                        {
                            "symbol":  p["symbol"],
                            "qty":     p.get("qty", 0),
                            "value":   round(p.get("market_value", 0), 2),
                            "pnl_pct": round(p.get("unrealized_plpc", 0) * 100, 2),
                        }
                        for p in alp_pos
                    ],
                }, indent=2))
            except Exception as e:
                ctx_parts.append(f"Alpaca data unavailable: {e}")

        # Pull live Fear & Greed
        try:
            import requests as _req
            fg = _req.get(
                "https://api.alternative.me/fng/?limit=1&format=json", timeout=5
            ).json()["data"][0]
            ctx_parts.append(f"Fear & Greed Index: {fg['value']} ({fg['value_classification']})")
        except Exception:
            pass

        market_context = "\n\n".join(ctx_parts) or "No live data"

        # ── Query all AI providers ─────────────────────────────────────
        result = run_multi_ai_analysis(
            market_context=market_context,
            tickers=[
                "BTC", "ETH", "SOL", "DOGE", "ADA",
                "NVDA", "MSFT", "AAPL", "GOOGL", "META", "TSLA", "AMD",
                "SPY", "QQQ", "JEPI", "SCHD", "SGOV",
            ],
        )
        self.last_result = result

        providers_used   = result.get("providers_used", [])
        providers_failed = result.get("providers_failed", [])
        self._log(
            f"Providers used: {providers_used}  |  "
            f"Failed/unconfigured: {providers_failed}"
        )

        if "error" in result and not result.get("signals"):
            self._log(f"Multi-AI error: {result['error']}", "WARN")
            return actions

        # ── Log per-provider summaries ─────────────────────────────────
        for provider, summary in result.get("market_summaries", {}).items():
            if summary:
                self._log(f"  [{provider}] {summary[:100]}")

        consensus_risk = result.get("consensus_risk", "medium")
        self._log(f"Consensus risk level: {consensus_risk.upper()}")

        # ── Process consensus signals ──────────────────────────────────
        for sig in result.get("signals", []):
            ticker    = sig["ticker"]
            action    = sig["action"]
            conf      = sig["confidence"]
            agreement = sig["agreement"]
            strength  = sig["strength"]
            rationale = sig["rationale"]
            n_agree   = sig["providers_agree"]
            n_total   = sig["providers_total"]

            self._log(
                f"  {strength} {action} {ticker:6} "
                f"conf={conf:.0%} agreement={agreement:.0%} ({n_agree}/{n_total}) "
                f"— {rationale[:70]}",
                "TRADE" if strength == "STRONG" else "INFO",
            )

            should_execute = (
                conf      >= MIN_CONFIDENCE_TO_EXECUTE and
                agreement >= MIN_AGREEMENT_TO_EXECUTE  and
                action    != "HOLD"
            )

            is_crypto = ticker in CRYPTO_TICKERS

            if should_execute and action == "BUY":
                if is_crypto and self.rh and self.rh.is_configured() and cash > 50:
                    pair     = CRYPTO_TICKERS[ticker]
                    notional = min(equity * 0.05, cash * 0.25)
                    quote    = self.rh.get_quote(pair)
                    price    = float(quote.get("price", 0))
                    if price and notional >= 10:
                        qty = round(notional / price, 8)
                        self._log(f"  AUTO-EXECUTE BUY {pair} ${notional:.0f}", "TRADE")
                        order = self.rh.buy_market(pair, qty)
                        actions.append({
                            "symbol": pair, "action": "BUY",
                            "quantity": qty, "price": price, "notional": notional,
                            "confidence": conf, "agreement": agreement,
                            "providers": f"{n_agree}/{n_total} AIs agreed",
                            "reason": rationale,
                            "order_id": order.get("id"),
                        })

                elif not is_crypto and self.alpaca and self.alpaca.is_configured():
                    if self.alpaca.is_market_open():
                        alp_equity = self.alpaca.get_portfolio_value()
                        alp_cash   = self.alpaca.get_cash()
                        notional   = min(alp_equity * 0.05, alp_cash * 0.25)
                        if notional >= 10:
                            self._log(f"  AUTO-EXECUTE BUY {ticker} ${notional:.0f}", "TRADE")
                            order = self.alpaca.buy_market(ticker, notional=notional)
                            actions.append({
                                "symbol": ticker, "action": "BUY", "notional": notional,
                                "confidence": conf, "agreement": agreement,
                                "providers": f"{n_agree}/{n_total} AIs agreed",
                                "reason": rationale,
                                "order_id": order.get("id"),
                            })

            elif should_execute and action == "SELL":
                if is_crypto and self.rh and self.rh.is_configured():
                    holdings = {h["symbol"]: h for h in self.rh.get_holdings()}
                    if ticker in holdings:
                        h   = holdings[ticker]
                        qty = round(h["quantity"] * 0.5, 8)
                        self._log(f"  AUTO-EXECUTE SELL 50% {ticker}", "TRADE")
                        pair  = CRYPTO_TICKERS[ticker]
                        order = self.rh.sell_market(pair, qty)
                        actions.append({
                            "symbol": pair, "action": "SELL", "quantity": qty,
                            "confidence": conf, "agreement": agreement,
                            "providers": f"{n_agree}/{n_total} AIs agreed",
                            "reason": rationale,
                            "order_id": order.get("id"),
                        })

                elif not is_crypto and self.alpaca and self.alpaca.is_configured():
                    positions = {p["symbol"]: p for p in self.alpaca.get_positions()}
                    if ticker in positions:
                        pos      = positions[ticker]
                        sell_qty = round(float(pos.get("qty", 0)) * 0.5, 8)
                        if sell_qty > 0:
                            self._log(f"  AUTO-EXECUTE SELL 50% {ticker}", "TRADE")
                            order = self.alpaca.sell_market(ticker, sell_qty)
                            actions.append({
                                "symbol": ticker, "action": "SELL", "quantity": sell_qty,
                                "confidence": conf, "agreement": agreement,
                                "providers": f"{n_agree}/{n_total} AIs agreed",
                                "reason": rationale,
                                "order_id": order.get("id"),
                            })

            else:
                # Log but don't execute (low agreement or HOLD)
                actions.append({
                    "symbol": ticker, "action": action,
                    "confidence": conf, "agreement": agreement,
                    "providers": f"{n_agree}/{n_total} AIs",
                    "reason": rationale,
                    "auto_executed": False,
                })

        self._log(
            f"Multi-AI complete. {len(result.get('signals',[]))} consensus signal(s), "
            f"{sum(1 for a in actions if a.get('order_id'))} auto-executed."
        )
        return actions
