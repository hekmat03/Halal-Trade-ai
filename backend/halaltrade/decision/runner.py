"""Multi-strategy decision runner — connects regime detection, opportunity
ranking, and the strategy library into ONE decision per bar.

This is the piece that was missing: ``regime/detector.py`` and
``ranking/opportunity.py`` existed as separate, working tools, but nothing
called both of them together with real strategies. This module is that glue.

Per-bar flow:
1. Run EVERY registered strategy against the current candle window.
2. Any strategy that says SELL (closing an existing position) is honored
   IMMEDIATELY — exits are urgent and never compete against other
   candidates. Only ONE SELL is possible at a time anyway (one position).
3. Otherwise, collect every BUY candidate, score each one as an
   ``Opportunity`` (regime fit computed fresh, confidence/risk-reward read
   from the Signal or reasonably defaulted), and hand them to ``select_best()``.
   If none clear the minimums, or none exist, HOLD.

This does NOT invent a new ranking algorithm — it reuses
``ranking.opportunity.select_best`` exactly as built, so the "if none
qualify -> HOLD" and scoring rules are the same ones already tested there.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..backtest.base import Strategy
from ..marketdata.models import Candle
from ..models import Side, Signal
from ..ranking.opportunity import Opportunity, RankingWeights, select_best
from ..regime.detector import MarketRegime, detect_regime

__all__ = ["StrategyProfile", "MultiStrategyRunner"]


@dataclass(frozen=True)
class StrategyProfile:
    """Static metadata about one strategy, used only for scoring — never for
    changing the strategy's own trading logic.

    compatible_regimes: which MarketRegime values this strategy is actually
      designed for (used to compute regime_compatibility_score).
    quality_score: 0-1, caller-supplied from past backtest/walk-forward
      results (see ``validation/walkforward.py``). Defaults to a neutral 0.5
      when unknown — NEVER silently assumed to be good (0.5, not 1.0).
    """

    name: str
    strategy: Strategy
    compatible_regimes: set[MarketRegime]
    quality_score: float = 0.5


@dataclass
class MultiStrategyRunner:
    """Runs multiple strategies each bar and picks ONE opportunity, or HOLD.

    fast_period/slow_period/atr_period control the regime detector used for
    scoring — independent of whatever periods individual strategies use
    internally (this only affects the *ranking* input, not any strategy's
    own entry/exit logic).
    """

    profiles: list[StrategyProfile]
    ranking_weights: Optional[RankingWeights] = None
    min_confidence: float = 0.0
    min_risk_reward: float = 0.0
    regime_fast_period: int = 12
    regime_slow_period: int = 26
    regime_atr_period: int = 14
    expected_fee_pct: float = 0.002  # round-trip fee+slippage estimate

    def evaluate(
        self,
        candles: list[Candle],
        position: float,
        equity: float,
    ) -> Signal:
        """Return exactly ONE Signal for this bar: a SELL (if exiting), the
        single best-ranked BUY, or HOLD if nothing qualifies.
        """
        raw_signals: dict[str, Signal] = {}

        for profile in self.profiles:
            raw_signals[profile.name] = profile.strategy(
                candles,
                position,
                equity,
            )

        # Exits are urgent and never compete -- honor the FIRST sell found.
        for name, signal in raw_signals.items():
            if signal.side == Side.SELL:
                return signal

        buy_candidates = {
            name: sig
            for name, sig in raw_signals.items()
            if sig.side == Side.BUY
        }

        if not buy_candidates:
            return Signal(
                side=Side.HOLD,
                reason="No strategy proposed a trade this bar.",
            )

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        regime_result = detect_regime(
            closes,
            highs,
            lows,
            fast_period=self.regime_fast_period,
            slow_period=self.regime_slow_period,
            atr_period=self.regime_atr_period,
        )

        opportunities: dict[str, Opportunity] = {}

        for profile in self.profiles:
            signal = buy_candidates.get(profile.name)

            if signal is None:
                continue

            if regime_result.regime == MarketRegime.UNCLEAR:
                regime_fit = 0.0
            elif regime_result.regime in profile.compatible_regimes:
                regime_fit = 1.0
            else:
                regime_fit = 0.2

            confidence = (
                signal.confidence
                if signal.confidence > 0
                else 0.5
            )

            risk_reward = signal.risk_reward

            if (
                risk_reward is None
                and signal.stop_loss is not None
                and signal.price is not None
            ):
                risk = signal.price - signal.stop_loss

                reward_target = (
                    signal.proposed_exit
                    if signal.proposed_exit is not None
                    else signal.price
                )

                risk_reward = (
                    (reward_target - signal.price) / risk
                    if risk > 0
                    else 1.0
                )

            if risk_reward is None:
                risk_reward = 1.0

            opportunities[profile.name] = Opportunity(
                strategy_name=profile.name,
                confidence=confidence,
                risk_reward_ratio=max(
                    risk_reward,
                    0.0,
                ),
                strategy_quality_score=profile.quality_score,
                regime_compatibility_score=regime_fit,
                expected_fee_pct=self.expected_fee_pct,
                current_exposure_pct=0.0,
            )

        best = select_best(
            list(opportunities.values()),
            weights=self.ranking_weights,
            min_confidence=self.min_confidence,
            min_risk_reward=self.min_risk_reward,
        )

        if best is None:
            return Signal(
                side=Side.HOLD,
                reason=(
                    f"{len(buy_candidates)} candidate(s) proposed but "
                    f"none cleared minimums "
                    f"(min_confidence={self.min_confidence}, "
                    f"min_risk_reward={self.min_risk_reward})."
                ),
            )

        winning_signal = buy_candidates[best.strategy_name]

        return winning_signal.model_copy(
            update={
                "reason": (
                    f"[selected over {len(buy_candidates) - 1} "
                    f"other candidate(s) via regime="
                    f"{regime_result.regime.value}] "
                    f"{winning_signal.reason}"
                )
            }
              )
