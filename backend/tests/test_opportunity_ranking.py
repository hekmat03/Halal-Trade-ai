"""Tests for deterministic opportunity ranking."""
from __future__ import annotations

import unittest

from halaltrade.ranking.opportunity import (
    Opportunity,
    RankingWeights,
    rank_opportunities,
    select_best,
)


class TestOpportunityRanking(unittest.TestCase):
    def _opp(
        self,
        name: str,
        *,
        confidence: float = 0.8,
        rr: float = 2.0,
        quality: float = 0.8,
        regime: float = 0.8,
        fee: float = 0.001,
        exposure: float = 0.0,
    ) -> Opportunity:
        return Opportunity(
            strategy_name=name,
            confidence=confidence,
            risk_reward_ratio=rr,
            strategy_quality_score=quality,
            regime_compatibility_score=regime,
            expected_fee_pct=fee,
            current_exposure_pct=exposure,
        )

    def test_empty_list_returns_empty(self) -> None:
        self.assertEqual(rank_opportunities([]), [])

    def test_highest_score_is_first(self) -> None:
        weak = self._opp("weak", confidence=0.5, quality=0.5, regime=0.5)
        strong = self._opp("strong", confidence=0.9, quality=0.9, regime=0.9)

        ranked = rank_opportunities([weak, strong])

        self.assertEqual(ranked[0][0].strategy_name, "strong")
        self.assertGreater(ranked[0][1], ranked[1][1])

    def test_select_best_returns_best_candidate(self) -> None:
        first = self._opp("first", confidence=0.6)
        second = self._opp("second", confidence=0.95)

        result = select_best([first, second])

        self.assertIsNotNone(result)
        self.assertEqual(result.strategy_name, "second")

    def test_minimum_confidence_filters_candidates(self) -> None:
        low = self._opp("low", confidence=0.50)
        high = self._opp("high", confidence=0.80)

        result = select_best(
            [low, high],
            min_confidence=0.70,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.strategy_name, "high")

    def test_minimum_rr_filters_candidates(self) -> None:
        low = self._opp("low_rr", rr=0.8)
        high = self._opp("high_rr", rr=2.0)

        result = select_best(
            [low, high],
            min_risk_reward=1.5,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.strategy_name, "high_rr")

    def test_no_candidate_meets_requirements_returns_none(self) -> None:
        candidate = self._opp(
            "candidate",
            confidence=0.40,
            rr=0.8,
        )

        result = select_best(
            [candidate],
            min_confidence=0.70,
            min_risk_reward=1.5,
        )

        self.assertIsNone(result)

    def test_empty_selection_returns_none(self) -> None:
        self.assertIsNone(select_best([]))

    def test_fee_penalty_can_change_ranking(self) -> None:
        cheap = self._opp("cheap", fee=0.0001)
        expensive = self._opp("expensive", fee=0.05)

        ranked = rank_opportunities(
            [expensive, cheap],
            RankingWeights(
                confidence=0.0,
                risk_reward=0.0,
                strategy_quality=1.0,
                regime_fit=0.0,
                fee_penalty=1.0,
                exposure_penalty=0.0,
            ),
        )

        self.assertEqual(ranked[0][0].strategy_name, "cheap")

    def test_exposure_penalty_can_change_ranking(self) -> None:
        low_exposure = self._opp("low_exposure", exposure=0.0)
        high_exposure = self._opp("high_exposure", exposure=0.5)

        ranked = rank_opportunities(
            [high_exposure, low_exposure],
            RankingWeights(
                confidence=0.0,
                risk_reward=0.0,
                strategy_quality=1.0,
                regime_fit=0.0,
                fee_penalty=0.0,
                exposure_penalty=1.0,
            ),
        )

        self.assertEqual(ranked[0][0].strategy_name, "low_exposure")


if __name__ == "__main__":
    unittest.main()