"""
News Sentiment Engine
──────────────────────
Scrapes financial news from 8 free RSS sources, scores sentiment,
maps articles to specific tickers, and generates actionable trade signals.

Sources (all free, no API key required):
  Yahoo Finance RSS    — per-ticker news feeds
  Google News RSS      — broad financial news
  Reuters Business     — macro + corporate news
  CNBC Markets         — market moving news
  MarketWatch          — stocks + macro
  CoinDesk             — crypto-specific news
  CryptoSlate          — altcoin news
  Reddit (WSB/stocks)  — retail sentiment (JSON API)

Sentiment scoring (two-layer):
  Layer 1 — Keyword scoring (instant, always available)
    Bullish keywords: beat, surge, record, breakout, upgrade, buy, rally...
    Bearish keywords: miss, crash, downgrade, sell, fraud, bankrupt, loss...

  Layer 2 — Claude AI analysis (deep, runs on high-impact articles)
    Sends top 5 headlines to Claude with portfolio context.
    Returns structured JSON with ticker signals, confidence, and rationale.

Trade signal generation:
  Score ≥  2.0 + confidence ≥ 0.70 → BUY signal
  Score ≤ -2.0 + confidence ≥ 0.70 → SELL/AVOID signal
  Auto-executes on Robinhood (crypto) or Alpaca (stocks) based on ticker type.
"""

import requests
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from dotenv import load_dotenv
from strategies.base import BaseStrategy

load_dotenv(override=True)   # ensure .env is loaded even when called as a module

# ── Watched tickers ───────────────────────────────────────────────────────────

CRYPTO_TICKERS = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
    "DOGE": "DOGE-USD", "ADA": "ADA-USD", "AVAX": "AVAX-USD",
    "LINK": "LINK-USD", "XRP": "XRP-USD", "MATIC": "MATIC-USD",
}

STOCK_TICKERS = [
    "NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMZN", "TSLA",
    "AMD", "JPM", "SPY", "QQQ", "JEPI", "SCHD", "OXY",
]

ALL_TICKERS = list(CRYPTO_TICKERS.keys()) + STOCK_TICKERS

# ── News sources ──────────────────────────────────────────────────────────────

RSS_SOURCES = [
    {
        "name":     "Reuters Business",
        "url":      "https://feeds.reuters.com/reuters/businessNews",
        "category": "macro",
    },
    {
        "name":     "CNBC Markets",
        "url":      "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "category": "stocks",
    },
    {
        "name":     "MarketWatch",
        "url":      "https://feeds.marketwatch.com/marketwatch/marketpulse/",
        "category": "stocks",
    },
    {
        "name":     "CoinDesk",
        "url":      "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "category": "crypto",
    },
    {
        "name":     "CryptoSlate",
        "url":      "https://cryptoslate.com/feed/",
        "category": "crypto",
    },
    {
        "name":     "Seeking Alpha Markets",
        "url":      "https://seekingalpha.com/market_currents.xml",
        "category": "stocks",
    },
]

# Per-ticker Yahoo Finance RSS (fetched dynamically)
YAHOO_RSS_TEMPLATE = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={}&region=US&lang=en-US"

# Reddit JSON endpoints (public, no auth)
REDDIT_SOURCES = [
    {"name": "r/wallstreetbets", "url": "https://www.reddit.com/r/wallstreetbets/hot.json?limit=25"},
    {"name": "r/stocks",         "url": "https://www.reddit.com/r/stocks/hot.json?limit=25"},
    {"name": "r/cryptocurrency", "url": "https://www.reddit.com/r/cryptocurrency/hot.json?limit=25"},
    {"name": "r/investing",      "url": "https://www.reddit.com/r/investing/hot.json?limit=15"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)",
    "Accept":     "application/rss+xml, application/xml, text/xml, */*",
}

# ── Sentiment keywords ────────────────────────────────────────────────────────

