"""
Multi-AI Consensus Engine
──────────────────────────
Queries multiple AI providers with an identical market analysis prompt,
normalises their responses, and aggregates into a consensus signal.

Supported providers (uses whichever keys exist in .env):
  Claude   — claude-sonnet-4-6         (Anthropic)
  GPT-4o   — gpt-4o-mini               (OpenAI)
  Gemini   — gemini-1.5-flash          (Google)
  Groq     — llama-3.3-70b-versatile   (free tier, fast)

Consensus logic:
  1. Each provider votes BUY / SELL / HOLD per ticker with a confidence.
  2. Votes are weighted by confidence.
  3. Final signal = action with the highest weighted vote share.
  4. Agreement score = fraction of providers that agree on the final action.
  5. High-agreement signals (>= 0.67) are flagged as STRONG.

Add keys to .env:
  OPENAI_API_KEY=sk-...
  GOOGLE_API_KEY=AIza...
  GROQ_API_KEY=gsk_...
  ANTHROPIC_API_KEY=sk-ant-...   (already present)
"""

import os
import json
import re
import time
from datetime import datetime
from typing import Optional

# ── Resolve .env ──────────────────────────────────────────────────────────────
_ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_FILE = os.path.join(_ROOT, ".env")
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_ENV_FILE, override=True)
except Exception:
    pass

# ── Shared prompt template ────────────────────────────────────────────────────

_PROMPT_TEMPLATE = """You are an expert financial analyst for an automated trading platform.
Analyse the market snapshot below and return trade signals.

MARKET SNAPSHOT:
{market_context}

WATCHED UNIVERSE: {tickers}

RULES:
- Only include signals with confidence >= 0.65
- Max 6 signals total
- action must be BUY, SELL, or HOLD
- Never suggest >20% allocation to a single asset
- Return ONLY valid JSON — no prose, no markdown fences

REQUIRED JSON FORMAT:
{{
  "signals": [
    {{
      "ticker": "NVDA",
      "action": "BUY",
      "confidence": 0.82,
      "rationale": "one sentence max",
      "suggested_pct": 5
    }}
  ],
  "market_summary": "2 sentence overall market read",
  "risk_level": "low|medium|high",
  "top_opportunities": ["NVDA", "BTC"],
  "top_risks": ["TSLA"]
}}"""

WATCHED_TICKERS = [
    "BTC", "ETH", "SOL", "DOGE", "ADA",
    "NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMZN", "TSLA", "AMD",
    "SPY", "QQQ", "JEPI", "SCHD", "SGOV",
]


def _extract_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON from model output."""
    raw = raw.strip()
    # Remove ```json ... ``` fences
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    # Find the outermost JSON object
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        return json.loads(m.group())
    return json.loads(raw)


# ── Per-provider query functions ──────────────────────────────────────────────

def _query_claude(prompt: str) -> Optional[dict]:
    """Query Anthropic Claude."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_json(msg.content[0].text)
    except Exception as e:
        return {"error": str(e)}


def _query_openai(prompt: str) -> Optional[dict]:
    """Query OpenAI GPT-4o-mini."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=1200,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a financial analyst. Return only valid JSON."},
                {"role": "user",   "content": prompt},
            ],
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        return {"error": str(e)}


def _query_gemini(prompt: str) -> Optional[dict]:
    """Query Google Gemini 1.5 Flash."""
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp  = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=1200,
            ),
        )
        return _extract_json(resp.text)
    except Exception as e:
        return {"error": str(e)}


def _query_groq(prompt: str) -> Optional[dict]:
    """Query Groq (Llama 3.3 70B) — free tier, very fast."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=1200,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a financial analyst. Return only valid JSON."},
                {"role": "user",   "content": prompt},
            ],
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        return {"error": str(e)}


# ── Consensus aggregation ─────────────────────────────────────────────────────

