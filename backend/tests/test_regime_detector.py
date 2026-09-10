"""Tests for deterministic market-regime detection."""
from __future__ import annotations

import unittest

from halaltrade.regime.detector import (
    MarketRegime,
    VolatilityLevel,
    detect_regime,
)


class TestRegimeDetector(unittest.TestCase):
    def _ohlc(self, closes: list[float]) -> tuple[list[float], list[float], list[float]]:
        highs = [price + 10.0 for price in closes]
        lows = [price - 10.0 for price in closes]
        return closes, highs, lows

    def test_insufficient_data_is_unclear(self) -> None:
        closes = [100.0] * 10
        closes, highs, lows = self._ohlc(closes)

        result = detect_regime(closes, highs, lows)

        self.assertEqual(result.regime, MarketRegime.UNCLEAR)
        self.assertFalse(result.tradeable())
        self.assertIsNone(result.trend_strength)

    def test_length_mismatch_is_unclear(self) -> None:
        closes = [100.0] * 100
        highs = [101.0] * 99
        lows = [99.0] * 100

        result = detect_regime(closes, highs, lows)

        self.assertEqual(result.regime, MarketRegime.UNCLEAR)
        self.assertFalse(result.tradeable())

    def test_flat_market_is_ranging(self) -> None:
        closes = [100.0] * 100
        closes, highs, lows = self._ohlc(closes)

        result = detect_regime(closes, highs, lows)

        self.assertEqual(result.regime, MarketRegime.RANGING)
        self.assertTrue(result.tradeable())
        self.assertIsNotNone(result.trend_strength)

    def test_upward_market_has_positive_trend_strength(self) -> None:
        closes = [100.0 + i * 2.0 for i in range(100)]
        closes, highs, lows = self._ohlc(closes)

        result = detect_regime(closes, highs, lows)

        self.assertGreater(result.trend_strength or 0.0, 0.0)
        self.assertIn(
            result.regime,
            {
                MarketRegime.WEAK_UPTREND,
                MarketRegime.STRONG_UPTREND,
            },
        )

    def test_downward_market_has_negative_trend_strength(self) -> None:
        closes = [300.0 - i * 2.0 for i in range(100)]
        closes, highs, lows = self._ohlc(closes)

        result = detect_regime(closes, highs, lows)

        self.assertLess(result.trend_strength or 0.0, 0.0)
        self.assertIn(
            result.regime,
            {
                MarketRegime.WEAK_DOWNTREND,
                MarketRegime.STRONG_DOWNTREND,
            },
        )

    def test_zero_atr_is_unclear(self) -> None:
        closes = [100.0] * 100
        highs = closes.copy()
        lows = closes.copy()

        result = detect_regime(closes, highs, lows)

        self.assertEqual(result.regime, MarketRegime.UNCLEAR)
        self.assertFalse(result.tradeable())

    def test_result_contains_reason(self) -> None:
        closes = [100.0] * 100
        closes, highs, lows = self._ohlc(closes)

        result = detect_regime(closes, highs, lows)

        self.assertTrue(result.reason)
        self.assertIn("volatility", result.reason)

    def test_volatility_is_known_with_enough_data(self) -> None:
        closes = [100.0 + i * 0.1 for i in range(100)]
        closes, highs, lows = self._ohlc(closes)

        result = detect_regime(closes, highs, lows)

        self.assertIn(
            result.volatility,
            {
                VolatilityLevel.HIGH,
                VolatilityLevel.NORMAL,
                VolatilityLevel.LOW,
            },
        )


if __name__ == "__main__":
    unittest.main()