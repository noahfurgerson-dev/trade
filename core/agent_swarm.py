"""
Autonomous Agent Swarm
───────────────────────
Four specialized AI agents working in parallel, each focused on a distinct
analytical domain.  Their signals are aggregated into a final consensus.

Agents:
  SentimentAgent  — Interprets news, social media, and Fear & Greed mood
  TechnicalsAgent — Reads chart patterns, indicators, momentum signals
  RiskAgent       — Evaluates portfolio exposure, drawdown risk, position sizing
  MacroAgent      — Assesses macro environment, sector rotation, economic cycle

Each agent receives context specific to their domain, returns structured JSON
with per-ticker signals and a domain verdict.  The swarm aggregator weights
their votes and produces a final STRONG/WEAK BUY/SELL/HOLD per ticker.

Usage:
    from core.agent_swarm import run_swarm
    result = run_swarm(market_context, portfolio_context, tickers)
"""

import json
import os
import re
from datetime import datetime


# ── Agent prompt templates ────────────────────────────────────────────────────

SENTIMENT_SYSTEM = """You are a market sentiment specialist.
Your ONLY job: interpret news headlines, social media mood, and the Fear & Greed
Index to determine whether current sentiment is bullish, bearish, or neutral for
each ticker.  Ignore technicals — pure sentiment only."""

TECHNICALS_SYSTEM = """You are a technical analysis specialist.
Your ONLY job: interpret price action signals — RSI, MACD, Bollinger Bands,
moving averages, volume patterns, and momentum — to identify high-probability
entries and exits.  Ignore news and fundamentals — pure technicals only."""

RISK_SYSTEM = """You are a portfolio risk specialist.
Your ONLY job: assess whether the portfolio has dangerous over-exposure,
identify positions at risk of large drawdown, and recommend position sizing
adjustments.  Focus on risk-adjusted returns, not raw upside."""

MACRO_SYSTEM = """You are a macroeconomic specialist.
Your ONLY job: assess the current economic environment — interest rates, dollar
strength, commodity trends, sector rotation, and global risk appetite — to
identify which asset classes and sectors are best positioned.
Focus on the big picture, not individual ticker moves."""

SIGNAL_JSON_FORMAT = """
Return ONLY valid JSON in this exact format:
{
  "signals": [
    {
      "ticker": "BTC",
      "action": "BUY",
      "confidence": 0.78,
      "rationale": "one sentence max",
      "time_horizon": "1-3 days"
    }
  ],
  "verdict": "one sentence overall summary from your domain perspective",
  "risk_level": "low|medium|high",
  "top_opportunities": ["BTC", "ETH"],
  "top_risks": ["TSLA"]
}
Rules: max 5 signals, confidence 0.0-1.0, only include signals with confidence >= 0.60.
"""


# ── Single agent caller ────────────────────────────────────────────────────────

