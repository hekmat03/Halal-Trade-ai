"""Example / candidate strategies for the backtest engine.

IMPORTANT — read before using any of these for real:
None of the strategies in this file are "validated" in the sense the master
spec requires (out-of-sample tested, walk-forward validated, compared against
Buy & Hold). They are real, deterministic trading logic — not toys — but
"real logic" and "validated strategy" are different things. Validation only
happens by actually running BacktestEngine against real historical BTC/USDT
data and reviewing the PerformanceReport, which must be done in a real
Python environment with the project's dependencies installed (this cannot be
faked or skipped). Treat everything here as a candidate for that process,
not a conclusion of it.

Every strategy still: never sizes without a stop-loss on BUY, still passes
through the full gate pipeline, and never guarantees or implies future
performance.
"""
from __future__ import annotations

from typing import Callable

from ..indicators.core import atr as atr_series
from ..indicators.core import bollinger_bands, donchian_channel, ema, rsi
from ..marketdata.models import Candle
from ..models import Side, Signal
from ..regime.detector import MarketRegime, detect_regime
from ..sizing.kelly import KellyInputs, half_kelly_position_size
from .base import Strategy

__all__ = [
    "HoldStrategy",
    "SmaCrossStrategy",
    "make_sma_cross",
    "make_hold",
    "make_ema_trend",
    "make_rsi_mean_reversion",
    "make_donchian_breakout",
    "make_regime_filtered",
]


def _kelly_or_fixed_amount(
    equity: float,
    kelly_inputs: KellyInputs | None,
    kelly_max_fraction: float,
    fixed_notional: float,
) -> float:
    """Shared sizing helper: half-Kelly if stats are supplied, else a flat amount."""
    if kelly_inputs is not None:
        return half_kelly_position_size(
            equity=equity, inputs=kelly_inputs, max_fraction=kelly_max_fraction
        )
    return fixed_notional


class HoldStrategy:
    """No-op / HOLD strategy: never recommends a trade."""

    name = "hold"

    def __call__(self, candles: list[Candle], position: float, equity: float) -> Signal:
        return Signal(side=Side.HOLD)


