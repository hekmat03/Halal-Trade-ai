"""Market regime detection — rule-based, deterministic, stdlib-only.

Per the master spec (section 8): the system must classify market conditions
so strategies can be matched to conditions they're actually suited for, and
if the regime is "Unclear" the system must not trade.

This is DELIBERATELY rule-based, not machine learning. The spec allows
"Rule-based + Machine learning (optional)" — rule-based is the simpler,
more auditable choice, and per the project's own overfitting-prevention
principle ("simple parameter sets preferred over complex ones"), that's the
right default. An ML classifier can be layered on later; it should not be
the first version.

Method:
* Trend direction/strength: fast EMA vs slow EMA distance, NORMALIZED by
  ATR (so "how many average true ranges apart are the two EMAs" — this
  makes the strength thresholds meaningful across different volatility
  regimes instead of using a raw price-difference threshold that would mean
  something different at $20k BTC vs $80k BTC).
* Volatility level: current ATR vs its own recent average (ATR of ATR,
  effectively) — high/low/normal relative to the asset's OWN recent history,
  not an absolute number.
* Insufficient data at any step -> UNCLEAR, never guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ..indicators.core import atr as atr_series
from ..indicators.core import ema, sma

__all__ = ["MarketRegime", "RegimeResult", "detect_regime"]


class MarketRegime(str, Enum):
    STRONG_UPTREND = "strong_uptrend"
    WEAK_UPTREND = "weak_uptrend"
    RANGING = "ranging"
    WEAK_DOWNTREND = "weak_downtrend"
    STRONG_DOWNTREND = "strong_downtrend"
    UNCLEAR = "unclear"


class VolatilityLevel(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RegimeResult:
    regime: MarketRegime
    volatility: VolatilityLevel
    trend_strength: Optional[float]  # (fast_ema - slow_ema) / atr; None if unknown
    reason: str

    def tradeable(self) -> bool:
        """Per spec: if regime is Unclear/unreliable, no trade."""
        return self.regime != MarketRegime.UNCLEAR


def detect_regime(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    *,
    fast_period: int = 12,
    slow_period: int = 26,
    atr_period: int = 14,
    vol_lookback: int = 50,
    strong_threshold: float = 1.5,
    weak_threshold: float = 0.3,
    high_vol_ratio: float = 1.3,
    low_vol_ratio: float = 0.7,
) -> RegimeResult:
    """Classify the current bar's market regime from OHLC history.

    All inputs are plain lists (oldest first, current bar last) — same
    convention as ``indicators.core``. Requires enough bars to warm up EMA,
    slow ATR average AND the volatility lookback; if not, returns UNCLEAR
    with an explicit reason rather than guessing.
    """
    n = len(closes)
    min_bars = max(slow_period, atr_period + vol_lookback) + 1
    if n < min_bars or len(highs) != n or len(lows) != n:
        return RegimeResult(
            regime=MarketRegime.UNCLEAR,
            volatility=VolatilityLevel.UNKNOWN,
            trend_strength=None,
            reason=f"insufficient data: have {n} bars, need >= {min_bars}.",
        )

    fast_ema = ema(closes, fast_period)
    slow_ema = ema(closes, slow_period)
    atr_vals = atr_series(highs, lows, closes, atr_period)

    if fast_ema[-1] is None or slow_ema[-1] is None or atr_vals[-1] is None:
        return RegimeResult(
            regime=MarketRegime.UNCLEAR,
            volatility=VolatilityLevel.UNKNOWN,
            trend_strength=None,
            reason="indicator warmup incomplete despite bar count (gap in data?).",
        )

    current_atr = atr_vals[-1]
    if current_atr <= 0:
        return RegimeResult(
            regime=MarketRegime.UNCLEAR,
            volatility=VolatilityLevel.UNKNOWN,
            trend_strength=None,
            reason="ATR is zero or negative — cannot normalize trend strength.",
        )

    trend_strength = (fast_ema[-1] - slow_ema[-1]) / current_atr

    if trend_strength >= strong_threshold:
        regime = MarketRegime.STRONG_UPTREND
    elif trend_strength >= weak_threshold:
        regime = MarketRegime.WEAK_UPTREND
    elif trend_strength <= -strong_threshold:
        regime = MarketRegime.STRONG_DOWNTREND
    elif trend_strength <= -weak_threshold:
        regime = MarketRegime.WEAK_DOWNTREND
    else:
        regime = MarketRegime.RANGING

    # Volatility: compare current ATR to the average ATR over vol_lookback bars.
    recent_atrs = [v for v in atr_vals[-vol_lookback:] if v is not None]
    if len(recent_atrs) < vol_lookback // 2:
        volatility = VolatilityLevel.UNKNOWN
    else:
        avg_atr = sum(recent_atrs) / len(recent_atrs)
        if avg_atr <= 0:
            volatility = VolatilityLevel.UNKNOWN
        else:
            ratio = current_atr / avg_atr
            if ratio >= high_vol_ratio:
                volatility = VolatilityLevel.HIGH
            elif ratio <= low_vol_ratio:
                volatility = VolatilityLevel.LOW
            else:
                volatility = VolatilityLevel.NORMAL

    return RegimeResult(
        regime=regime,
        volatility=volatility,
        trend_strength=trend_strength,
        reason=(
            f"EMA({fast_period})-EMA({slow_period}) = {trend_strength:.2f} ATRs apart; "
            f"volatility={volatility.value}."
        ),
    )
