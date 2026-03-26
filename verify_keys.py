# -*- coding: utf-8 -*-
"""
Key Pair Verification Tool
---------------------------
Derives the PUBLIC KEY from your current PRIVATE KEY and shows it.
Compare it to what you registered on Robinhood's website.

If they don't match -> re-register the public key shown here on Robinhood.

Usage:
    python verify_keys.py
"""

import os
import base64
from dotenv import load_dotenv

load_dotenv(override=True)

print("\n" + "=" * 60)
print("  Robinhood Key Pair Verification")
print("=" * 60 + "\n")

api_key     = os.getenv("RH_API_KEY", "").strip()
private_b64 = os.getenv("RH_PRIVATE_KEY", "").strip()

# ── Check API key ──────────────────────────────────────────────────
print("[1] API Key (from .env)")
print("-" * 60)
if api_key:
    print(f"    {api_key}")
    print(f"    Length: {len(api_key)} chars")
else:
    print("    *** NOT SET — add RH_API_KEY to your .env ***")
print()

# ── Decode private key ─────────────────────────────────────────────
print("[2] Private Key check")
print("-" * 60)
if not private_b64:
    print("    *** NOT SET — add RH_PRIVATE_KEY to your .env ***")
    exit(1)

try:
    # Fix base64 padding if needed
    padded = private_b64 + "=" * (4 - len(private_b64) % 4)
    raw = base64.b64decode(padded)
    print(f"    Decoded length: {len(raw)} bytes", end="")
    if len(raw) == 32:
        print(" (correct)")
    else:
        print(f" *** WRONG — expected 32, got {len(raw)}. Did you paste the PUBLIC key by mistake? ***")
        exit(1)
except Exception as e:
    print(f"    *** Failed to decode: {e} ***")
    exit(1)

# ── Derive public key ──────────────────────────────────────────────
try:
    from nacl.signing import SigningKey
    signing_key = SigningKey(raw)
    public_key_bytes = bytes(signing_key.verify_key)
    public_key_b64 = base64.b64encode(public_key_bytes).decode("utf-8")
except Exception as e:
    print(f"    *** Could not load signing key: {e} ***")
    exit(1)

print()
print("[3] Public Key derived from your Private Key")
print("-" * 60)
print(f"    {public_key_b64}")
print()

# ── Self-test: sign and verify ─────────────────────────────────────
try:
    import time
    test_msg = f"{api_key}{int(time.time())}/api/v1/crypto/trading/accounts/"
    signed = signing_key.sign(test_msg.encode("utf-8"))
    sig_b64 = base64.b64encode(signed.signature).decode("utf-8")
    # Verify locally
    signing_key.verify_key.verify(test_msg.encode("utf-8"), signed.signature)
    print("[4] Self-test: sign + verify")
    print("-" * 60)
    print("    PASSED — key pair is internally consistent")
    print(f"    Sample signature: {sig_b64[:32]}...")
except Exception as e:
    print(f"    FAILED: {e}")
    exit(1)

print()
print("=" * 60)
print("  ACTION REQUIRED")
print("=" * 60)
print(f"""
Go to: robinhood.com > Account > Settings > Crypto API Keys

Find the key with API Key ID:
    {api_key}

The PUBLIC KEY registered for that key MUST match:
    {public_key_b64}

If it does NOT match:
  Option A) Delete the old key on Robinhood and create a new one
            using the public key shown above.

  Option B) Run 'python generate_keys.py' again to get a fresh pair,
            register the new public key on Robinhood, and update your .env.
""")
