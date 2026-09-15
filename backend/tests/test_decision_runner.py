"""Tests for halaltrade.decision.runner.MultiStrategyRunner.

Requires the full halaltrade package (pydantic etc.) to import, like every
other test in this suite. The decision logic itself was independently
verified with lightweight stand-ins during development (see project notes) —
these tests exercise the SAME behavior through the real Signal/Candle types.
"""
from __future__ import annotations

from halaltrade.decision.runner import MultiStrategyRunner, StrategyProfile
from halaltrade.marketdata.models import Candle
from halaltrade.models import Side, Signal
from halaltrade.regime.detector import MarketRegime


def make_candles(price: float = 100.0, n: int = 80) -> list[Candle]:
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe="1h",
            open=price,
            high=price + 0.2,
            low=price - 0.2,
            close=price,
            volume=1.0,
            timestamp=i,
            source="test",
        )
        for i in range(n)
    ]


def strat_high_conf(candles, position, equity):
    return Signal(
        side=Side.BUY,
        price=100.0,
        amount=100.0,
        stop_loss=98.0,
        proposed_exit=104.0,
        confidence=0.9,
    )


def strat_low_conf(candles, position, equity):
    return Signal(
        side=Side.BUY,
        price=100.0,
        amount=100.0,
        stop_loss=99.0,
        proposed_exit=101.0,
        confidence=0.2,
    )


def strat_hold(candles, position, equity):
    return Signal(side=Side.HOLD)


def strat_sell(candles, position, equity):
    return Signal(
        side=Side.SELL,
        price=100.0,
    )


def test_picks_higher_confidence_candidate():
    runner = MultiStrategyRunner(
        profiles=[
            StrategyProfile(
                name="high",
                strategy=strat_high_conf,
                compatible_regimes={MarketRegime.RANGING},
                quality_score=0.7,
            ),
            StrategyProfile(
                name="low",
                strategy=strat_low_conf,
                compatible_regimes={MarketRegime.RANGING},
                quality_score=0.3,
            ),
        ]
    )

    result = runner.evaluate(
        make_candles(),
        position=0.0,
        equity=1000.0,
    )

    assert result.side == Side.BUY
    assert result.stop_loss == 98.0


def test_sell_always_wins_immediately():
    runner = MultiStrategyRunner(
        profiles=[
            StrategyProfile(
                name="sell",
                strategy=strat_sell,
                compatible_regimes=set(),
            ),
            StrategyProfile(
                name="buy",
                strategy=strat_high_conf,
                compatible_regimes=set(),
            ),
        ]
    )

    result = runner.evaluate(
        make_candles(),
        position=1.0,
        equity=1000.0,
    )

    assert result.side == Side.SELL


def test_hold_when_nothing_proposed():
    runner = MultiStrategyRunner(
        profiles=[
            StrategyProfile(
                name="hold_only",
                strategy=strat_hold,
                compatible_regimes=set(),
            )
        ]
    )

    result = runner.evaluate(
        make_candles(),
        position=0.0,
        equity=1000.0,
    )

    assert result.side == Side.HOLD


def test_hold_when_nothing_clears_minimum_confidence():
    runner = MultiStrategyRunner(
        profiles=[
            StrategyProfile(
                name="low",
                strategy=strat_low_conf,
                compatible_regimes=set(),
            )
        ],
        min_confidence=0.5,
    )

    result = runner.evaluate(
        make_candles(),
        position=0.0,
        equity=1000.0,
    )

    assert result.side == Side.HOLD


def test_no_candidates_at_all_returns_hold_not_error():
    runner = MultiStrategyRunner(
        profiles=[]
    )

    result = runner.evaluate(
        make_candles(),
        position=0.0,
        equity=1000.0,
    )

    assert result.side == Side.HOLD
