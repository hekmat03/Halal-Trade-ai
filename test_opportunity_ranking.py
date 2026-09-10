"""Tests for halaltrade.ranking.opportunity (stdlib only)."""
from __future__ import annotations

import unittest

from halaltrade.ranking.opportunity import Opportunity, rank_opportunities, select_best


def make_opp(name="strat", confidence=0.5, rr=2.0, quality=0.5, regime_fit=0.5, fee=0.001, exposure=0.0):
    return Opportunity(
        strategy_name=name,
        confidence=confidence,
        risk_reward_ratio=rr,
        strategy_quality_score=quality,
        regime_compatibility_score=regime_fit,
        expected_fee_pct=fee,
        current_exposure_pct=exposure,
    )


class TestRankOpportunities(unittest.TestCase):
    def test_empty_list_returns_empty(self) -> None:
        self.assertEqual(rank_opportunities([]), [])

    def test_higher_confidence_ranks_first_all_else_equal(self) -> None:
        low = make_opp("low_conf", confidence=0.2)
        high = make_opp("high_conf", confidence=0.9)
        ranked = rank_opportunities([low, high])
        self.assertEqual(ranked[0][0].strategy_name, "high_conf")

    def test_higher_fees_penalize_score(self) -> None:
        cheap = make_opp("cheap", fee=0.0005)
        expensive = make_opp("expensive", fee=0.05)
        ranked = rank_opportunities([cheap, expensive])
        self.assertEqual(ranked[0][0].strategy_name, "cheap")

    def test_higher_exposure_penalizes_score(self) -> None:
        free = make_opp("free", exposure=0.0)
        loaded = make_opp("loaded", exposure=0.9)
        ranked = rank_opportunities([free, loaded])
        self.assertEqual(ranked[0][0].strategy_name, "free")

    def test_extreme_risk_reward_is_capped_not_dominant(self) -> None:
        # A wildly high claimed R:R shouldn't overwhelm poor scores elsewhere.
        huge_rr_but_bad = make_opp("huge_rr", rr=100.0, confidence=0.1, quality=0.1, regime_fit=0.1)
        balanced = make_opp("balanced", rr=2.0, confidence=0.8, quality=0.8, regime_fit=0.8)
        ranked = rank_opportunities([huge_rr_but_bad, balanced])
        self.assertEqual(ranked[0][0].strategy_name, "balanced")


class TestSelectBest(unittest.TestCase):
    def test_empty_list_returns_none(self) -> None:
        self.assertIsNone(select_best([]))

    def test_returns_none_when_nothing_meets_minimums(self) -> None:
        weak = make_opp("weak", confidence=0.1, rr=1.0)
        result = select_best([weak], min_confidence=0.5, min_risk_reward=1.5)
        self.assertIsNone(result)

    def test_returns_best_eligible(self) -> None:
        weak = make_opp("weak", confidence=0.2, rr=1.0)
        strong = make_opp("strong", confidence=0.9, rr=2.5, quality=0.9, regime_fit=0.9)
        result = select_best([weak, strong], min_confidence=0.5, min_risk_reward=1.5)
        self.assertEqual(result.strategy_name, "strong")

    def test_filters_out_ineligible_even_if_highest_raw_score(self) -> None:
        # High confidence but fails the minimum risk/reward -> must be excluded.
        disqualified = make_opp("disqualified", confidence=0.99, rr=0.5, quality=0.99, regime_fit=0.99)
        eligible = make_opp("eligible", confidence=0.5, rr=2.0, quality=0.5, regime_fit=0.5)
        result = select_best([disqualified, eligible], min_confidence=0.3, min_risk_reward=1.5)
        self.assertEqual(result.strategy_name, "eligible")


if __name__ == "__main__":
    unittest.main()
