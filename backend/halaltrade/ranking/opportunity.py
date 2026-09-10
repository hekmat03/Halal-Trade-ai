"""Opportunity ranking (master spec section 10).

When multiple strategies produce a signal on the same bar, this module picks
ONE — the highest-ranked opportunity that clears minimum requirements — or
returns None ("if none qualify -> HOLD", per spec).

This is a composite SCORE, not a probability of profit — it never promises
anything will win. It is a way to prioritize among several competing,
already-gate-eligible signals using measurable, auditable criteria (spec
section 10's ranking criteria), not a guarantee mechanism.

Deliberately simple (weighted sum of normalized factors), per the project's
own overfitting-prevention principle: "simple parameter sets preferred over
complex ones." A more elaborate ranking model can replace this later if a
real, out-of-sample-validated case for it exists — not by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = ["Opportunity", "RankingWeights", "rank_opportunities", "select_best"]


@dataclass(frozen=True)
class Opportunity:
    """One candidate trade, already past the gate pipeline, awaiting selection.

    All scores are expected in comparable ranges (0-1 where noted) so the
    weighted sum is meaningful; callers are responsible for normalizing their
    own strategy-quality and regime-compatibility figures before calling in.
    """

    strategy_name: str
    confidence: float
    risk_reward_ratio: float
    strategy_quality_score: float
    regime_compatibility_score: float
    expected_fee_pct: float = 0.001
    current_exposure_pct: float = 0.0


@dataclass(frozen=True)
class RankingWeights:
    confidence: float = 0.25
    risk_reward: float = 0.25
    strategy_quality: float = 0.25
    regime_fit: float = 0.25
    fee_penalty: float = 1.0
    exposure_penalty: float = 0.5


def _score(opp: Opportunity, weights: RankingWeights) -> float:
    rr_component = min(opp.risk_reward_ratio / 3.0, 1.0)

    raw = (
        weights.confidence * opp.confidence
        + weights.risk_reward * rr_component
        + weights.strategy_quality * opp.strategy_quality_score
        + weights.regime_fit * opp.regime_compatibility_score
    )

    penalty = (
        weights.fee_penalty * opp.expected_fee_pct
        + weights.exposure_penalty * opp.current_exposure_pct
    )

    return raw - penalty


def rank_opportunities(
    opportunities: list[Opportunity],
    weights: Optional[RankingWeights] = None,
) -> list[tuple[Opportunity, float]]:
    """Return (opportunity, score) pairs sorted best-first."""
    w = weights or RankingWeights()

    scored = [(opp, _score(opp, w)) for opp in opportunities]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    return scored


def select_best(
    opportunities: list[Opportunity],
    *,
    weights: Optional[RankingWeights] = None,
    min_confidence: float = 0.0,
    min_risk_reward: float = 0.0,
) -> Optional[Opportunity]:
    """Return the single best opportunity that clears minimum requirements.

    Returns None if the list is empty or nothing clears the minimums.
    """
    eligible = [
        opp
        for opp in opportunities
        if opp.confidence >= min_confidence
        and opp.risk_reward_ratio >= min_risk_reward
    ]

    if not eligible:
        return None

    ranked = rank_opportunities(eligible, weights)
    return ranked[0][0]