def make_sma_cross(
    fast: int = 5,
    slow: int = 20,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.04,
    kelly_inputs: KellyInputs | None = None,
    kelly_max_fraction: float = 0.10,
    fixed_notional: float = 100.0,
) -> Strategy:
    """Simple moving-average crossover strategy (example only, not validated)."""

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
            if kelly_inputs is not None:
                amount = half_kelly_position_size(
                    equity=equity, inputs=kelly_inputs, max_fraction=kelly_max_fraction
                )
            else:
                amount = fixed_notional
            if amount <= 0:
                return Signal(side=Side.HOLD, reason="Kelly sizing returned 0 — no edge, staying flat.")
            return Signal(
                side=Side.BUY,
                price=price,
                amount=amount,
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


def make_ema_trend(
    fast: int = 12,
    slow: int = 26,
    atr_period: int = 14,
    atr_mult_stop: float = 2.0,
    atr_mult_target: float = 3.0,
    kelly_inputs: KellyInputs | None = None,
    kelly_max_fraction: float = 0.10,
    fixed_notional: float = 100.0,
) -> Strategy:
    """Trend-following: EMA fast/slow crossover with ATR-based volatility stops."""

    def strategy(candles: list[Candle], position: float, equity: float) -> Signal:
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        if len(closes) < max(slow, atr_period + 1) + 1:
            return Signal(side=Side.HOLD)

        fast_ema = ema(closes, fast)
        slow_ema = ema(closes, slow)
        atr_vals = atr_series(highs, lows, closes, atr_period)

        if fast_ema[-1] is None or slow_ema[-1] is None or fast_ema[-2] is None or slow_ema[-2] is None:
            return Signal(side=Side.HOLD)

        price = closes[-1]
        crossed_up = fast_ema[-2] <= slow_ema[-2] and fast_ema[-1] > slow_ema[-1]
        crossed_down = fast_ema[-2] >= slow_ema[-2] and fast_ema[-1] < slow_ema[-1]

        if not position and crossed_up:
            current_atr = atr_vals[-1]
            if current_atr is None or current_atr <= 0:
                return Signal(side=Side.HOLD, reason="ATR unavailable — cannot set a volatility stop.")
            amount = _kelly_or_fixed_amount(equity, kelly_inputs, kelly_max_fraction, fixed_notional)
            if amount <= 0:
                return Signal(side=Side.HOLD, reason="Kelly sizing returned 0 — no edge, staying flat.")
            return Signal(
                side=Side.BUY,
                price=price,
                amount=amount,
                stop_loss=price - atr_mult_stop * current_atr,
                proposed_exit=price + atr_mult_target * current_atr,
                reason=f"EMA({fast}) crossed above EMA({slow}); ATR-based stop.",
            )
        if position and crossed_down:
            return Signal(side=Side.SELL, price=price, reason=f"EMA({fast}) crossed below EMA({slow}) — close.")
        return Signal(side=Side.HOLD)

    strategy.__name__ = f"ema_trend_{fast}x{slow}"  # type: ignore[attr-defined]
    return strategy


def make_rsi_mean_reversion(
    rsi_period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
    bb_period: int = 20,
    bb_std: float = 2.0,
    stop_loss_pct: float = 0.03,
    kelly_inputs: KellyInputs | None = None,
    kelly_max_fraction: float = 0.10,
    fixed_notional: float = 100.0,
) -> Strategy:
    """Mean-reversion: RSI oversold + price at/below the lower Bollinger Band."""

    def strategy(candles: list[Candle], position: float, equity: float) -> Signal:
        closes = [c.close for c in candles]
        if len(closes) < max(rsi_period + 1, bb_period) + 1:
            return Signal(side=Side.HOLD)

        rsi_vals = rsi(closes, rsi_period)
        mid, upper, lower = bollinger_bands(closes, bb_period, bb_std)

        if rsi_vals[-1] is None or lower[-1] is None or mid[-1] is None:
            return Signal(side=Side.HOLD)

        price = closes[-1]
        current_rsi = rsi_vals[-1]

        if not position and current_rsi < oversold and price <= lower[-1]:
            amount = _kelly_or_fixed_amount(equity, kelly_inputs, kelly_max_fraction, fixed_notional)
            if amount <= 0:
                return Signal(side=Side.HOLD, reason="Kelly sizing returned 0 — no edge, staying flat.")
            return Signal(
                side=Side.BUY,
                price=price,
                amount=amount,
                stop_loss=price * (1 - stop_loss_pct),
                proposed_exit=mid[-1],
                reason=f"RSI={current_rsi:.1f} < {oversold} and price at/below lower Bollinger band.",
            )
        if position and (current_rsi > overbought or price >= mid[-1]):
            return Signal(
                side=Side.SELL, price=price,
                reason=f"RSI={current_rsi:.1f} recovered or price reverted to mean — close.",
            )
        return Signal(side=Side.HOLD)

    strategy.__name__ = f"rsi_mean_revert_{rsi_period}"  # type: ignore[attr-defined]
    return strategy


def make_donchian_breakout(
    channel_period: int = 20,
    atr_period: int = 14,
    atr_mult_stop: float = 2.0,
    kelly_inputs: KellyInputs | None = None,
    kelly_max_fraction: float = 0.10,
    fixed_notional: float = 100.0,
) -> Strategy:
    """Breakout: BUY when price closes above the prior N-bar Donchian high."""

    def strategy(candles: list[Candle], position: float, equity: float) -> Signal:
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        if len(closes) < max(channel_period, atr_period + 1) + 1:
            return Signal(side=Side.HOLD)

        upper, lower = donchian_channel(highs, lows, channel_period)
        atr_vals = atr_series(highs, lows, closes, atr_period)

        if upper[-1] is None or atr_vals[-1] is None:
            return Signal(side=Side.HOLD)

        price = closes[-1]

        if not position and price > upper[-1]:
            current_atr = atr_vals[-1]
            if current_atr is None or current_atr <= 0:
                return Signal(side=Side.HOLD, reason="ATR unavailable — cannot set a volatility stop.")
            amount = _kelly_or_fixed_amount(equity, kelly_inputs, kelly_max_fraction, fixed_notional)
            if amount <= 0:
                return Signal(side=Side.HOLD, reason="Kelly sizing returned 0 — no edge, staying flat.")
            return Signal(
                side=Side.BUY,
                price=price,
                amount=amount,
                stop_loss=price - atr_mult_stop * current_atr,
                reason=f"Price {price:.2f} broke above {channel_period}-bar Donchian high {upper[-1]:.2f}.",
            )
        if position and lower[-1] is not None and price < lower[-1]:
            return Signal(
                side=Side.SELL, price=price,
                reason=f"Price broke below {channel_period}-bar Donchian low — close.",
            )
        return Signal(side=Side.HOLD)

    strategy.__name__ = f"donchian_breakout_{channel_period}"  # type: ignore[attr-defined]
    return strategy


def make_regime_filtered(
    base_strategy: Strategy,
    allowed_regimes: set[MarketRegime],
    *,
    fast_period: int = 12,
    slow_period: int = 26,
    atr_period: int = 14,
    weak_threshold: float = 0.3,
    strong_threshold: float = 1.5,
) -> Strategy:
    """Wrap any strategy so it only opens NEW positions when the market is
    in a regime it was actually designed for.

    IMPORTANT — align the periods: pass the SAME fast/slow/ATR periods used
    by the base strategy, or the regime classification can lag/disagree with
    the base strategy's own signal timing.
    """

    def strategy(candles: list[Candle], position: float, equity: float) -> Signal:
        base_signal = base_strategy(candles, position, equity)

        if base_signal.side != Side.BUY:
            return base_signal

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        regime_result = detect_regime(
            closes, highs, lows,
            fast_period=fast_period, slow_period=slow_period, atr_period=atr_period,
            weak_threshold=weak_threshold, strong_threshold=strong_threshold,
        )

        if regime_result.regime not in allowed_regimes:
            return Signal(
                side=Side.HOLD,
                reason=(
                    f"Regime filter blocked entry: base strategy wanted BUY but "
                    f"regime is {regime_result.regime.value} (allowed: "
                    f"{sorted(r.value for r in allowed_regimes)}). {regime_result.reason}"
                ),
            )
        return base_signal

    base_name = getattr(base_strategy, "__name__", "strategy")
    strategy.__name__ = f"regime_filtered_{base_name}"  # type: ignore[attr-defined]
    return strategy