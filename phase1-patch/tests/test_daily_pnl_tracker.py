"""Tests for halaltrade.risk.daily.DailyPnLTracker (stdlib only, no external deps)."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from halaltrade.risk.daily import DailyPnLTracker


def dt(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class TestDailyPnLTracker(unittest.TestCase):
    def test_first_update_establishes_baseline_at_zero(self) -> None:
        tracker = DailyPnLTracker()
        today = tracker.update(
            cumulative_realized_pnl=50.0,
            now=lambda: dt(2026, 9, 9, 10, 0),
        )
        self.assertEqual(today, 0.0)

    def test_same_day_accumulates_correctly(self) -> None:
        tracker = DailyPnLTracker()
        tracker.update(100.0, now=lambda: dt(2026, 9, 9, 9, 0))
        today = tracker.update(
            130.0,
            now=lambda: dt(2026, 9, 9, 15, 0),
        )
        self.assertAlmostEqual(today, 30.0, places=6)

    def test_day_rollover_resets_bucket(self) -> None:
        tracker = DailyPnLTracker()
        tracker.update(
            100.0,
            now=lambda: dt(2026, 9, 9, 23, 55),
        )
        tracker.update(
            60.0,
            now=lambda: dt(2026, 9, 9, 23, 59),
        )
        today_after_rollover = tracker.update(
            60.0,
            now=lambda: dt(2026, 9, 10, 0, 1),
        )
        self.assertEqual(today_after_rollover, 0.0)

    def test_losses_after_rollover_measured_from_new_baseline(self) -> None:
        tracker = DailyPnLTracker()
        tracker.update(
            1000.0,
            now=lambda: dt(2026, 9, 9, 12, 0),
        )
        tracker.update(
            1000.0,
            now=lambda: dt(2026, 9, 10, 0, 5),
        )
        today = tracker.update(
            950.0,
            now=lambda: dt(2026, 9, 10, 6, 0),
        )
        self.assertAlmostEqual(today, -50.0, places=6)

    def test_force_reset(self) -> None:
        tracker = DailyPnLTracker()
        tracker.update(
            500.0,
            now=lambda: dt(2026, 9, 9, 12, 0),
        )
        tracker.update(
            400.0,
            now=lambda: dt(2026, 9, 9, 18, 0),
        )
        self.assertAlmostEqual(tracker.today_pnl, -100.0, places=6)
        tracker.force_reset(400.0)
        self.assertEqual(tracker.today_pnl, 0.0)


if __name__ == "__main__":
    unittest.main()