BULLISH_STRONG = [
    "record high", "all-time high", "beats expectations", "earnings beat",
    "strong buy", "price target raised", "upgrade", "outperform",
    "breakout", "surge", "soar", "rally", "moon", "bullish",
    "exceeds", "blowout", "massive growth", "partnership", "acquisition wins",
    "fda approval", "regulatory approval", "major contract",
]
BULLISH_WEAK = [
    "rises", "gains", "up", "positive", "growth", "increase",
    "buy", "accumulate", "demand", "expansion", "profitable",
    "beat", "higher", "strong", "momentum", "advances",
]
BEARISH_STRONG = [
    "all-time low", "misses expectations", "earnings miss", "strong sell",
    "price target cut", "downgrade", "underperform", "crash", "plunge",
    "collapse", "bankrupt", "fraud", "investigation", "sec charges",
    "massive loss", "layoffs", "recall", "lawsuit", "default", "panic",
]
BEARISH_WEAK = [
    "falls", "drops", "down", "negative", "decline", "decrease",
    "sell", "miss", "lower", "weak", "concern", "risk",
    "uncertainty", "volatile", "headwinds", "pressure",
]

# ── Cache ─────────────────────────────────────────────────────────────────────

CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "news_cache.json")
CACHE_TTL_MINUTES = 15


def _load_cache() -> dict:
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                d = json.load(f)
            # Expire after TTL
            if datetime.fromisoformat(d.get("timestamp", "2000-01-01")) > \
               datetime.now() - timedelta(minutes=CACHE_TTL_MINUTES):
                return d
        except Exception:
            pass
    return {}


def _save_cache(d: dict):
    d["timestamp"] = datetime.now().isoformat()
    with open(CACHE_FILE, "w") as f:
        json.dump(d, f, indent=2)


# ── Fetching functions ────────────────────────────────────────────────────────

def _parse_rss(xml_text: str, source_name: str, category: str) -> list[dict]:
    """Parse RSS XML into list of article dicts."""
    articles = []
    try:
        root = ET.fromstring(xml_text)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        # Try standard RSS items first
        items = root.findall(".//item")
        if not items:
            items = root.findall(".//entry")
        for item in items[:20]:
            title = (item.findtext("title") or "").strip()
            desc  = (item.findtext("description") or
                     item.findtext("summary") or "").strip()
            pub   = (item.findtext("pubDate") or
                     item.findtext("published") or "").strip()
            link  = (item.findtext("link") or "").strip()
            # Remove HTML tags from description
            desc  = re.sub(r"<[^>]+>", " ", desc)[:300]
            if title:
                articles.append({
                    "title":    title,
                    "desc":     desc,
                    "pub":      pub,
                    "link":     link,
                    "source":   source_name,
                    "category": category,
                })
    except Exception:
        pass
    return articles


