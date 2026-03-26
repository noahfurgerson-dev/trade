# -*- coding: utf-8 -*-
"""
Robinhood API Live Signing Test
--------------------------------
Tests GET and POST signing with the correct method-included format.
The signing message format (from official docs) is:
    message = f"{api_key}{timestamp}{path}{method}{body}"

Run this to confirm which body encoding works with the live API.
Usage:
    python debug_api.py
"""

import os, base64, time, json, requests
from dotenv import load_dotenv

load_dotenv(override=True)

API_KEY     = os.getenv("RH_API_KEY", "").strip()
PRIVATE_B64 = os.getenv("RH_PRIVATE_KEY", "").strip()
BASE_URL    = "https://trading.robinhood.com"

from nacl.signing import SigningKey
padded      = PRIVATE_B64 + "=" * (4 - len(PRIVATE_B64) % 4)
signing_key = SigningKey(base64.b64decode(padded))

print("\n" + "=" * 60)
print("  Robinhood Live API Test  (method-included signing)")
print("=" * 60)
print(f"API Key : {API_KEY}\n")

def sign(method, path, body_str=""):
    ts = str(int(time.time()))
    message = API_KEY + ts + path + method + body_str
    sig = base64.b64encode(signing_key.sign(message.encode("utf-8")).signature).decode()
    return ts, sig

def get(path, label):
    ts, sig = sign("GET", path)
    headers = {"x-api-key": API_KEY, "x-signature": sig,
               "x-timestamp": ts, "Content-Type": "application/json"}
    r = requests.get(BASE_URL + path, headers=headers, timeout=10)
    status = r.status_code
    try:
        body = r.json()
    except Exception:
        body = r.text
    icon = "OK" if status == 200 else "FAIL"
    print(f"  [{icon}] GET {path}  ->  HTTP {status}")
    if status == 200:
        print(f"       Response: {json.dumps(body)[:120]}")
    else:
        print(f"       Error: {body}")
    return status, body

# ── Test 1: GET accounts (no body) ─────────────────────────────────
print("-" * 60)
print("Test 1: GET /api/v1/crypto/trading/accounts/")
status, acct = get("/api/v1/crypto/trading/accounts/", "accounts")

# ── Test 2: GET best bid/ask (query param) ─────────────────────────
print("\nTest 2: GET /api/v1/crypto/marketdata/best_bid_ask/?symbol=BTC-USD")
get("/api/v1/crypto/marketdata/best_bid_ask/?symbol=BTC-USD", "best_bid_ask")

# ── Test 3: POST with dict-repr body (matches docs test vector) ────
print("\nTest 3: POST order (dict repr body — matches docs test vector)")
order_dict = {
    "client_order_id": "00000000-0000-0000-0000-000000000001",
    "side": "buy",
    "symbol": "BTC-USD",
    "type": "market",
    "market_order_config": {"asset_quantity": "0.00001"},
}
body_repr = str(order_dict)
ts, sig = sign("POST", "/api/v1/crypto/trading/orders/", body_repr)
headers = {"x-api-key": API_KEY, "x-signature": sig,
           "x-timestamp": ts, "Content-Type": "application/json"}
r = requests.post(BASE_URL + "/api/v1/crypto/trading/orders/",
                  headers=headers, json=order_dict, timeout=10)
print(f"  [{'OK' if r.status_code in (200,201) else 'FAIL'}] POST -> HTTP {r.status_code}: {r.text[:120]}")

# ── Test 4: POST with json-string body ─────────────────────────────
print("\nTest 4: POST order (json.dumps body)")
body_json = json.dumps(order_dict)
ts, sig = sign("POST", "/api/v1/crypto/trading/orders/", body_json)
headers = {"x-api-key": API_KEY, "x-signature": sig,
           "x-timestamp": ts, "Content-Type": "application/json"}
r = requests.post(BASE_URL + "/api/v1/crypto/trading/orders/",
                  headers=headers, data=body_json, timeout=10)
print(f"  [{'OK' if r.status_code in (200,201) else 'FAIL'}] POST -> HTTP {r.status_code}: {r.text[:120]}")

print("\n" + "=" * 60)
if status == 200:
    print("  GET is working! Note which POST test passes above.")
    print("  We will update robinhood.py to use that body format.")
else:
    print("  GET still failing — key pair mismatch or key not activated yet.")
    print("  Wait a few minutes for Robinhood to activate the key, then retry.")
print("=" * 60)
