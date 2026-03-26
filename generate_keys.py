# -*- coding: utf-8 -*-
"""
Robinhood Crypto API -- Ed25519 Key Pair Generator

Run this once to generate your key pair.
Then register the PUBLIC key on Robinhood's website.
Your PRIVATE key goes in your .env file (never share it).

Usage:
    python generate_keys.py
"""

from nacl.signing import SigningKey
import base64

print("\n" + "=" * 55)
print("  Robinhood Crypto API -- Key Pair Generator")
print("=" * 55 + "\n")

# Generate Ed25519 key pair
signing_key = SigningKey.generate()

# Export keys as base64
private_key_b64 = base64.b64encode(bytes(signing_key)).decode("utf-8")
public_key_b64  = base64.b64encode(bytes(signing_key.verify_key)).decode("utf-8")

print("Keys generated!\n")
print("-" * 55)
print("  PUBLIC KEY  (paste this into Robinhood's website)")
print("-" * 55)
print(public_key_b64)
print()
print("-" * 55)
print("  PRIVATE KEY  (paste this into your .env file)")
print("  WARNING: Never share this. Never commit it to git.")
print("-" * 55)
print(private_key_b64)
print()
print("=" * 55)
print("  NEXT STEPS:")
print("=" * 55)
print("""
 1. Go to: robinhood.com > Account > Settings
           > Crypto API Keys > Create API Key

 2. Paste your PUBLIC KEY above into the
    'Public Key' field on Robinhood's website.

 3. Select permissions:  [x] Trading  [x] Read

 4. Robinhood will give you an API Key ID.
    Copy it -- that is your RH_API_KEY.

 5. Open your .env file and add:

      RH_API_KEY=<the API Key ID from Robinhood>
      RH_PRIVATE_KEY=<the PRIVATE KEY printed above>

 6. Open http://localhost:8502, enter both keys
    in the sidebar, and click 'Connect'.

Done! Your platform is live.
""")
