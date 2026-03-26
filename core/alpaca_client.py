"""
Alpaca Markets Client
──────────────────────
Stock & ETF trading via the Alpaca Markets API.
Supports both paper trading (free) and live trading.

Get free API keys at: https://alpaca.markets
Paper trading: https://paper-api.alpaca.markets  (no real money)
Live trading:  https://api.alpaca.markets

Set in .env:
    ALPACA_API_KEY=PKxxxxxxxxxxxxxxxxxxxx
    ALPACA_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    ALPACA_PAPER=true   # set to false for live trading
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)


class AlpacaClient:

    def __init__(self):
        self.api_key    = os.getenv("ALPACA_API_KEY", "").strip()
        self.api_secret = os.getenv("ALPACA_API_SECRET", "").strip()
        paper           = os.getenv("ALPACA_PAPER", "true").strip().lower()
        self.is_paper   = paper != "false"

        base = "https://paper-api.alpaca.markets" if self.is_paper else "https://api.alpaca.markets"
        self.base_url   = base + "/v2"
        self.data_url   = "https://data.alpaca.markets/v2"

        self._session = requests.Session()
        self._session.headers.update({
            "APCA-API-KEY-ID"    : self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type"       : "application/json",
        })

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self._session.get(self.base_url + path, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = self._session.post(self.base_url + path, json=body, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> dict:
        resp = self._session.delete(self.base_url + path, timeout=10)
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    # ── Account ────────────────────────────────────────────────────

    def get_account(self) -> dict:
        try:
            return self._get("/account")
        except Exception as e:
            return {"error": str(e)}

    def get_cash(self) -> float:
        acct = self.get_account()
        return float(acct.get("cash", 0) or 0)

    def get_portfolio_value(self) -> float:
        acct = self.get_account()
        return float(acct.get("portfolio_value", 0) or 0)

    def get_buying_power(self) -> float:
        acct = self.get_account()
        return float(acct.get("buying_power", 0) or 0)

    # ── Positions ──────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        try:
            raw = self._get("/positions")
            return [
                {
                    "symbol"        : p["symbol"],
                    "qty"           : float(p["qty"]),
                    "avg_cost"      : float(p["avg_entry_price"]),
                    "current_price" : float(p["current_price"]),
                    "market_value"  : float(p["market_value"]),
                    "unrealized_pnl": float(p["unrealized_pl"]),
                    "pnl_pct"       : float(p["unrealized_plpc"]) * 100,
                }
                for p in raw
            ]
        except Exception as e:
            print(f"Alpaca positions error: {e}")
            return []

    # ── Orders ─────────────────────────────────────────────────────

    def buy_market(self, symbol: str, qty: float = None, notional: float = None) -> dict:
        body = {"symbol": symbol, "side": "buy", "type": "market", "time_in_force": "day"}
        if notional:
            body["notional"] = str(round(notional, 2))  # USD amount
        elif qty:
            body["qty"] = str(qty)
        try:
            return self._post("/orders", body)
        except Exception as e:
            return {"error": str(e)}

    def sell_market(self, symbol: str, qty: float) -> dict:
        body = {"symbol": symbol, "side": "sell", "type": "market",
                "time_in_force": "day", "qty": str(qty)}
        try:
            return self._post("/orders", body)
        except Exception as e:
            return {"error": str(e)}

    def get_orders(self, limit: int = 20) -> list[dict]:
        try:
            raw = self._get("/orders", {"limit": limit, "status": "all"})
            return [
                {
                    "id"        : o["id"],
                    "symbol"    : o["symbol"],
                    "side"      : o["side"],
                    "type"      : o["type"],
                    "qty"       : float(o.get("qty") or 0),
                    "filled_qty": float(o.get("filled_qty") or 0),
                    "avg_price" : float(o.get("filled_avg_price") or 0),
                    "status"    : o["status"],
                    "created_at": o["created_at"],
                }
                for o in raw
            ]
        except Exception as e:
            return []

    # ── Market Data ────────────────────────────────────────────────

    def get_quote(self, symbol: str) -> dict:
        try:
            resp = self._session.get(
                self.data_url + f"/stocks/{symbol}/quotes/latest",
                timeout=10
            )
            resp.raise_for_status()
            q = resp.json().get("quote", {})
            return {
                "symbol": symbol,
                "bid"   : float(q.get("bp", 0)),
                "ask"   : float(q.get("ap", 0)),
                "price" : (float(q.get("bp", 0)) + float(q.get("ap", 0))) / 2,
            }
        except Exception as e:
            return {"symbol": symbol, "error": str(e)}

    def get_latest_bar(self, symbol: str) -> dict:
        try:
            resp = self._session.get(
                self.data_url + f"/stocks/{symbol}/bars/latest",
                timeout=10
            )
            resp.raise_for_status()
            bar = resp.json().get("bar", {})
            return {
                "symbol": symbol,
                "close" : float(bar.get("c", 0)),
                "open"  : float(bar.get("o", 0)),
                "high"  : float(bar.get("h", 0)),
                "low"   : float(bar.get("l", 0)),
                "volume": int(bar.get("v", 0)),
            }
        except Exception as e:
            return {"symbol": symbol, "error": str(e)}

    def is_market_open(self) -> bool:
        try:
            clock = self._get("/clock")
            return clock.get("is_open", False)
        except Exception:
            return False
