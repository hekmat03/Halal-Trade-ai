"""Market regime detection (Delivery 6).

Classifies the BTC/USDT market regime from OHLCV candles using only local,
deterministic indicator math — no network, no API keys, no randomness:

* MA-slope: fast SMA vs slow SMA separation (trend direction + strength).
* Volatility ratio: ATR(14) / close (high-volatility flag).
* RSI(14): overbought/oversold extremes as supporting evidence.

Regimes: ``trending-up`` | ``trending-down`` | ``ranging`` |
``high-volatility`` (plus ``unknown`` when there is too little data).

Pure functions over ``list[Candle]``; returns a ``Regime`` dataclass with a
name, a confidence score in [0, 1], and a human-readable evidence list.
Research output only — never a trading decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..marketdata.models import Candle

__all__ = ["Regime", "RegimeName", "classify_regime", "rsi", "atr_ratio", "ma_gap_pct"]

RegimeName = Literal["trending-up", "trending-down", "ranging", "high-volatility", "unknown"]

TREND_GAP_PCT = 0.005      # |fast-slow|/close above this => trending
HIGH_VOL_RATIO = 0.03      # ATR(14)/close above this => high-volatility
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
MIN_BARS = 30


@dataclass
class Regime:
    """The classified market regime (research only, not a trade signal)."""

    name: str
    confidence: float            # 0..1
    evidence: list[str] = field(default_factory=list)
    lookback: int = 0            # candles examined


def _sma(values: list[float], window: int) -> float:
    return sum(values[-window:]) / window


def ma_gap_pct(closes: list[float], fast: int = 10, slow: int = 30) -> float | None:
    """Relative (fast SMA - slow SMA) / price; None if not enough bars."""
    if len(closes) < slow or closes[-1] == 0:
        return None
    return (_sma(closes, fast) - _sma(closes, slow)) / closes[-1]


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder RSI; None if fewer than period+1 closes."""
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))


def atr_ratio(candles: list[Candle], period: int = 14) -> float | None:
    """ATR(period) / last close; None if not enough bars."""
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(len(candles) - period, len(candles)):
        high = float(candles[i].high)
        low = float(candles[i].low)
        prev = float(candles[i - 1].close)
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    atr = sum(trs) / len(trs)
    last = float(candles[-1].close)
    return atr / last if last else None


def classify_regime(
    candles: list[Candle],
    *,
    fast: int = 10,
    slow: int = 30,
    lookback: int = 60,
) -> Regime:
    """Classify the regime of the most recent ``lookback`` candles.

    Priority: high-volatility first (a volatile trend is still volatile),
    then trend direction from MA separation, else ranging. ``unknown`` when
    fewer than ``MIN_BARS`` usable candles are supplied.
    """
    window = list(candles[-lookback:]) if lookback > 0 else list(candles)
    if len(window) < MIN_BARS:
        return Regime(
            name="unknown",
            confidence=0.0,
            evidence=[f"only {len(window)} candles (< {MIN_BARS}); regime unknown"],
            lookback=len(window),
        )
    closes = [float(c.close) for c in window]
    evidence: list[str] = []
    gap = ma_gap_pct(closes, fast, slow)
    vol = atr_ratio(window)
    rsi_v = rsi(closes)

    if gap is None:
        return Regime(name="unknown", confidence=0.0,
                      evidence=["insufficient bars for MA separation"],
                      lookback=len(window))
    evidence.append(f"SMA({fast}/{slow}) gap {gap:+.2%} of price")
    if vol is not None:
        evidence.append(f"ATR(14)/close {vol:.2%}")
    if rsi_v is not None:
        evidence.append(f"RSI(14) {rsi_v:.1f}")

    is_volatile = vol is not None and vol > HIGH_VOL_RATIO
    if is_volatile:
        direction = (
            "up" if gap > 0 else "down" if gap < 0 else "choppy"
        )
        conf = min(1.0, 0.55 + min(vol / HIGH_VOL_RATIO - 1.0, 1.0) * 0.3
                   + min(abs(gap) / TREND_GAP_PCT, 2.0) * 0.05)
        return Regime(
            name="high-volatility",
            confidence=round(conf, 3),
            evidence=[f"volatility {vol:.2%} above {HIGH_VOL_RATIO:.0%} threshold; "
                      f"underlying drift {direction}"] + evidence,
            lookback=len(window),
        )
    if gap > TREND_GAP_PCT:
        conf = min(1.0, 0.5 + min(gap / TREND_GAP_PCT - 1.0, 3.0) * 0.12)
        extra = ""
        if rsi_v is not None and rsi_v >= RSI_OVERBOUGHT:
            extra = f"; RSI {rsi_v:.1f} overbought — trend may be extended"
            conf = min(conf, 0.75)
        return Regime(name="trending-up", confidence=round(conf, 3),
                      evidence=[f"fast MA above slow MA by {gap:.2%}{extra}"] + evidence,
                      lookback=len(window))
    if gap < -TREND_GAP_PCT:
        conf = min(1.0, 0.5 + min(abs(gap) / TREND_GAP_PCT - 1.0, 3.0) * 0.12)
        extra = ""
        if rsi_v is not None and rsi_v <= RSI_OVERSOLD:
            extra = f"; RSI {rsi_v:.1f} oversold — downtrend may be extended"
            conf = min(conf, 0.75)
        return Regime(name="trending-down", confidence=round(conf, 3),
                      evidence=[f"fast MA below slow MA by {gap:.2%}{extra}"] + evidence,
                      lookback=len(window))
    conf = min(0.85, 0.5 + (1.0 - abs(gap) / TREND_GAP_PCT) * 0.2)
    return Regime(name="ranging", confidence=round(conf, 3),
                  evidence=[f"MA gap {gap:+.2%} inside ±{TREND_GAP_PCT:.1%} band — no trend"] + evidence,
                  lookback=len(window))