def _aggregate_consensus(provider_results: dict[str, dict]) -> dict:
    """
    Aggregate signals from multiple providers into a consensus.

    Returns:
        {
          "signals": [...],          # consensus signals
          "provider_signals": {...}, # per-provider breakdown
          "agreement_scores": {...}, # per-ticker agreement (0-1)
          "market_summaries": {...}, # per-provider summary
          "consensus_risk": "...",   # majority risk level
          "providers_used": [...],
          "providers_failed": [...],
        }
    """
    # Separate successful from failed
    ok      = {p: r for p, r in provider_results.items() if r and "error" not in r and "signals" in r}
    failed  = [p for p, r in provider_results.items() if not r or "error" in r or "signals" not in r]

    if not ok:
        return {
            "signals": [],
            "provider_signals": {},
            "agreement_scores": {},
            "market_summaries": {},
            "consensus_risk": "unknown",
            "providers_used": [],
            "providers_failed": list(provider_results.keys()),
            "error": "All AI providers failed or unavailable",
        }

    # Accumulate weighted votes per ticker per action
    # votes[ticker][action] = sum of confidence weights
    votes: dict[str, dict[str, float]] = {}
    vote_counts: dict[str, dict[str, int]] = {}

    for provider, result in ok.items():
        for sig in result.get("signals", []):
            ticker  = sig.get("ticker", "").upper()
            action  = sig.get("action", "HOLD").upper()
            conf    = float(sig.get("confidence", 0.5))
            if not ticker or action not in ("BUY", "SELL", "HOLD"):
                continue
            if ticker not in votes:
                votes[ticker]       = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
                vote_counts[ticker] = {"BUY": 0,   "SELL": 0,   "HOLD": 0}
            votes[ticker][action]       += conf
            vote_counts[ticker][action] += 1

    # Build consensus signals
    consensus_signals = []
    agreement_scores  = {}
    n_providers       = len(ok)

    for ticker, action_weights in votes.items():
        total_weight = sum(action_weights.values())
        if total_weight == 0:
            continue

        # Winning action = highest weighted vote
        best_action = max(action_weights, key=lambda a: action_weights[a])
        best_weight = action_weights[best_action]
        consensus_conf = round(best_weight / total_weight, 3)

        # Agreement = fraction of providers that voted for this action
        n_agreeing  = vote_counts[ticker][best_action]
        agreement   = round(n_agreeing / n_providers, 3)
        agreement_scores[ticker] = agreement

        # Only include if consensus confidence meaningful
        if consensus_conf < 0.4:
            continue

        # Gather rationales from agreeing providers
        rationales = []
        for provider, result in ok.items():
            for sig in result.get("signals", []):
                if sig.get("ticker","").upper() == ticker and sig.get("action","").upper() == best_action:
                    rationales.append(f"[{provider}] {sig.get('rationale','')[:60]}")

        consensus_signals.append({
            "ticker":           ticker,
            "action":           best_action,
            "confidence":       consensus_conf,
            "agreement":        agreement,
            "strength":         "STRONG" if agreement >= 0.67 else "MODERATE",
            "providers_agree":  n_agreeing,
            "providers_total":  n_providers,
            "rationale":        " | ".join(rationales[:3]),
            "suggested_pct":    5,   # conservative default
        })

    # Sort by agreement then confidence
    consensus_signals.sort(
        key=lambda x: (x["agreement"], x["confidence"]), reverse=True
    )

    # Consensus risk level = majority vote
    risk_votes = {}
    for result in ok.values():
        rl = result.get("risk_level", "medium").lower()
        risk_votes[rl] = risk_votes.get(rl, 0) + 1
    consensus_risk = max(risk_votes, key=lambda r: risk_votes[r]) if risk_votes else "medium"

    return {
        "signals":          consensus_signals,
        "provider_signals": {p: r.get("signals", [])           for p, r in ok.items()},
        "market_summaries": {p: r.get("market_summary", "")    for p, r in ok.items()},
        "top_opportunities":{p: r.get("top_opportunities", []) for p, r in ok.items()},
        "top_risks":        {p: r.get("top_risks", [])         for p, r in ok.items()},
        "agreement_scores": agreement_scores,
        "consensus_risk":   consensus_risk,
        "providers_used":   list(ok.keys()),
        "providers_failed": failed,
        "timestamp":        datetime.now().isoformat(),
    }


# ── Main public API ───────────────────────────────────────────────────────────

def run_multi_ai_analysis(
    market_context: str = "",
    tickers: list[str] = None,
    timeout_per_provider: float = 20.0,
) -> dict:
    """
    Query all available AI providers and return a consensus signal dict.

    Args:
        market_context: JSON string or free-text snapshot of portfolio + prices
        tickers:        List of ticker symbols to watch
        timeout_per_provider: Seconds to wait per provider (parallel not used
                              due to API rate limits — sequential with timeout)

    Returns: consensus dict (see _aggregate_consensus docstring)
    """
    if tickers is None:
        tickers = WATCHED_TICKERS

    prompt = _PROMPT_TEMPLATE.format(
        market_context=market_context or "No live data available — use general knowledge.",
        tickers=", ".join(tickers),
    )

    providers = {
        "Claude":  _query_claude,
        "GPT-4o":  _query_openai,
        "Gemini":  _query_gemini,
        "Groq":    _query_groq,
    }

    results = {}
    available = 0
    for name, fn in providers.items():
        # Skip if key not present (avoids wasting time)
        key_map = {
            "Claude": "ANTHROPIC_API_KEY",
            "GPT-4o": "OPENAI_API_KEY",
            "Gemini": "GOOGLE_API_KEY",
            "Groq":   "GROQ_API_KEY",
        }
        if not os.getenv(key_map[name], "").strip():
            results[name] = {"error": "API key not configured"}
            continue

        available += 1
        t0 = time.time()
        try:
            results[name] = fn(prompt)
        except Exception as e:
            results[name] = {"error": str(e)}
        elapsed = time.time() - t0

        status = "OK" if results[name] and "error" not in results[name] else f"FAIL: {results[name].get('error','')[:60]}"
        print(f"[MultiAI] {name:8} {elapsed:.1f}s  {status}")

    if available == 0:
        return {
            "signals": [],
            "error":   "No AI providers configured. Add at least one key to .env.",
            "providers_used": [],
            "providers_failed": list(providers.keys()),
        }

    return _aggregate_consensus(results)


def get_provider_status() -> list[dict]:
    """
    Return which AI providers are configured (for dashboard display).
    Does NOT make any API calls.
    """
    key_map = {
        "Claude (Anthropic)":  "ANTHROPIC_API_KEY",
        "GPT-4o (OpenAI)":    "OPENAI_API_KEY",
        "Gemini (Google)":    "GOOGLE_API_KEY",
        "Groq (Llama 3.3)":   "GROQ_API_KEY",
    }
    return [
        {
            "provider":    name,
            "configured":  bool(os.getenv(key, "").strip()),
            "env_key":     key,
        }
        for name, key in key_map.items()
    ]
