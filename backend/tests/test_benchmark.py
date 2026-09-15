"""Tests for halaltrade.validation.benchmark (stdlib only, hand-verified numbers)."""
from __future__ import annotations

import unittest

from halaltrade.validation.benchmark import buy_and_hold_return, compare_to_benchmark


class TestBuyAndHoldReturn(unittest.TestCase):
    def test_rejects_too_few_prices(self) -> None:
        with self.assertRaises(ValueError):
            buy_and_hold_return([100.0])

    def test_rejects_non_positive_prices(self) -> None:
        with self.assertRaises(ValueError):
            buy_and_hold_return([100.0, -50.0, 100.0])

    def test_simple_doubling(self) -> None:
        result = buy_and_hold_return([100.0, 150.0, 200.0])
        self.assertAlmostEqual(result.total_return, 1.0, places=6)  # +100%

    def test_simple_loss(self) -> None:
        result = buy_and_hold_return([100.0, 50.0])
        self.assertAlmostEqual(result.total_return, -0.5, places=6)

    def test_flat_prices_zero_return_and_sharpe(self) -> None:
        result = buy_and_hold_return([100.0] * 10)
        self.assertAlmostEqual(result.total_return, 0.0, places=6)
        self.assertEqual(result.sharpe, 0.0)
        self.assertEqual(result.max_drawdown, 0.0)

    def test_max_drawdown_detects_peak_to_trough(self) -> None:
        # rises to 120, falls to 90 (25% down from peak), recovers to 110
        result = buy_and_hold_return([100.0, 120.0, 90.0, 110.0])
        self.assertAlmostEqual(
            result.max_drawdown,
            (120.0 - 90.0) / 120.0,
            places=6,
        )

    def test_monotonic_uptrend_has_zero_drawdown(self) -> None:
        result = buy_and_hold_return([100.0, 110.0, 120.0, 130.0])
        self.assertEqual(result.max_drawdown, 0.0)


class TestCompareToBenchmark(unittest.TestCase):
    def test_passes_when_return_beats_benchmark(self) -> None:
        from halaltrade.validation.benchmark import BuyAndHoldResult

        bh = BuyAndHoldResult(
            total_return=0.05,
            sharpe=1.0,
            max_drawdown=0.1,
        )

        verdict = compare_to_benchmark(
            strategy_return=0.10,
            strategy_sharpe=0.5,
            benchmark=bh,
        )

        self.assertIn("PASSES", verdict)

    def test_passes_when_sharpe_beats_benchmark_even_if_return_is_lower(self) -> None:
        from halaltrade.validation.benchmark import BuyAndHoldResult

        bh = BuyAndHoldResult(
            total_return=0.20,
            sharpe=0.5,
            max_drawdown=0.3,
        )

        verdict = compare_to_benchmark(
            strategy_return=0.05,
            strategy_sharpe=1.5,
            benchmark=bh,
        )

        self.assertIn("PASSES", verdict)

    def test_fails_when_worse_on_both(self) -> None:
        from halaltrade.validation.benchmark import BuyAndHoldResult

        bh = BuyAndHoldResult(
            total_return=0.20,
            sharpe=1.5,
            max_drawdown=0.1,
        )

        verdict = compare_to_benchmark(
            strategy_return=0.05,
            strategy_sharpe=0.5,
            benchmark=bh,
        )

        self.assertIn("FAILS", verdict)


if __name__ == "__main__":
    unittest.main()
