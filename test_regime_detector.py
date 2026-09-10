"""Tests for halaltrade.regime.detector (stdlib only)."""
from __future__ import annotations

import unittest

from halaltrade.regime.detector import MarketRegime, VolatilityLevel, detect_regime


def flat_series(price: float, n: int) -> tuple[list[float], list[float], list[float]]:
    closes = [price] * n
    highs = [price + 0.1] * n
    lows = [price - 0.1] * n
    return closes, highs, lows


class TestDetectRegime(unittest.TestCase):
    def test_insufficient_data_is_unclear(self) -> None:
        closes, highs, lows = flat_series(100.0, 10)
        result = detect_regime(closes, highs, lows)
        self.assertEqual(result.regime, MarketRegime.UNCLEAR)
        self.assertFalse(result.tradeable())

    def test_mismatched_lengths_is_unclear(self) -> None:
        closes, highs, lows = flat_series(100.0, 100)
        result = detect_regime(closes, highs[:-1], lows)
        self.assertEqual(result.regime, MarketRegime.UNCLEAR)

    def test_flat_market_is_ranging_with_zero_atr_handled(self) -> None:
        # perfectly flat OHLC -> ATR could be 0 depending on high/low spread;
        # here high/low differ by 0.2 so ATR > 0, should resolve to RANGING.
        closes, highs, lows = flat_series(100.0, 100)
        result = detect_regime(closes, highs, lows, vol_lookback=30)
        self.assertEqual(result.regime, MarketRegime.RANGING)

    def test_strong_uptrend_detected(self) -> None:
        n = 100
        closes = [100 + i * 3.0 for i in range(n)]  # steep, sustained climb
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        result = detect_regime(closes, highs, lows, vol_lookback=30)
        self.assertEqual(result.regime, MarketRegime.STRONG_UPTREND)
        self.assertTrue(result.tradeable())
        self.assertGreater(result.trend_strength, 0)

    def test_strong_downtrend_detected(self) -> None:
        n = 100
        closes = [500 - i * 3.0 for i in range(n)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        result = detect_regime(closes, highs, lows, vol_lookback=30)
        self.assertEqual(result.regime, MarketRegime.STRONG_DOWNTREND)
        self.assertLess(result.trend_strength, 0)

    def test_high_volatility_detected_after_calm_period(self) -> None:
        # calm period (small H-L range), then a burst of much wider ranges
        n_calm = 80
        closes = [100.0] * n_calm
        highs = [100.2] * n_calm
        lows = [99.8] * n_calm
        n_burst = 20
        for i in range(n_burst):
            closes.append(100.0)
            highs.append(100.0 + 5.0)
            lows.append(100.0 - 5.0)
        result = detect_regime(closes, highs, lows, atr_period=14, vol_lookback=50)
        self.assertEqual(result.volatility, VolatilityLevel.HIGH)

    def test_never_fabricates_trend_strength_when_unclear(self) -> None:
        closes, highs, lows = flat_series(100.0, 5)
        result = detect_regime(closes, highs, lows)
        self.assertIsNone(result.trend_strength)


if __name__ == "__main__":
    unittest.main()
