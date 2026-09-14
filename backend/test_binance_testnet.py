# Run in Colab: !python test_binance_testnet.py
# Step 1: read-only connection test (get account balances). No order placed yet.

import asyncio
from halaltrade.live.binance_client import BinanceLiveClient

API_KEY = "cLRHZBFMHJCvzFLKV1CUhZY1tUJyidtK8cwMDjAdb0xmBuVl0YD8SWPz4zVRqV6O"
API_SECRET = "tbjns6y2LcjumKeRQraQkchXVhTHxLLa1z1Y2atIGnqz4isCNLn7UhBNiHNOaFnM"

async def main():
    client = BinanceLiveClient(API_KEY, API_SECRET)  # defaults to TESTNET
    print("Connecting to Binance TESTNET (fake money)...")
    account = await client.get_account()
    print("\nConnection successful! Account info:")
    print(f"Can trade: {account.get('canTrade')}")
    print(f"Account type: {account.get('accountType')}")
    print("\nBalances (non-zero only):")
    for balance in account.get("balances", []):
        free = float(balance["free"])
        locked = float(balance["locked"])
        if free > 0 or locked > 0:
            print(f"  {balance['asset']}: free={free}, locked={locked}")

asyncio.run(main())
