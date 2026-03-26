"""
Trending Coin Scanner (CoinGecko)
──────────────────────────────────
Fetches CoinGecko's trending coins (top 7 by search volume in 24h).
Cross-references against Robinhood's tradeable crypto pairs.
Buys trending coins with upward price momentum — a simple
"follow-the-crowd" signal that often precedes price spikes.

CoinGecko trending API: https://api.coingecko.com/api/v3/search/trending
No API key required (free tier, 30 calls/min).

Risk: Trending coins can pump AND dump fast.
Position sizes are intentionally small (max 5% per coin).
"""

import requests
from strategies.base import BaseStrategy

COINGECKO_TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
COINGECKO_PRICE_URL    = "https://api.coingecko.com/api/v3/simple/price"

# Robinhood tradeable symbols (symbol → Robinhood pair)
RH_TRADEABLE = {
    "btc":   "BTC-USD",
    "eth":   "ETH-USD",
    "sol":   "SOL-USD",
    "doge":  "DOGE-USD",
    "ada":   "ADA-USD",
    "avax":  "AVAX-USD",
    "link":  "LINK-USD",
    "matic": "MATIC-USD",
    "shib":  "SHIB-USD",
    "ltc":   "LTC-USD",
    "uni":   "UNI-USD",
    "xlm":   "XLM-USD",
    "xrp":   "XRP-USD",
    "dot":   "DOT-USD",
    "atom":  "ATOM-USD",
}

MAX_POSITION_PCT  = 0.05   # Max 5% per trending coin
MIN_NOTIONAL      = 15.0   # Minimum $15 trade
MIN_PRICE_CHANGE  = 0.02   # Only buy if 24h price change > +2%
MAX_TRENDING_BUYS = 2      # Buy at most 2 trending coins per cycle


def fetch_trending() -> list[dict]:
    """Fetch top trending coins from CoinGecko."""
    resp = requests.get(COINGECKO_TRENDING_URL, timeout=8)
    resp.raise_for_status()
    coins = resp.json().get("coins", [])
    return [
        {
            "id":     c["item"]["id"],
            "symbol": c["item"]["symbol"].lower(),
            "name":   c["item"]["name"],
            "rank":   c["item"].get("market_cap_rank", 9999),
            "score":  c["item"].get("score", 0),
        }
        for c in coins
    ]


def fetch_price_changes(coin_ids: list[str]) -> dict:
    """Fetch 24h price changes for given CoinGecko IDs."""
    if not coin_ids:
        return {}
    try:
        ids_str = ",".join(coin_ids)
        resp = requests.get(
            COINGECKO_PRICE_URL,
            params={"ids": ids_str, "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            timeout=8
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


class TrendingScannerStrategy(BaseStrategy):
    """
    Buys trending coins from CoinGecko that are also tradeable on Robinhood
    and are showing positive 24h momentum.
    """

    def __init__(self, client, max_position_pct: float = 0.05):
        super().__init__(client, max_position_pct)
        self.last_trending: list[dict] = []

    def describe(self) -> str:
        return "Buys CoinGecko trending coins with +2% momentum if tradeable on Robinhood."

    def run(self) -> list[dict]:
        actions = []
        self._log("Trending Scanner: fetching CoinGecko trending coins...")

        try:
            trending = fetch_trending()
        except Exception as e:
            self._log(f"CoinGecko fetch failed: {e}", "WARN")
            return actions

        self.last_trending = trending
        self._log(f"Top trending coins: {', '.join(c['symbol'].upper() for c in trending[:7])}")

        # Filter to Robinhood-tradeable
        tradeable = [
            t for t in trending
            if t["symbol"] in RH_TRADEABLE
        ]

        if not tradeable:
            self._log("No trending coins tradeable on Robinhood this cycle.")
            actions.append({"action": "HOLD", "reason": "No tradeable trending coins"})
            return actions

        # Fetch 24h price changes
        coin_ids = [c["id"] for c in tradeable]
        prices   = fetch_price_changes(coin_ids)

        self._log(f"{len(tradeable)} trending coins tradeable on Robinhood — checking momentum...")

        # Get current portfolio state
        holdings = {h["pair"]: h for h in self.client.get_holdings()}
        cash     = self.client.get_cash()
        equity   = self.client.get_total_equity()
        buys     = 0

        for coin in tradeable:
            if buys >= MAX_TRENDING_BUYS:
                self._log("Max trending buys reached for this cycle.")
                break

            pair        = RH_TRADEABLE[coin["symbol"]]
            price_data  = prices.get(coin["id"], {})
            change_24h  = price_data.get("usd_24h_change", 0) or 0
            current_usd = price_data.get("usd", 0) or 0

            status_icon = "📈" if change_24h > 0 else "📉"
            self._log(
                f"  {status_icon} {coin['symbol'].upper():8} ({coin['name'][:20]}) "
                f"24h: {change_24h:+.1f}%  MCap rank: #{coin['rank']}"
            )

            # Skip if already holding
            if pair in holdings:
                self._log(f"  SKIP {pair} — already holding")
                continue

            # Require positive momentum
            if change_24h < MIN_PRICE_CHANGE * 100:
                self._log(f"  SKIP {pair} — momentum insufficient ({change_24h:+.1f}% < {MIN_PRICE_CHANGE*100:.0f}%)")
                continue

            # Size position
            notional = min(equity * self.max_position_pct, cash * 0.5)
            if notional < MIN_NOTIONAL:
                self._log(f"  SKIP {pair} — insufficient cash")
                continue

            # Get live price from Robinhood
            quote = self.client.get_quote(pair)
            price = quote.get("price", current_usd)
            if not price:
                continue

            qty = notional / price
            self._log(
                f"  BUY {qty:.6f} {pair} @ ${price:.6f} (${notional:.2f}) "
                f"— trending #{coin['score']+1} with {change_24h:+.1f}% momentum",
                "TRADE"
            )
            order = self.client.buy_market(pair, qty)
            actions.append({
                "pair":       pair,
                "action":     "BUY",
                "quantity":   qty,
                "price":      price,
                "notional":   notional,
                "reason":     f"CoinGecko trending #{coin['score']+1}, {change_24h:+.1f}% 24h momentum",
                "order_id":   order.get("id"),
                "trending_rank": coin["score"],
            })
            cash -= notional
            buys += 1

        if not actions:
            actions.append({
                "action": "HOLD",
                "reason": "Trending coins lack sufficient momentum",
                "trending": [c["symbol"].upper() for c in tradeable[:5]],
            })

        self._log(f"Trending scanner done. {len(actions)} action(s).")
        return actions

    def get_trending_report(self) -> list[dict]:
        """Return trending data without trading."""
        try:
            trending = fetch_trending()
            coin_ids = [c["id"] for c in trending]
            prices   = fetch_price_changes(coin_ids)
            report   = []
            for coin in trending:
                price_data = prices.get(coin["id"], {})
                report.append({
                    "symbol":     coin["symbol"].upper(),
                    "name":       coin["name"],
                    "rank":       coin["rank"],
                    "change_24h": price_data.get("usd_24h_change", 0),
                    "price_usd":  price_data.get("usd", 0),
                    "tradeable":  coin["symbol"] in RH_TRADEABLE,
                    "rh_pair":    RH_TRADEABLE.get(coin["symbol"]),
                })
            return report
        except Exception as e:
            return [{"error": str(e)}]
