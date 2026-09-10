"""Tests for halaltrade.risk.drawdown.DrawdownLockout (stdlib only, no external deps)."""
from __future__ import annotations

import unittest

from halaltrade.risk.drawdown import DrawdownLockout


class TestDrawdownLockout(unittest.TestCase):
    def test_rejects_invalid_max_drawdown(self) -> None:
        with self.assertRaises(ValueError):
            DrawdownLockout(max_drawdown=0.0)
        with self.assertRaises(ValueError):
            DrawdownLockout(max_drawdown=1.5)

    def test_not_locked_while_within_drawdown_limit(self) -> None:
        lockout = DrawdownLockout(max_drawdown=0.10)
        lockout.update(10_000)
        locked = lockout.update(9_500)
        self.assertFalse(locked)

    def test_locks_when_drawdown_exceeds_cap(self) -> None:
        lockout = DrawdownLockout(max_drawdown=0.10)
        lockout.update(10_000)
        locked = lockout.update(8_900)
        self.assertTrue(locked)
        self.assertTrue(lockout.locked)
        self.assertIn("11.00%", lockout.locked_reason)

    def test_stays_locked_even_if_equity_recovers(self) -> None:
        lockout = DrawdownLockout(max_drawdown=0.10)
        lockout.update(10_000)
        lockout.update(8_900)
        locked_after_recovery = lockout.update(10_500)
        self.assertTrue(locked_after_recovery)

    def test_explicit_unlock_clears_lock(self) -> None:
        lockout = DrawdownLockout(max_drawdown=0.10)
        lockout.update(10_000)
        lockout.update(8_900)
        self.assertTrue(lockout.locked)
        lockout.unlock()
        self.assertFalse(lockout.locked)
        self.assertIsNone(lockout.locked_reason)

    def test_unlock_can_reset_peak(self) -> None:
        lockout = DrawdownLockout(max_drawdown=0.10)
        lockout.update(10_000)
        lockout.update(8_900)
        lockout.unlock(reset_peak_to=9_000)
        self.assertEqual(lockout.peak, 9_000)
        locked = lockout.update(8_600)
        self.assertFalse(locked)

    def test_ignores_non_positive_equity_observations(self) -> None:
        lockout = DrawdownLockout(max_drawdown=0.10)
        lockout.update(10_000)
        locked = lockout.update(0)
        self.assertFalse(locked)
        self.assertEqual(lockout.peak, 10_000)


if __name__ == "__main__":
    unittest.main()