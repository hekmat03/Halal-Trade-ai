"""Tests for halaltrade.zakat.calculate_zakat (stdlib only, no external deps)."""
from __future__ import annotations

import unittest

from halaltrade.zakat import calculate_zakat


class TestCalculateZakat(unittest.TestCase):
    def test_no_zakat_below_nisab(self) -> None:
        result = calculate_zakat(portfolio_value_usdt=1000.0, nisab_threshold_usdt=5000.0)
        self.assertFalse(result.due)
        self.assertEqual(result.zakat_amount_usdt, 0.0)

    def test_zakat_due_at_exactly_nisab(self) -> None:
        result = calculate_zakat(portfolio_value_usdt=5000.0, nisab_threshold_usdt=5000.0)
        self.assertTrue(result.due)
        self.assertEqual(result.zakat_amount_usdt, 125.0)

    def test_zakat_on_full_value_not_just_excess(self) -> None:
        result = calculate_zakat(portfolio_value_usdt=10_000.0, nisab_threshold_usdt=5000.0)
        self.assertEqual(result.zakat_amount_usdt, 250.0)

    def test_zero_nisab_means_never_due(self) -> None:
        result = calculate_zakat(portfolio_value_usdt=10_000.0, nisab_threshold_usdt=0.0)
        self.assertFalse(result.due)
        self.assertEqual(result.zakat_amount_usdt, 0.0)

    def test_custom_rate(self) -> None:
        result = calculate_zakat(
            portfolio_value_usdt=1000.0,
            nisab_threshold_usdt=500.0,
            zakat_rate=0.05
        )
        self.assertEqual(result.zakat_amount_usdt, 50.0)

    def test_rejects_negative_inputs(self) -> None:
        with self.assertRaises(ValueError):
            calculate_zakat(
                portfolio_value_usdt=-1.0,
                nisab_threshold_usdt=100.0
            )

        with self.assertRaises(ValueError):
            calculate_zakat(
                portfolio_value_usdt=100.0,
                nisab_threshold_usdt=-1.0
            )

    def test_rejects_invalid_rate(self) -> None:
        with self.assertRaises(ValueError):
            calculate_zakat(
                portfolio_value_usdt=100.0,
                nisab_threshold_usdt=50.0,
                zakat_rate=1.5
            )


if __name__ == "__main__":
    unittest.main()