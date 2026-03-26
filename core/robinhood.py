"""
Robinhood Official Crypto Trading API Client
https://docs.robinhood.com/crypto/trading/

Auth: Ed25519 key-pair signing (nacl/PyNaCl)
Base: https://trading.robinhood.com
"""

import os
import base64
import time
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://trading.robinhood.com"


class RobinhoodClient:
    """
    Official Robinhood Crypto Trading API wrapper.
    Requires RH_API_KEY and RH_PRIVATE_KEY in .env
    """

    def __init__(self):
        # Strip whitespace/newlines — common copy-paste artifact that silently
        # breaks HTTP headers, causing "missing required headers" from Robinhood.
        self.api_key = os.getenv("RH_API_KEY", "").strip()
        self._private_key_b64 = os.getenv("RH_PRIVATE_KEY", "").strip()
        self._signing_key = None
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

        if self.api_key and self._private_key_b64:
            self._init_signing_key()

    def _init_signing_key(self):
        try:
            from nacl.signing import SigningKey
            # Strip padding issues — add == if base64 length is off
            key_b64 = self._private_key_b64
            padding = 4 - len(key_b64) % 4
            if padding != 4:
                key_b64 += "=" * padding
            raw = base64.b64decode(key_b64)
            if len(raw) != 32:
                raise ValueError(
                    f"Private key must be 32 bytes after decoding, got {len(raw)}. "
                    "Make sure you're using the PRIVATE KEY from generate_keys.py, "
                    "not the public key."
                )
            self._signing_key = SigningKey(raw)
        except Exception as e:
            print(f"[RH] Key init error: {e}")
            self._key_error = str(e)

    def get_key_error(self) -> str | None:
        return getattr(self, "_key_error", None)

    def is_configured(self) -> bool:
        return bool(self.api_key and self._signing_key)

    # ── Request signing ────────────────────────────────────────────────

    def _sign(self, method: str, path: str, body: str = "") -> dict:
        """
        Build signed headers per Robinhood API spec.
        Message format: api_key + timestamp + path + METHOD + body
        Ref: https://docs.robinhood.com/crypto/trading/
        """
        timestamp = str(int(time.time()))
        message = self.api_key + timestamp + path + method + body
        signed = self._signing_key.sign(message.encode("utf-8"))
        sig_b64 = base64.b64encode(signed.signature).decode("utf-8")
        return {
            "x-api-key": self.api_key,
            "x-signature": sig_b64,
            "x-timestamp": timestamp,
        }

    def _get(self, path: str, params: dict = None) -> dict | list:
        from urllib.parse import urlencode
        query = ""
        if params:
            query = "?" + urlencode(params)
        full_path = path + query
        headers = self._sign("GET", full_path, "")
        resp = self._session.get(BASE_URL + full_path, headers=headers, timeout=10)
        self._raise_for_status(resp)
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        body_str = json.dumps(body)
        headers = self._sign("POST", path, body_str)
        resp = self._session.post(BASE_URL + path, headers=headers,
                                  json=body, timeout=10)
        self._raise_for_status(resp)
        return resp.json()

    def _delete(self, path: str) -> dict:
        headers = self._sign("DELETE", path, "")
        resp = self._session.delete(BASE_URL + path, headers=headers, timeout=10)
        self._raise_for_status(resp)
        return resp.json()

    def _raise_for_status(self, resp):
        """Raise with the actual Robinhood error message from the response body."""
        if not resp.ok:
            try:
                detail = resp.json()
                msg = detail.get("detail") or detail.get("message") or str(detail)
            except Exception:
                msg = resp.text or resp.reason
            raise Exception(f"HTTP {resp.status_code}: {msg}")

    # ── Account ────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        """Account details including buying power."""
        try:
            data = self._get("/api/v1/crypto/trading/accounts/")
            results = data.get("results", [data])
            return results[0] if results else {}
        except Exception as e:
            return {"error": str(e)}

    def get_portfolio_value(self) -> float:
        acct = self.get_account()
        # equity = buying power + value of holdings
        return float(acct.get("equity", 0) or 0)

    def get_cash(self) -> float:
        acct = self.get_account()
        return float(acct.get("buying_power", 0) or 0)

    # ── Holdings ───────────────────────────────────────────────────────

    def get_holdings(self) -> list[dict]:
        """All crypto holdings with current value."""
        try:
            data = self._get("/api/v1/crypto/trading/holdings/")
            results = data.get("results", [])
            enriched = []
            for h in results:
                symbol = h.get("asset_code", "")
                qty = float(h.get("total_quantity", 0) or 0)
                if qty == 0:
                    continue
                cost_held = float(h.get("quantity_available_for_trading", 0) or 0)
                avg_price = float(h.get("cost_held", 0) or 0) / qty if qty else 0
                # Get current price
                pair = f"{symbol}-USD"
                current_price = self._get_best_price(pair)
                market_value = qty * current_price
                cost_basis = float(h.get("cost_held", 0) or 0)
                unrealized_pnl = market_value - cost_basis
                pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis else 0
                enriched.append({
                    "symbol": symbol,
                    "pair": pair,
                    "quantity": qty,
                    "avg_cost": avg_price,
                    "current_price": current_price,
                    "market_value": market_value,
                    "cost_basis": cost_basis,
                    "unrealized_pnl": unrealized_pnl,
                    "pnl_pct": pnl_pct,
                })
            return enriched
        except Exception as e:
            print(f"Holdings error: {e}")
            return []

    def get_total_equity(self) -> float:
        """Cash + sum of all holding market values."""
        cash = self.get_cash()
        holdings = self.get_holdings()
        return cash + sum(h["market_value"] for h in holdings)

    # ── Market Data ────────────────────────────────────────────────────

    def _get_best_price(self, symbol: str) -> float:
        """Get best bid/ask midpoint for a symbol like BTC-USD."""
        try:
            data = self._get("/api/v1/crypto/marketdata/best_bid_ask/", {"symbol": symbol})
            results = data.get("results", [])
            if results:
                bid = float(results[0].get("bid_inclusive_of_sell_spread", 0) or 0)
                ask = float(results[0].get("ask_inclusive_of_buy_spread", 0) or 0)
                return (bid + ask) / 2 if bid and ask else bid or ask
        except Exception:
            pass
        return 0.0

    def get_quote(self, symbol: str) -> dict:
        """Full quote for a trading pair like BTC-USD."""
        try:
            data = self._get("/api/v1/crypto/marketdata/best_bid_ask/", {"symbol": symbol})
            results = data.get("results", [])
            if results:
                r = results[0]
                bid = float(r.get("bid_inclusive_of_sell_spread", 0) or 0)
                ask = float(r.get("ask_inclusive_of_buy_spread", 0) or 0)
                return {
                    "symbol": symbol,
                    "bid": bid,
                    "ask": ask,
                    "price": (bid + ask) / 2 if bid and ask else bid or ask,
                    "timestamp": r.get("timestamp"),
                }
        except Exception as e:
            return {"error": str(e)}
        return {}

    def get_estimated_price(self, symbol: str, side: str, quantity: float) -> float:
        """Get estimated execution price for a given qty. side: 'bid' or 'ask'"""
        try:
            data = self._get("/api/v1/crypto/trading/estimated_price/", {
                "symbol": symbol, "side": side, "quantity": str(quantity)
            })
            results = data.get("results", [])
            if results:
                return float(results[0].get("price", 0) or 0)
        except Exception:
            pass
        return 0.0

    # ── Orders ─────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,           # "buy" | "sell"
        order_type: str,     # "market" | "limit" | "stop_limit"
        quantity: float = None,
        asset_quantity: float = None,  # crypto amount (alternative to USD qty)
        limit_price: float = None,
        stop_price: float = None,
        time_in_force: str = "gtc",
    ) -> dict:
        """
        Place a crypto order.
        Use `quantity` for USD notional amount, or `asset_quantity` for coin amount.
        """
        body = {
            "client_order_id": self._gen_order_id(),
            "side": side,
            "symbol": symbol,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        # Robinhood rejects quantities with more than 18 decimal places.
        # Round to 8 dp (standard crypto precision) to avoid floating-point noise.
        def _qty_str(v: float) -> str:
            return f"{round(v, 8):.8f}".rstrip("0").rstrip(".")

        if order_type == "market":
            if asset_quantity is not None:
                body["market_order_config"] = {"asset_quantity": _qty_str(asset_quantity)}
            elif quantity is not None:
                body["market_order_config"] = {"asset_quantity": _qty_str(quantity)}
        elif order_type == "limit" and limit_price is not None:
            body["limit_order_config"] = {
                "asset_quantity": _qty_str(asset_quantity or quantity or 0),
                "limit_price": str(limit_price),
                "time_in_force": time_in_force,
            }
        elif order_type == "stop_limit" and limit_price and stop_price:
            body["stop_limit_order_config"] = {
                "asset_quantity": _qty_str(asset_quantity or quantity or 0),
                "limit_price": str(limit_price),
                "stop_price": str(stop_price),
                "time_in_force": time_in_force,
            }
        try:
            return self._post("/api/v1/crypto/trading/orders/", body)
        except Exception as e:
            return {"error": str(e)}

    def buy_market(self, symbol: str, asset_quantity: float) -> dict:
        return self.place_order(symbol, "buy", "market", asset_quantity=asset_quantity)

    def sell_market(self, symbol: str, asset_quantity: float) -> dict:
        return self.place_order(symbol, "sell", "market", asset_quantity=asset_quantity)

    def buy_limit(self, symbol: str, asset_quantity: float, limit_price: float) -> dict:
        return self.place_order(symbol, "buy", "limit", asset_quantity=asset_quantity, limit_price=limit_price)

    def sell_limit(self, symbol: str, asset_quantity: float, limit_price: float) -> dict:
        return self.place_order(symbol, "sell", "limit", asset_quantity=asset_quantity, limit_price=limit_price)

    def cancel_order(self, order_id: str) -> dict:
        try:
            # Cancel is a POST per Robinhood API spec, not DELETE
            return self._post(f"/api/v1/crypto/trading/orders/{order_id}/cancel/", {})
        except Exception as e:
            return {"error": str(e)}

    def get_orders(self, limit: int = 50) -> list[dict]:
        """Recent order history."""
        try:
            data = self._get("/api/v1/crypto/trading/orders/")
            orders = data.get("results", [])
            result = []
            for o in orders[:limit]:
                result.append({
                    "id": o.get("id"),
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "type": o.get("type"),
                    "state": o.get("state"),
                    "quantity": float(o.get("quantity") or 0),
                    "filled_qty": float(o.get("filled_asset_quantity") or 0),
                    "avg_price": float(o.get("average_price") or 0),
                    "created_at": o.get("created_at"),
                })
            return result
        except Exception as e:
            print(f"Orders error: {e}")
            return []

    def get_order(self, order_id: str) -> dict:
        try:
            return self._get(f"/api/v1/crypto/trading/orders/{order_id}/")
        except Exception as e:
            return {"error": str(e)}

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _gen_order_id() -> str:
        import uuid
        return str(uuid.uuid4())

    def get_positions(self) -> list[dict]:
        """Alias for get_holdings() — normalized format for strategies."""
        return self.get_holdings()
