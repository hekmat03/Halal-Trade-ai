"""Tests for halaltrade.indicators.core — stdlib only, hand-verified numbers."""
from __future__ import annotations

import unittest

from halaltrade.indicators.core import atr, bollinger_bands, donchian_channel, ema, rsi, sma


class TestSMA(unittest.TestCase):
    def test_none_before_warmup(self) -> None:
        result = sma([1, 2, 3], period=5)
        self.assertEqual(result, [None, None, None])

    def test_known_values(self) -> None:
        result = sma([1, 2, 3, 4, 5], period=3)
        # bar 2 (0-indexed): avg(1,2,3)=2 ; bar3: avg(2,3,4)=3 ; bar4: avg(3,4,5)=4
        self.assertEqual(result, [None, None, 2.0, 3.0, 4.0])

    def test_rejects_invalid_period(self) -> None:
        with self.assertRaises(ValueError):
            sma([1, 2, 3], period=0)


class TestEMA(unittest.TestCase):
    def test_none_before_warmup(self) -> None:
        result = ema([1, 2], period=5)
        self.assertEqual(result, [None, None])

    def test_seed_is_sma(self) -> None:
        # First valid EMA value equals the plain SMA seed.
        values = [10, 20, 30, 40]
        result = ema(values, period=3)
        self.assertAlmostEqual(result[2], (10 + 20 + 30) / 3, places=6)

    def test_known_next_value(self) -> None:
        values = [10, 20, 30, 40]
        result = ema(values, period=3)
        alpha = 2 / 4
        expected_next = alpha * 40 + (1 - alpha) * result[2]
        self.assertAlmostEqual(result[3], expected_next, places=6)


class TestRSI(unittest.TestCase):
    def test_none_before_warmup(self) -> None:
        result = rsi([1, 2, 3], period=14)
        self.assertEqual(result, [None, None, None])

    def test_all_gains_yields_100(self) -> None:
        values = [float(i) for i in range(1, 20)]  # strictly increasing
        result = rsi(values, period=14)
        self.assertEqual(result[14], 100.0)

    def test_all_losses_yields_0(self) -> None:
        values = [float(i) for i in range(20, 1, -1)]  # strictly decreasing
        result = rsi(values, period=14)
        self.assertEqual(result[14], 0.0)

    def test_flat_prices_yields_neutral_50(self) -> None:
        values = [100.0] * 20
        result = rsi(values, period=14)
        self.assertEqual(result[14], 50.0)

    def test_bounded_between_0_and_100(self) -> None:
        values = [50, 55, 48, 60, 52, 58, 45, 62, 49, 57, 53, 61, 47, 59, 51, 56]
        result = rsi(values, period=14)
        for v in result:
            if v is not None:
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 100.0)


class TestATR(unittest.TestCase):
    def test_none_before_warmup(self) -> None:
        result = atr([10, 11], [9, 10], [9.5, 10.5], period=14)
        self.assertEqual(result, [None, None])

    def test_rejects_mismatched_lengths(self) -> None:
        with self.assertRaises(ValueError):
            atr([1, 2], [1], [1, 2], period=1)

    def test_known_simple_case(self) -> None:
        # Flat, no gaps: true range each bar = high - low = 2.0 always.
        highs = [11.0] * 10
        lows = [9.0] * 10
        closes = [10.0] * 10
        result = atr(highs, lows, closes, period=3)
        self.assertAlmostEqual(result[3], 2.0, places=6)
        self.assertAlmostEqual(result[9], 2.0, places=6)


class TestBollingerBands(unittest.TestCase):
    def test_none_before_warmup(self) -> None:
        mid, upper, lower = bollinger_bands([1, 2, 3], period=5)
        self.assertEqual(mid, [None, None, None])
        self.assertEqual(upper, [None, None, None])
        self.assertEqual(lower, [None, None, None])

    def test_zero_variance_bands_equal_mid(self) -> None:
        # Constant series -> std = 0 -> upper == lower == mid.
        values = [100.0] * 25
        mid, upper, lower = bollinger_bands(values, period=20, num_std=2.0)
        self.assertAlmostEqual(mid[19], 100.0, places=6)
        self.assertAlmostEqual(upper[19], 100.0, places=6)
        self.assertAlmostEqual(lower[19], 100.0, places=6)

    def test_bands_straddle_mid(self) -> None:
        values = [10, 12, 9, 15, 11, 13, 8, 14, 10, 12]
        mid, upper, lower = bollinger_bands(values, period=5, num_std=2.0)
        for i in range(len(values)):
            if mid[i] is not None:
                self.assertGreaterEqual(upper[i], mid[i])
                self.assertLessEqual(lower[i], mid[i])


class TestDonchianChannel(unittest.TestCase):
    def test_none_before_warmup(self) -> None:
        upper, lower = donchian_channel([1, 2, 3], [1, 2, 3], period=5)
        self.assertEqual(upper, [None, None, None])

    def test_excludes_current_bar_no_lookahead(self) -> None:
        highs = [10, 20, 15, 100]  # bar 3 has a huge spike
        lows = [5, 8, 7, 1]
        upper, lower = donchian_channel(highs, lows, period=3)
        # channel at index 3 must be computed from bars 0,1,2 ONLY (not the spike at 3)
        self.assertEqual(upper[3], 20)  # max(10,20,15)
        self.assertEqual(lower[3], 5)   # min(5,8,7)

    def test_rejects_mismatched_lengths(self) -> None:
        with self.assertRaises(ValueError):
            donchian_channel([1, 2], [1], period=1)


if __name__ == "__main__":
    unittest.main()