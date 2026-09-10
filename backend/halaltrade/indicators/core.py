"""Core technical indicators — pure Python, stdlib only, zero external deps.

Every function takes plain lists of floats (not Candle/pydantic objects) so
this module can be tested and reasoned about in complete isolation from the
rest of the stack. Strategies (in ``backtest/strategies.py``) extract
``.close`` / ``.high`` / ``.low`` from ``Candle`` objects and pass plain
lists in here.

Convention: every function returns a list the SAME LENGTH as the input, with
``None`` in the positions where there isn't enough data yet (the "warmup"
period). This lets a caller index by bar position and safely check
``value is not None`` rather than doing separate length bookkeeping.

No look-ahead: every value at index i is computed ONLY from inputs at
indices <= i. This is a hard invariant — the master spec explicitly
requires avoiding look-ahead bias in backtesting.
"""
from __future__ import annotations

import math
from typing import Optional

__all__ = ["sma", "ema", "rsi", "atr", "bollinger_bands", "donchian_channel"]


def sma(values: list[float], period: int) -> list[Optional[float]]:
    """Simple moving average. ``period`` must be >= 1."""
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    out: list[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if i + 1 < period:
            continue
        window = values[i + 1 - period : i + 1]
        out[i] = sum(window) / period
    return out


def ema(values: list[float], period: int) -> list[Optional[float]]:
    """Exponential moving average, seeded with an SMA of the first `period` bars.

    Standard smoothing factor alpha = 2 / (period + 1).
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[Optional[float]]:
    """Relative Strength Index using Wilder's smoothing method.

    Returns values in [0, 100]. First `period` bars are None (need `period`
    price CHANGES, i.e. `period + 1` prices, to seed the average gain/loss).
    A pathological all-gains or all-losses run yields 100.0 or 0.0 exactly
    (rather than dividing by zero) — RSI's own textbook edge case.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(values)
    out: list[Optional[float]] = [None] * n
    if n < period + 1:
        return out

    gains = []
    losses = []
    for i in range(1, n):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0.0 and avg_gain == 0.0:
            return 50.0
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    out[period] = _rsi_from_avgs(avg_gain, avg_loss)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_from_avgs(avg_gain, avg_loss)

    return out


def atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[Optional[float]]:
    """Average True Range using Wilder's smoothing. Same length inputs required."""
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, closes must be the same length")
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(closes)
    out: list[Optional[float]] = [None] * n
    if n < period + 1:
        return out

    true_ranges: list[float] = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    avg_tr = sum(true_ranges[:period]) / period
    out[period] = avg_tr
    for i in range(period, len(true_ranges)):
        avg_tr = (avg_tr * (period - 1) + true_ranges[i]) / period
        out[i + 1] = avg_tr
    return out


def bollinger_bands(
    values: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    """Returns (middle_band, upper_band, lower_band) — each same length as input.

    Middle band is the SMA; upper/lower are +/- num_std population std devs.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    mid = sma(values, period)
    upper: list[Optional[float]] = [None] * len(values)
    lower: list[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if mid[i] is None:
            continue
        window = values[i + 1 - period : i + 1]
        mean = mid[i]
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return mid, upper, lower


def donchian_channel(
    highs: list[float], lows: list[float], period: int = 20
) -> tuple[list[Optional[float]], list[Optional[float]]]:
    """Returns (upper, lower) — highest high / lowest low over the trailing window.

    IMPORTANT: the window at index i is bars [i-period, i-1] (EXCLUDING the
    current bar). This is deliberate — a breakout strategy checks "did the
    current close break the PRIOR channel", not a channel that already
    includes today's own high/low.
    """
    if not (len(highs) == len(lows)):
        raise ValueError("highs and lows must be the same length")
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(highs)
    upper: list[Optional[float]] = [None] * n
    lower: list[Optional[float]] = [None] * n
    for i in range(n):
        if i < period:
            continue
        window_highs = highs[i - period : i]
        window_lows = lows[i - period : i]
        upper[i] = max(window_highs)
        lower[i] = min(window_lows)
    return upper, lower