"""Tests for deterministic walk-forward window generation."""
from __future__ import annotations

import unittest

from halaltrade.validation.walkforward import (
    WalkForwardWindow,
    generate_walk_forward_windows,
)


class TestWalkForwardWindows(unittest.TestCase):
    def test_standard_non_overlapping_windows(self) -> None:
        windows = generate_walk_forward_windows(
            n_bars=100,
            train_size=60,
            test_size=10,
        )

        self.assertEqual(
            windows,
            [
                WalkForwardWindow(0, 60, 60, 70),
                WalkForwardWindow(10, 70, 70, 80),
                WalkForwardWindow(20, 80, 80, 90),
                WalkForwardWindow(30, 90, 90, 100),
            ],
        )

    def test_custom_step(self) -> None:
        windows = generate_walk_forward_windows(
            n_bars=100,
            train_size=50,
            test_size=10,
            step=20,
        )

        self.assertEqual(
            windows,
            [
                WalkForwardWindow(0, 50, 50, 60),
                WalkForwardWindow(20, 70, 70, 80),
                WalkForwardWindow(40, 90, 90, 100),
            ],
        )

    def test_not_enough_data_returns_empty(self) -> None:
        windows = generate_walk_forward_windows(
            n_bars=50,
            train_size=40,
            test_size=20,
        )

        self.assertEqual(windows, [])

    def test_exact_fit_produces_one_window(self) -> None:
        windows = generate_walk_forward_windows(
            n_bars=100,
            train_size=70,
            test_size=30,
        )

        self.assertEqual(
            windows,
            [WalkForwardWindow(0, 70, 70, 100)],
        )

    def test_test_window_never_exceeds_data(self) -> None:
        windows = generate_walk_forward_windows(
            n_bars=95,
            train_size=50,
            test_size=15,
            step=10,
        )

        for window in windows:
            self.assertLessEqual(window.test_end, 95)
            self.assertLess(window.train_end, window.test_start + 1)

    def test_train_always_precedes_test(self) -> None:
        windows = generate_walk_forward_windows(
            n_bars=200,
            train_size=100,
            test_size=20,
        )

        for window in windows:
            self.assertEqual(window.train_end, window.test_start)
            self.assertLess(window.train_start, window.train_end)
            self.assertLess(window.test_start, window.test_end)

    def test_zero_train_size_raises(self) -> None:
        with self.assertRaises(ValueError):
            generate_walk_forward_windows(
                n_bars=100,
                train_size=0,
                test_size=10,
            )

    def test_negative_test_size_raises(self) -> None:
        with self.assertRaises(ValueError):
            generate_walk_forward_windows(
                n_bars=100,
                train_size=50,
                test_size=-10,
            )

    def test_zero_step_raises(self) -> None:
        with self.assertRaises(ValueError):
            generate_walk_forward_windows(
                n_bars=100,
                train_size=50,
                test_size=10,
                step=0,
            )


if __name__ == "__main__":
    unittest.main()