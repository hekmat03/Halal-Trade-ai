"""Tests for halaltrade.live.binance_client — the parts testable without a
real Binance connection: HMAC signing (pure) and OrderStatus/terminal-status
logic. Actual network calls (place_market_order, get_order, etc.) require a
real or mocked httpx transport hitting Binance Testnet and are NOT covered
here — see the module docstring.
"""
from __future__ import annotations

import hashlib
import hmac
import unittest

from halaltrade.live.binance_client import (
    TERMINAL_STATUSES,
    OrderStatus,
    sign_query_string,
)


class TestSignQueryString(unittest.TestCase):
    def test_deterministic(self) -> None:
        qs = "symbol=BTCUSDT&side=BUY&quantity=0.001&timestamp=1699999999999"
        secret = "my_secret"
        self.assertEqual(
            sign_query_string(qs, secret),
            sign_query_string(qs, secret),
        )

    def test_matches_direct_hmac_call(self) -> None:
        qs = "symbol=BTCUSDT&side=SELL&timestamp=1699999999999"
        secret = "another_secret"
        expected = hmac.new(
            secret.encode(),
            qs.encode(),
            hashlib.sha256,
        ).hexdigest()

        self.assertEqual(
            sign_query_string(qs, secret),
            expected,
        )

    def test_returns_64_char_hex_string(self) -> None:
        sig = sign_query_string(
            "a=1",
            "secret",
        )

        self.assertEqual(
            len(sig),
            64,
        )

        self.assertTrue(
            all(
                c in "0123456789abcdef"
                for c in sig
            )
        )

    def test_different_queries_produce_different_signatures(self) -> None:
        sig1 = sign_query_string(
            "a=1",
            "secret",
        )
        sig2 = sign_query_string(
            "a=2",
            "secret",
        )

        self.assertNotEqual(
            sig1,
            sig2,
        )

    def test_different_secrets_produce_different_signatures(self) -> None:
        sig1 = sign_query_string(
            "a=1",
            "secret1",
        )
        sig2 = sign_query_string(
            "a=1",
            "secret2",
        )

        self.assertNotEqual(
            sig1,
            sig2,
        )


class TestOrderStatus(unittest.TestCase):
    def test_terminal_statuses_are_exactly_the_expected_four(self) -> None:
        expected = {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }

        self.assertEqual(
            TERMINAL_STATUSES,
            expected,
        )

    def test_new_is_not_terminal(self) -> None:
        self.assertNotIn(
            OrderStatus.NEW,
            TERMINAL_STATUSES,
        )

    def test_partially_filled_is_not_terminal(self) -> None:
        # A partial fill is NOT a confirmed final outcome -- must keep polling.
        self.assertNotIn(
            OrderStatus.PARTIALLY_FILLED,
            TERMINAL_STATUSES,
        )


if __name__ == "__main__":
    unittest.main()
