"""Tests for halaltrade.validation.live_readiness.LiveReadinessCheck."""
from __future__ import annotations

import unittest

from halaltrade.validation.live_readiness import LiveReadinessCheck


def all_pass_kwargs(**overrides):
    base = dict(
        paper_trading_days=35.0,
        paper_profit_factor=1.5,
        walk_forward_passed=True,
        user_enabled_live=True,
        user_confirmed_risk=True,
        user_confirmed_shariah=True,
        system_healthy=True,
        api_key_restricted=True,
    )
    base.update(overrides)
    return base


class TestLiveReadinessCheck(unittest.TestCase):
    def test_all_conditions_met_is_ready(self) -> None:
        check = LiveReadinessCheck(**all_pass_kwargs())
        result = check.evaluate()
        self.assertTrue(result.ready)
        self.assertEqual(result.failed, [])
        self.assertEqual(len(result.passed), 8)

    def test_insufficient_paper_days_blocks(self) -> None:
        check = LiveReadinessCheck(**all_pass_kwargs(paper_trading_days=10.0))
        result = check.evaluate()
        self.assertFalse(result.ready)
        self.assertTrue(any("paper trading duration" in f for f in result.failed))

    def test_low_profit_factor_blocks(self) -> None:
        check = LiveReadinessCheck(**all_pass_kwargs(paper_profit_factor=0.9))
        result = check.evaluate()
        self.assertFalse(result.ready)
        self.assertTrue(any("profit factor" in f for f in result.failed))

    def test_profit_factor_exactly_at_threshold_is_not_enough(self) -> None:
        # spec language is ">" the minimum, not ">="
        check = LiveReadinessCheck(**all_pass_kwargs(paper_profit_factor=1.1))
        result = check.evaluate()
        self.assertFalse(result.ready)

    def test_missing_walk_forward_blocks(self) -> None:
        check = LiveReadinessCheck(**all_pass_kwargs(walk_forward_passed=False))
        result = check.evaluate()
        self.assertFalse(result.ready)
        self.assertTrue(any("walk-forward" in f for f in result.failed))

    def test_user_must_explicitly_enable_live(self) -> None:
        check = LiveReadinessCheck(**all_pass_kwargs(user_enabled_live=False))
        result = check.evaluate()
        self.assertFalse(result.ready)

    def test_user_must_confirm_risk(self) -> None:
        check = LiveReadinessCheck(**all_pass_kwargs(user_confirmed_risk=False))
        result = check.evaluate()
        self.assertFalse(result.ready)

    def test_user_must_confirm_shariah(self) -> None:
        check = LiveReadinessCheck(**all_pass_kwargs(user_confirmed_shariah=False))
        result = check.evaluate()
        self.assertFalse(result.ready)

    def test_unhealthy_system_blocks(self) -> None:
        check = LiveReadinessCheck(**all_pass_kwargs(system_healthy=False))
        result = check.evaluate()
        self.assertFalse(result.ready)

    def test_unrestricted_api_key_blocks(self) -> None:
        # This one matters most: an API key that CAN withdraw must never go live.
        check = LiveReadinessCheck(**all_pass_kwargs(api_key_restricted=False))
        result = check.evaluate()
        self.assertFalse(result.ready)
        self.assertTrue(any("withdrawal" in f for f in result.failed))

    def test_multiple_failures_all_reported_not_just_first(self) -> None:
        check = LiveReadinessCheck(
            **all_pass_kwargs(paper_trading_days=5.0, user_confirmed_risk=False, api_key_restricted=False)
        )
        result = check.evaluate()
        self.assertFalse(result.ready)
        self.assertEqual(len(result.failed), 3)

    def test_custom_thresholds_respected(self) -> None:
        check = LiveReadinessCheck(
            **all_pass_kwargs(paper_trading_days=20.0),
            min_paper_trading_days=15.0,
        )
        result = check.evaluate()
        self.assertTrue(result.ready)


if __name__ == "__main__":
    unittest.main()