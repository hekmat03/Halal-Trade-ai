"""Example strategies for the backtest engine.

These are **illustrative examples for testing only** — they are NOT validated
trading strategies and are clearly labelled as such. Do not use them for any
real trading decision. The business plan treats all strategies as research
output that must still pass the policy gates.
"""
from __future__ import annotations

from typing import Callable

from ..marketdata.models import Candle
from ..models import Side, Signal
from .base import Strategy

__all__ = ["HoldStrategy", "SmaCrossStrategy", "make_sma_cross", "make_hold"]


class HoldStrategy:
    """No-op / HOLD strategy: never recommends a trade.

    Included so the pipeline's deliberate "do nothing" path is exercised:
    HOLD signals carry no size and are rejected by the Risk gate, resolving to
    NO TRADE. This is an example, not a strategy.
    """

    name = "hold"

    def __call__(self, candles: list[Candle], position: float, equity: float) -> Signal:
        return Signal(side=Side.HOLD)


def make_sma_cross(
    fast: int = 5,
    slow: int = 20,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.04,
) -> Strategy:
    """Simple moving-average crossover strategy (example only, not validated).

    BUY when the fast SMA crosses above the slow SMA and there is no position;
    SELL (close) when the fast SMA crosses below the slow SMA while holding.
    Every BUY carries a mandatory stop-loss and an optional take-profit derived
    from the requested percentages, so it satisfies the Risk gate.
    """

    def _sma(closes: list[float], window: int) -> float:
        return sum(closes[-window:]) / window

    def strategy(candles: list[Candle], position: float, equity: float) -> Signal:
        closes = [c.close for c in candles]
        if len(closes) < slow:
            return Signal(side=Side.HOLD)
        price = closes[-1]
        fast_ma = _sma(closes, fast)
        slow_ma = _sma(closes, slow)
        if not position and fast_ma > slow_ma:
            return Signal(
                side=Side.BUY,
                price=price,
                stop_loss=price * (1 - stop_loss_pct),
                proposed_exit=price * (1 + take_profit_pct),
                reason="SMA cross up (example)",
            )
        if position and fast_ma < slow_ma:
            return Signal(
                side=Side.SELL,
                price=price,
                reason="SMA cross down (example) — close position",
            )
        return Signal(side=Side.HOLD)

    strategy.__name__ = f"sma_{fast}x{slow}"  # type: ignore[attr-defined]
    return strategy


def make_hold() -> Strategy:
    """Return a reusable HOLD no-op strategy instance."""
    return HoldStrategy()