def _call_agent(
    agent_name: str,
    system_prompt: str,
    user_context: str,
    tickers: list[str],
    api_key: str,
) -> dict:
    """Call one agent with its specialized prompt. Returns parsed signal dict."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = (
            f"DOMAIN CONTEXT:\n{user_context}\n\n"
            f"TICKERS TO ANALYSE: {', '.join(tickers)}\n\n"
            + SIGNAL_JSON_FORMAT
        )

        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            result = json.loads(m.group())
            result["agent"] = agent_name
            return result
        return {"agent": agent_name, "error": "JSON parse failed", "raw": raw[:100]}

    except Exception as e:
        return {"agent": agent_name, "error": str(e)}


# ── Swarm aggregator ──────────────────────────────────────────────────────────

def _aggregate_swarm(agent_results: list[dict]) -> dict:
    """
    Combine signals from all agents via weighted vote.
    Weights: Technicals=1.2, Sentiment=1.0, Risk=0.9, Macro=0.8
    Returns per-ticker consensus + agent summary.
    """
    WEIGHTS = {
        "SentimentAgent":  1.0,
        "TechnicalsAgent": 1.2,
        "RiskAgent":       0.9,
        "MacroAgent":      0.8,
    }

    # Collect all signals per ticker
    ticker_votes: dict[str, list[dict]] = {}
    agent_verdicts: list[str] = []

    for agent_r in agent_results:
        if "error" in agent_r:
            continue
        agent_name = agent_r.get("agent", "Unknown")
        weight     = WEIGHTS.get(agent_name, 1.0)

        for sig in agent_r.get("signals", []):
            ticker = sig.get("ticker", "")
            if not ticker:
                continue
            if ticker not in ticker_votes:
                ticker_votes[ticker] = []
            ticker_votes[ticker].append({
                "action":     sig.get("action", "HOLD"),
                "confidence": sig.get("confidence", 0.5) * weight,
                "agent":      agent_name,
                "rationale":  sig.get("rationale", ""),
            })

        verdict = agent_r.get("verdict", "")
        if verdict:
            agent_verdicts.append(f"[{agent_name}] {verdict}")

    # Compute per-ticker consensus
    consensus = {}
    for ticker, votes in ticker_votes.items():
        buy_score  = sum(v["confidence"] for v in votes if v["action"] == "BUY")
        sell_score = sum(v["confidence"] for v in votes if v["action"] == "SELL")
        hold_score = sum(v["confidence"] for v in votes if v["action"] == "HOLD")
        total      = buy_score + sell_score + hold_score or 1.0

        if buy_score >= sell_score and buy_score / total >= 0.40:
            direction   = "BUY"
            final_score = buy_score / total
        elif sell_score > buy_score and sell_score / total >= 0.40:
            direction   = "SELL"
            final_score = sell_score / total
        else:
            direction   = "HOLD"
            final_score = hold_score / total

        agent_count = len(votes)
        signal_str  = "STRONG" if (agent_count >= 3 and final_score >= 0.55) else "WEAK"

        consensus[ticker] = {
            "ticker":       ticker,
            "action":       direction,
            "strength":     signal_str,
            "score":        round(final_score, 3),
            "agent_count":  agent_count,
            "agents":       votes,
            "buy_score":    round(buy_score, 2),
            "sell_score":   round(sell_score, 2),
        }

    # Overall market verdict
    buy_tickers  = [t for t, c in consensus.items() if c["action"] == "BUY"]
    sell_tickers = [t for t, c in consensus.items() if c["action"] == "SELL"]

    return {
        "consensus":       consensus,
        "agent_verdicts":  agent_verdicts,
        "buy_tickers":     buy_tickers,
        "sell_tickers":    sell_tickers,
        "agents_run":      [r.get("agent") for r in agent_results if "error" not in r],
        "agents_failed":   [r.get("agent") for r in agent_results if "error" in r],
        "timestamp":       datetime.now().isoformat(),
    }


# ── Public API ────────────────────────────────────────────────────────────────

def run_swarm(
    market_context:    str,
    portfolio_context: str,
    tickers:           list[str],
    news_context:      str = "",
    technicals_context: str = "",
) -> dict:
    """
    Run all four specialized agents and aggregate their signals.

    Args:
        market_context:     General market conditions (F&G, hours, equity)
        portfolio_context:  Current holdings, cash, P&L
        tickers:            List of tickers to analyse
        news_context:       Recent headlines (for sentiment agent)
        technicals_context: Indicator values (for technicals agent)

    Returns aggregated swarm result dict.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY not set"}

    # Build domain-specific context for each agent
    sentiment_ctx = (
        f"MARKET CONDITIONS:\n{market_context}\n\n"
        f"RECENT NEWS:\n{news_context or 'No news data available'}\n\n"
        f"PORTFOLIO:\n{portfolio_context}"
    )

    technicals_ctx = (
        f"TECHNICAL INDICATORS:\n{technicals_context or 'No technical data available'}\n\n"
        f"MARKET CONDITIONS:\n{market_context}"
    )

    risk_ctx = (
        f"PORTFOLIO STATE:\n{portfolio_context}\n\n"
        f"MARKET CONDITIONS:\n{market_context}"
    )

    macro_ctx = (
        f"MARKET CONDITIONS:\n{market_context}\n\n"
        f"RECENT HEADLINES (for macro context):\n{news_context or 'No news data'}"
    )

    # Run agents sequentially (parallel would need threading — keep simple for now)
    agents = [
        ("SentimentAgent",  SENTIMENT_SYSTEM,  sentiment_ctx),
        ("TechnicalsAgent", TECHNICALS_SYSTEM, technicals_ctx),
        ("RiskAgent",       RISK_SYSTEM,       risk_ctx),
        ("MacroAgent",      MACRO_SYSTEM,      macro_ctx),
    ]

    results = []
    for name, system, context in agents:
        r = _call_agent(name, system, context, tickers, api_key)
        results.append(r)

    return _aggregate_swarm(results)


def build_technicals_context(tickers: list[str]) -> str:
    """Build a text summary of technical indicators for the TechnicalsAgent."""
    try:
        from core.market_data import get_technicals
        lines = []
        for t in tickers[:12]:  # Cap to avoid context overflow
            tech = get_technicals(t)
            if tech:
                gc = "GoldenX" if tech.get("golden_cross") else "DeathX" if tech.get("golden_cross") is False else "N/A"
                lines.append(
                    f"{t}: RSI={tech.get('rsi','?')}, MACD={tech.get('macd_cross','?')}, "
                    f"BB_pos={tech.get('bb_position','?'):.2f}, "
                    f"Mom5d={tech.get('mom_5d','?')}%, "
                    f"MA={gc}"
                )
        return "\n".join(lines) if lines else "No technical data"
    except Exception as e:
        return f"Technical data unavailable: {e}"