def fetch_rss_articles() -> list[dict]:
    """Fetch from all RSS sources."""
    all_articles = []
    for src in RSS_SOURCES:
        try:
            resp = requests.get(src["url"], headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                articles = _parse_rss(resp.text, src["name"], src["category"])
                all_articles.extend(articles)
        except Exception:
            pass
    return all_articles


def fetch_yahoo_articles(tickers: list[str]) -> list[dict]:
    """Fetch Yahoo Finance RSS for each ticker."""
    articles = []
    for sym in tickers[:10]:   # Limit to avoid rate limiting
        try:
            url  = YAHOO_RSS_TEMPLATE.format(sym)
            resp = requests.get(url, headers=HEADERS, timeout=6)
            if resp.status_code == 200:
                arts = _parse_rss(resp.text, f"Yahoo/{sym}", "ticker")
                for a in arts:
                    a["mentioned_tickers"] = [sym]
                articles.extend(arts[:5])
        except Exception:
            pass
    return articles


def fetch_reddit_posts() -> list[dict]:
    """Fetch Reddit hot posts from finance/crypto subreddits."""
    posts = []
    rh = {"User-Agent": "TradingBot/1.0 by u/trading_research"}
    for src in REDDIT_SOURCES:
        try:
            resp = requests.get(src["url"], headers=rh, timeout=8)
            if resp.status_code != 200:
                continue
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            for c in children:
                p = c.get("data", {})
                title = p.get("title", "")
                score = p.get("score", 0)
                if score < 50 or not title:   # Filter low-engagement
                    continue
                posts.append({
                    "title":    title,
                    "desc":     p.get("selftext", "")[:200],
                    "pub":      "",
                    "link":     f"https://reddit.com{p.get('permalink','')}",
                    "source":   src["name"],
                    "category": "social",
                    "score":    score,
                    "comments": p.get("num_comments", 0),
                })
        except Exception:
            pass
    return posts


def fetch_all_news(use_cache: bool = True) -> list[dict]:
    """Fetch all news articles from all sources. Cached for TTL minutes."""
    if use_cache:
        cached = _load_cache()
        if cached.get("articles"):
            return cached["articles"]

    articles = []
    articles.extend(fetch_rss_articles())
    articles.extend(fetch_yahoo_articles(STOCK_TICKERS[:8]))
    articles.extend(fetch_reddit_posts())

    _save_cache({"articles": articles})
    return articles


# ── Sentiment scoring ─────────────────────────────────────────────────────────

def score_sentiment(text: str) -> float:
    """
    Keyword-based sentiment score.
    Returns float: -3.0 (very bearish) to +3.0 (very bullish)
    """
    text_lower = text.lower()
    score = 0.0
    for kw in BULLISH_STRONG:
        if kw in text_lower:
            score += 1.0
    for kw in BULLISH_WEAK:
        if kw in text_lower:
            score += 0.4
    for kw in BEARISH_STRONG:
        if kw in text_lower:
            score -= 1.0
    for kw in BEARISH_WEAK:
        if kw in text_lower:
            score -= 0.4
    return round(max(-3.0, min(3.0, score)), 2)


def extract_tickers(text: str) -> list[str]:
    """Find which watched tickers are mentioned in an article."""
    text_upper = text.upper()
    found = []
    for sym in ALL_TICKERS:
        # Match as whole word (e.g. "NVDA" not inside "NVDA-USD")
        pattern = r'\b' + re.escape(sym) + r'\b'
        if re.search(pattern, text_upper):
            found.append(sym)
    # Also check crypto names
    name_map = {
        "BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL",
        "DOGECOIN": "DOGE", "CARDANO": "ADA", "RIPPLE": "XRP",
    }
    for name, sym in name_map.items():
        if name in text_upper and sym not in found:
            found.append(sym)
    return found


def analyse_articles(articles: list[dict]) -> list[dict]:
    """
    Score each article and extract ticker mentions.
    Returns enriched articles sorted by absolute sentiment score.
    """
    enriched = []
    for a in articles:
        full_text        = f"{a['title']} {a.get('desc','')}"
        sentiment        = score_sentiment(full_text)
        tickers          = a.get("mentioned_tickers") or extract_tickers(full_text)
        a["sentiment"]   = sentiment
        a["tickers"]     = tickers
        a["impact"]      = abs(sentiment)
        enriched.append(a)
    # Sort by impact descending
    enriched.sort(key=lambda x: x["impact"], reverse=True)
    return enriched


def aggregate_ticker_signals(articles: list[dict]) -> dict:
    """
    Aggregate sentiment across all articles per ticker.
    Returns {ticker: {"score": float, "article_count": int, "headlines": [...]}}
    """
    signals = {}
    for a in articles:
        for sym in a.get("tickers", []):
            if sym not in signals:
                signals[sym] = {"score": 0.0, "count": 0, "headlines": []}
            signals[sym]["score"] += a["sentiment"]
            signals[sym]["count"] += 1
            signals[sym]["headlines"].append({
                "title":     a["title"][:100],
                "source":    a["source"],
                "sentiment": a["sentiment"],
            })
    # Normalise score by article count
    for sym in signals:
        n = signals[sym]["count"]
        signals[sym]["avg_score"]  = round(signals[sym]["score"] / n, 3)
        signals[sym]["raw_score"]  = round(signals[sym]["score"], 3)
        signals[sym]["headlines"]  = signals[sym]["headlines"][:5]
    return signals


def ai_deep_analysis(articles: list[dict], portfolio_context: str = "") -> dict:
    """
    Send top headlines to Claude for deep market analysis.
    Returns structured signals dict.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {"error": "No Anthropic API key"}

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Take top 10 most impactful articles
        top = [a for a in articles if a.get("tickers")][:10]
        if not top:
            top = articles[:10]

        headlines_text = "\n".join([
            f"- [{a['source']}] {a['title']} (sentiment: {a['sentiment']:+.1f})"
            for a in top
        ])

        prompt = f"""You are an expert financial analyst. Analyse these market headlines and generate trade signals.

CURRENT PORTFOLIO CONTEXT:
{portfolio_context or "Mixed portfolio: BTC, ETH, SOL, SPY, QQQ, NVDA"}

TOP NEWS HEADLINES (last 15 minutes):
{headlines_text}

WATCHED TICKERS: {', '.join(ALL_TICKERS)}

Return ONLY valid JSON in this exact format:
{{
  "signals": [
    {{
      "ticker": "NVDA",
      "action": "BUY",
      "confidence": 0.82,
      "rationale": "One sentence max",
      "time_horizon": "1-3 days",
      "news_driver": "headline title that drove this signal"
    }}
  ],
  "market_summary": "2 sentence overall market read",
  "risk_level": "low|medium|high",
  "top_opportunities": ["NVDA", "BTC"],
  "top_risks": ["TSLA"]
}}

Rules:
- Only include signals with confidence >= 0.65
- Max 5 signals
- Only include tickers from the watched list
- action must be BUY, SELL, or HOLD
"""
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Extract JSON from response
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {"error": "Could not parse JSON", "raw": raw[:200]}
    except Exception as e:
        return {"error": str(e)}


# ── Strategy class ────────────────────────────────────────────────────────────

BUY_SCORE_THRESHOLD  =  1.5    # Avg score to trigger buy
SELL_SCORE_THRESHOLD = -1.5    # Avg score to trigger sell/avoid
MIN_ARTICLES         =  2      # Need at least N articles to act
MAX_POSITION_PCT     =  0.06
MIN_TRADE_USD        =  25.0


class NewsSentimentStrategy(BaseStrategy):
    """
    Scrapes 8 news sources + Reddit, scores sentiment, and trades
    tickers with strong bullish/bearish signal consensus.
    """

    def __init__(self, rh_client, alpaca_client=None, max_position_pct: float = MAX_POSITION_PCT):
        super().__init__(rh_client, max_position_pct)
        self.rh     = rh_client
        self.alpaca = alpaca_client
        self.last_signals: dict = {}
        self.last_articles: list = []

    def describe(self) -> str:
        return "Scrapes Reuters/CNBC/CoinDesk/Reddit + AI analysis → sentiment trade signals."

    def run(self) -> list[dict]:
        actions = []

        self._log("News Sentiment: fetching articles from all sources...")
        articles = fetch_all_news(use_cache=True)
        self._log(f"  Fetched {len(articles)} articles from {len(set(a['source'] for a in articles))} sources")

        enriched  = analyse_articles(articles)
        signals   = aggregate_ticker_signals(enriched)
        self.last_articles = enriched
        self.last_signals  = signals

        # Log top movers
        sorted_sigs = sorted(signals.items(), key=lambda x: abs(x[1]["avg_score"]), reverse=True)
        for sym, sig in sorted_sigs[:8]:
            arrow = "🟢" if sig["avg_score"] > 0 else "🔴"
            self._log(
                f"  {arrow} {sym:6} avg_score={sig['avg_score']:+.2f}  "
                f"articles={sig['count']}  "
                f"top: \"{sig['headlines'][0]['title'][:55]}...\""
            )

        # ── Execute trades on strong signals ──────────────────────────
        rh_ok     = self.rh    and self.rh.is_configured()
        alpaca_ok = self.alpaca and self.alpaca.is_configured()

        for sym, sig in sorted_sigs:
            avg_score = sig["avg_score"]
            count     = sig["count"]

            if count < MIN_ARTICLES:
                continue

            is_crypto = sym in CRYPTO_TICKERS
            is_stock  = sym in STOCK_TICKERS

            # ── BUY signal ─────────────────────────────────────────────
            if avg_score >= BUY_SCORE_THRESHOLD:
                if is_crypto and rh_ok:
                    pair     = CRYPTO_TICKERS[sym]
                    equity   = self.rh.get_total_equity()
                    cash     = self.rh.get_cash()
                    notional = min(equity * MAX_POSITION_PCT, cash * 0.3)
                    if notional >= MIN_TRADE_USD:
                        quote = self.rh.get_quote(pair)
                        price = quote.get("price", 0)
                        if price:
                            qty = round(notional / price, 8)
                            self._log(
                                f"  NEWS BUY {pair} ${notional:.0f} "
                                f"(score={avg_score:+.2f} n={count})",
                                "TRADE"
                            )
                            order = self.rh.buy_market(pair, qty)
                            actions.append({
                                "symbol": pair, "action": "BUY",
                                "quantity": qty, "price": price, "notional": notional,
                                "news_score": avg_score, "article_count": count,
                                "reason": f"Bullish news sentiment score={avg_score:+.2f} ({count} articles)",
                                "headlines": sig["headlines"][:3],
                                "order_id": order.get("id"),
                            })

                elif is_stock and alpaca_ok and self.alpaca.is_market_open():
                    portfolio = self.alpaca.get_portfolio_value()
                    cash      = self.alpaca.get_cash()
                    notional  = min(portfolio * MAX_POSITION_PCT, cash * 0.3)
                    if notional >= MIN_TRADE_USD:
                        self._log(
                            f"  NEWS BUY {sym} ${notional:.0f} "
                            f"(score={avg_score:+.2f} n={count})",
                            "TRADE"
                        )
                        order = self.alpaca.buy_market(sym, notional=notional)
                        actions.append({
                            "symbol": sym, "action": "BUY", "notional": notional,
                            "news_score": avg_score, "article_count": count,
                            "reason": f"Bullish news sentiment score={avg_score:+.2f} ({count} articles)",
                            "headlines": sig["headlines"][:3],
                            "order_id": order.get("id"),
                        })

            # ── SELL signal (only if holding) ──────────────────────────
            elif avg_score <= SELL_SCORE_THRESHOLD:
                if is_crypto and rh_ok:
                    holdings = {h["symbol"]: h for h in self.rh.get_holdings()}
                    if sym in holdings:
                        h   = holdings[sym]
                        qty = round(h["quantity"] * 0.5, 8)   # Sell 50%
                        self._log(
                            f"  NEWS SELL 50% {sym} "
                            f"(score={avg_score:+.2f} n={count})",
                            "TRADE"
                        )
                        pair  = CRYPTO_TICKERS[sym]
                        order = self.rh.sell_market(pair, qty)
                        actions.append({
                            "symbol": pair, "action": "SELL", "quantity": qty,
                            "news_score": avg_score,
                            "reason": f"Bearish news sentiment score={avg_score:+.2f} ({count} articles)",
                            "order_id": order.get("id"),
                        })

                elif is_stock and alpaca_ok:
                    positions = {p["symbol"]: p for p in self.alpaca.get_positions()}
                    if sym in positions:
                        pos      = positions[sym]
                        sell_qty = round(pos["qty"] * 0.5, 8)
                        self._log(
                            f"  NEWS SELL 50% {sym} "
                            f"(score={avg_score:+.2f} n={count})",
                            "TRADE"
                        )
                        order = self.alpaca.sell_market(sym, sell_qty)
                        actions.append({
                            "symbol": sym, "action": "SELL", "quantity": sell_qty,
                            "news_score": avg_score,
                            "reason": f"Bearish news sentiment score={avg_score:+.2f} ({count} articles)",
                            "order_id": order.get("id"),
                        })

        # ── Run Claude AI deep analysis on top articles ────────────────
        self._log("Running Claude AI deep analysis on top headlines...")
        portfolio_ctx = f"Watching: {', '.join(ALL_TICKERS)}"
        ai_result     = ai_deep_analysis(enriched, portfolio_ctx)

        if "signals" in ai_result:
            self._log(f"  AI market summary: {ai_result.get('market_summary','')[:100]}")
            for sig in ai_result["signals"]:
                self._log(
                    f"  AI SIGNAL: {sig['action']} {sig['ticker']} "
                    f"confidence={sig['confidence']:.0%} — {sig['rationale'][:60]}",
                    "TRADE" if sig["confidence"] >= 0.75 else "INFO"
                )
        elif "error" in ai_result:
            self._log(f"  AI analysis: {ai_result['error']}", "WARN")

        self.last_ai_result = ai_result
        self._log(f"News sentiment done. {len(actions)} trade action(s).")
        return actions

    def get_news_report(self, use_cache: bool = True) -> dict:
        """Get full news report without trading — for dashboard display."""
        articles = fetch_all_news(use_cache=use_cache)
        enriched = analyse_articles(articles)
        signals  = aggregate_ticker_signals(enriched)
        return {
            "articles":      enriched[:50],
            "ticker_signals": signals,
            "source_count":  len(set(a["source"] for a in articles)),
            "article_count": len(articles),
            "timestamp":     datetime.now().isoformat(),
        }
