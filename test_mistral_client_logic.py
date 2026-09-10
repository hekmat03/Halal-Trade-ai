"""Tests for build_prompt / parse_response in halaltrade.research.mistral_client.

These test ONLY the pure, network-free logic. The actual API call
(MistralAnalyzer.analyze) requires httpx + a real or mocked HTTP transport
and is NOT covered here — see the module docstring.
"""
from __future__ import annotations

import unittest

from halaltrade.research.mistral_client import AIAnalysis, build_prompt, parse_response


class TestBuildPrompt(unittest.TestCase):
    def test_includes_provided_fields(self) -> None:
        prompt = build_prompt({"symbol": "BTCUSDT", "price": 65000.0, "market_regime": "strong_uptrend"})
        self.assertIn("BTCUSDT", prompt)
        self.assertIn("65000.0", prompt)
        self.assertIn("strong_uptrend", prompt)

    def test_omits_missing_fields_rather_than_fabricating(self) -> None:
        prompt = build_prompt({"symbol": "BTCUSDT"})
        self.assertNotIn("RSI", prompt)
        self.assertNotIn("Volatility", prompt)

    def test_ignores_none_values(self) -> None:
        prompt = build_prompt({"symbol": "BTCUSDT", "rsi": None})
        self.assertNotIn("RSI:", prompt)

    def test_empty_context_still_produces_valid_prompt(self) -> None:
        prompt = build_prompt({})
        self.assertIn("Based ONLY on the data above", prompt)


class TestParseResponse(unittest.TestCase):
    def test_parses_well_formed_response(self) -> None:
        raw = "RECOMMENDATION: BUY\nCONFIDENCE: 72\nREASON: Strong uptrend with rising volume."
        result = parse_response(raw)
        self.assertEqual(result.recommendation, "BUY")
        self.assertAlmostEqual(result.confidence, 0.72, places=6)
        self.assertEqual(result.source, "mistral")
        self.assertIn("Strong uptrend", result.reason)

    def test_multi_line_reason_is_joined(self) -> None:
        raw = "RECOMMENDATION: HOLD\nCONFIDENCE: 40\nREASON: Market is unclear.\nWaiting for confirmation."
        result = parse_response(raw)
        self.assertIn("Market is unclear.", result.reason)
        self.assertIn("Waiting for confirmation.", result.reason)

    def test_confidence_clamped_to_0_100_range(self) -> None:
        raw = "RECOMMENDATION: SELL\nCONFIDENCE: 150\nREASON: overconfident model."
        result = parse_response(raw)
        self.assertEqual(result.confidence, 1.0)

    def test_missing_recommendation_falls_back_to_hold(self) -> None:
        raw = "CONFIDENCE: 80\nREASON: forgot the recommendation line."
        result = parse_response(raw)
        self.assertEqual(result.recommendation, "HOLD")
        self.assertEqual(result.source, "fallback_parse_error")

    def test_missing_confidence_falls_back_to_hold(self) -> None:
        raw = "RECOMMENDATION: BUY\nREASON: forgot confidence."
        result = parse_response(raw)
        self.assertEqual(result.recommendation, "HOLD")
        self.assertEqual(result.source, "fallback_parse_error")

    def test_invalid_recommendation_value_falls_back_to_hold(self) -> None:
        raw = "RECOMMENDATION: SHORT\nCONFIDENCE: 90\nREASON: not a valid spot action."
        result = parse_response(raw)
        self.assertEqual(result.recommendation, "HOLD")
        self.assertEqual(result.source, "fallback_parse_error")

    def test_garbage_input_never_raises(self) -> None:
        result = parse_response("completely unstructured nonsense with no fields at all")
        self.assertEqual(result.recommendation, "HOLD")
        self.assertEqual(result.source, "fallback_parse_error")

    def test_never_returns_a_signal_type(self) -> None:
        # Enforce at the test level too: AIAnalysis is advisory-only, not a Signal.
        raw = "RECOMMENDATION: BUY\nCONFIDENCE: 60\nREASON: ok."
        result = parse_response(raw)
        self.assertIsInstance(result, AIAnalysis)
        self.assertFalse(hasattr(result, "amount"))
        self.assertFalse(hasattr(result, "stop_loss"))


if __name__ == "__main__":
    unittest.main()
