"""Binance SIGNED REST client — places and confirms real Spot orders.

DEFAULT-SAFE BY DESIGN: ``base_url`` defaults to Binance's TESTNET
(https://testnet.binance.vision), a free, real sandbox Binance provides with
fake funds for exactly this purpose. You must explicitly pass
``base_url=BINANCE_LIVE_URL`` to ever touch real money — this is a deliberate
default matching the master spec's "safe failure behavior: default to no-trade"
principle, applied here as "default to no real money."

CRITICAL, per the master spec section 12:
"Never assume an order succeeded just because the API request was sent.
Confirm the actual Binance order status before recording as complete."
Every ``place_order`` call here is followed by polling ``get_order`` until a
terminal status (FILLED/CANCELED/REJECTED/EXPIRED) or a timeout — the caller
NEVER receives a "success" without an explicitly confirmed status.

Signing: Binance Spot's signed-endpoint scheme is HMAC-SHA256 over the exact
query string, hex-digested, appended as a ``signature`` parameter. This is
implemented with Python's stdlib ``hmac``/``hashlib`` only — no external
crypto dependency.

TESTABILITY: this module's network calls go through an injectable
``httpx.AsyncClient`` (same pattern as ``marketdata/binance.py``) so tests
can supply a mock transport. ``sign_query_string`` itself is pure and
independent of any network. NEITHER has been exercised against the real
Binance API in this environment (no network access here) — this MUST be
tested against Binance Testnet before ever pointing at real funds.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import httpx

__all__ = [
    "BinanceLiveClient",
    "OrderStatus",
    "OrderResult",
    "sign_query_string",
    "BinanceOrderError",
    "BINANCE_LIVE_URL",
    "BINANCE_TESTNET_URL",
]

BINANCE_LIVE_URL = "https://api.binance.com"
BINANCE_TESTNET_URL = "https://testnet.binance.vision"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


TERMINAL_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
}


class BinanceOrderError(Exception):
    """Raised when Binance rejects an order or a request fails outright.

    Deliberately a distinct exception (not a generic Exception) so callers
    can catch order-specific failures without swallowing unrelated bugs.
    """


@dataclass(frozen=True)
class OrderResult:
    order_id: int
    client_order_id: str
    symbol: str
    side: str
    status: OrderStatus
    executed_qty: float
    cummulative_quote_qty: float
    raw: dict[str, Any]


def sign_query_string(query_string: str, api_secret: str) -> str:
    """Return the hex HMAC-SHA256 signature Binance requires on signed endpoints.

    Pure function — no network, no side effects. ``query_string`` must be the
    EXACT string that will be sent (parameter order matters for what the
    signature covers, though not for Binance's verification of it).
    """
    return hmac.new(
        api_secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class BinanceLiveClient:
    """Signed Binance Spot REST client. Testnet by default — see module docstring."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = BINANCE_TESTNET_URL,
        recv_window: int = 5000,
        client: Optional[httpx.AsyncClient] = None,
        now_ms: Optional[callable] = None,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError(
                "api_key and api_secret are both required — "
                "never trade with empty credentials."
            )

        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self._client = client
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    def _headers(self) -> dict[str, str]:
        return {"X-MBX-APIKEY": self.api_key}

    def _signed_query(self, params: dict[str, Any]) -> str:
        params = dict(params)
        params.setdefault("recvWindow", self.recv_window)
        params["timestamp"] = self._now_ms()

        query_string = urllib.parse.urlencode(params)
        signature = sign_query_string(
            query_string,
            self.api_secret,
        )

        return f"{query_string}&signature={signature}"

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        query = self._signed_query(params)
        url = f"{self.base_url}{path}?{query}"

        async def _do(
            client: httpx.AsyncClient,
        ) -> httpx.Response:
            if method == "POST":
                return await client.post(
                    url,
                    headers=self._headers(),
                    timeout=15.0,
                )

            if method == "GET":
                return await client.get(
                    url,
                    headers=self._headers(),
                    timeout=15.0,
                )

            if method == "DELETE":
                return await client.delete(
                    url,
                    headers=self._headers(),
                    timeout=15.0,
                )

            raise ValueError(f"Unsupported method: {method}")

        if self._client is not None:
            response = await _do(self._client)
        else:
            async with httpx.AsyncClient() as client:
                response = await _do(client)

        if response.status_code >= 400:
            raise BinanceOrderError(
                f"Binance {method} {path} failed: "
                f"HTTP {response.status_code}: {response.text}"
            )

        return response.json()

    async def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        client_order_id: str,
    ) -> dict[str, Any]:
        """Place a Spot MARKET order.

        Side must be 'BUY' or 'SELL' — no other value.
        """
        if side not in ("BUY", "SELL"):
            raise ValueError(
                f"side must be 'BUY' or 'SELL', got {side!r} — "
                "spot-only, no shorting/derivatives."
            )

        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
            "newClientOrderId": client_order_id,
        }

        return await self._request(
            "POST",
            "/api/v3/order",
            params,
        )

    async def get_order(
        self,
        *,
        symbol: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        """Fetch the CURRENT status of a previously placed order."""

        params = {
            "symbol": symbol,
            "origClientOrderId": client_order_id,
        }

        return await self._request(
            "GET",
            "/api/v3/order",
            params,
        )

    async def cancel_order(
        self,
        *,
        symbol: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "origClientOrderId": client_order_id,
        }

        return await self._request(
            "DELETE",
            "/api/v3/order",
            params,
        )

    async def get_account(self) -> dict[str, Any]:
        """Read-only account info (balances). Used to confirm API key permissions."""

        return await self._request(
            "GET",
            "/api/v3/account",
            {},
        )

    async def place_and_confirm(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        client_order_id: str,
        poll_interval_seconds: float = 1.0,
        max_polls: int = 10,
        sleep_fn: Optional[callable] = None,
    ) -> OrderResult:
        """Place an order, then POLL until a TERMINAL status is confirmed.

        Per spec: never assume success from the placement response alone.
        Raises BinanceOrderError if a terminal status isn't reached within
        ``max_polls`` attempts — the caller must treat that as "unknown
        outcome, investigate manually," never as a silent success.
        """

        placed = await self.place_market_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            client_order_id=client_order_id,
        )

        status = OrderStatus(
            placed.get("status", "NEW")
        )

        raw = placed

        for _ in range(max_polls):
            if status in TERMINAL_STATUSES:
                break

            if sleep_fn is not None:
                await sleep_fn(
                    poll_interval_seconds
                )
            else:
                import asyncio

                await asyncio.sleep(
                    poll_interval_seconds
                )

            raw = await self.get_order(
                symbol=symbol,
                client_order_id=client_order_id,
            )

            status = OrderStatus(
                raw.get("status", "NEW")
            )

        if status not in TERMINAL_STATUSES:
            raise BinanceOrderError(
                f"Order {client_order_id} did not reach a terminal status after "
                f"{max_polls} polls — last known status: {status.value}. "
                "Treat as UNKNOWN outcome, not a failure or success."
            )

        return OrderResult(
            order_id=raw.get("orderId", -1),
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            status=status,
            executed_qty=float(
                raw.get("executedQty", 0.0)
            ),
            cummulative_quote_qty=float(
                raw.get("cummulativeQuoteQty", 0.0)
            ),
            raw=raw,
      )
