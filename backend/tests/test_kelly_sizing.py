"""Tests for half-Kelly position sizing (halaltrade.sizing.kelly).

Note: halaltrade.sizing.kelly itself has zero external dependencies (stdlib
only) so the sizing math can be reused/tested anywhere. This test file still
imports it through the normal `halaltrade` package path for consistency with
the rest of the suite, which means it needs the same installed deps
(pydantic, etc.) as every other test here to import the package at all.
"""
from __future__ import annotations

import unittest

from halaltrade.sizing.kelly import KellyInputs, half_kelly_position_size, kelly_fraction


class TestKellyInputsValidation(unittest.TestCase):
    def test_rejects_win_rate_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            KellyInputs(win_rate=1.5, avg_win=0.02, avg_loss=0.01)
        with self.assertRaises(ValueError):
            KellyInputs(win_rate=-0.1, avg_win=0.02, avg_loss=0.01)

    def test_rejects_negative_avg_win(self) -> None:
        with self.assertRaises(ValueError):
            KellyInputs(win_rate=0.5, avg_win=-0.01, avg_loss=0.01)

    def test_rejects_negative_avg_loss(self) -> None:
        # avg_loss must be a magnitude (positive), not signed.
        with self.assertRaises(ValueError):
            KellyInputs(win_rate=0.5, avg_win=0.02, avg_loss=-0.01)


class TestKellyFraction(unittest.TestCase):
    def test_known_edge_case_50pct_winrate_2to1_payoff(self) -> None:
        # W=0.5, R=2 -> f* = 0.5 - 0.5/2 = 0.25 (a textbook Kelly example)
        inputs = KellyInputs(win_rate=0.5, avg_win=0.02, avg_loss=0.01)
        self.assertAlmostEqual(kelly_fraction(inputs), 0.25, places=6)

    def test_no_edge_returns_zero_not_negative(self) -> None:
        # Bad strategy: low win rate, poor payoff -> negative full Kelly.
        # Function must clamp to 0, never suggest a negative "size".
        inputs = KellyInputs(win_rate=0.2, avg_win=0.01, avg_loss=0.02)
        self.assertEqual(kelly_fraction(inputs), 0.0)

    def test_zero_avg_win_returns_zero(self) -> None:
        inputs = KellyInputs(win_rate=0.5, avg_win=0.0, avg_loss=0.01)
        self.assertEqual(kelly_fraction(inputs), 0.0)

    def test_zero_avg_loss_refuses_to_extrapolate(self) -> None:
        # No losing trades on record is a sample-size red flag, not a
        # green light for infinite sizing -> must return 0.0, not divide by zero.
        inputs = KellyInputs(win_rate=0.9, avg_win=0.02, avg_loss=0.0)
        self.assertEqual(kelly_fraction(inputs), 0.0)

    def test_perfect_win_rate_caps_reasonably(self) -> None:
        inputs = KellyInputs(win_rate=1.0, avg_win=0.02, avg_loss=0.01)
        # W=1 -> f* = 1 - 0 = 1.0 (would bet everything; half-Kelly layer
        # is what protects us from this, tested separately below)
        self.assertAlmostEqual(kelly_fraction(inputs), 1.0, places=6)


class TestHalfKellyPositionSize(unittest.TestCase):
    def test_halves_the_full_kelly_fraction(self) -> None:
        # f* = 0.25 (from the 50%/2:1 case) -> half-Kelly fraction = 0.125
        inputs = KellyInputs(win_rate=0.5, avg_win=0.02, avg_loss=0.01)
        size = half_kelly_position_size(equity=10_000, inputs=inputs, max_fraction=1.0)
        self.assertAlmostEqual(size, 10_000 * 0.125, places=2)

    def test_max_fraction_caps_even_a_perfect_edge(self) -> None:
        # Full Kelly would say "bet 100%"; half-Kelly says 50%; but our hard
        # safety ceiling (max_fraction) must still win.
        inputs = KellyInputs(win_rate=1.0, avg_win=0.02, avg_loss=0.01)
        size = half_kelly_position_size(
            equity=10_000, inputs=inputs, max_fraction=0.10
        )
        self.assertAlmostEqual(size, 10_000 * 0.10, places=2)

    def test_max_position_size_hard_cap_wins_over_kelly_math(self) -> None:
        inputs = KellyInputs(win_rate=1.0, avg_win=0.02, avg_loss=0.01)
        size = half_kelly_position_size(
            equity=100_000,
            inputs=inputs,
            max_fraction=0.5,
            max_position_size=500.0,  # mirrors Settings.max_position_size default
        )
        self.assertEqual(size, 500.0)

    def test_no_edge_returns_zero_size(self) -> None:
        inputs = KellyInputs(win_rate=0.2, avg_win=0.01, avg_loss=0.02)
        size = half_kelly_position_size(equity=10_000, inputs=inputs)
        self.assertEqual(size, 0.0)

    def test_zero_or_negative_equity_returns_zero(self) -> None:
        inputs = KellyInputs(win_rate=0.6, avg_win=0.02, avg_loss=0.01)
        self.assertEqual(half_kelly_position_size(equity=0, inputs=inputs), 0.0)
        self.assertEqual(half_kelly_position_size(equity=-500, inputs=inputs), 0.0)

    def test_invalid_max_fraction_raises(self) -> None:
        inputs = KellyInputs(win_rate=0.5, avg_win=0.02, avg_loss=0.01)
        with self.assertRaises(ValueError):
            half_kelly_position_size(equity=1000, inputs=inputs, max_fraction=0.0)
        with self.assertRaises(ValueError):
            half_kelly_position_size(equity=1000, inputs=inputs, max_fraction=1.5)


if __name__ == "__main__":
    unittest.main()
      
