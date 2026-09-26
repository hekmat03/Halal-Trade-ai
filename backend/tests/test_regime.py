"""Tests for market regime detection (Delivery 6).

Offline, deterministic, synthetic candles only. Proves the classifier returns
the right regime per synthetic shape, stays deterministic, and reports
``unknown`` on too-short input.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from halaltrade.marketdata import Candle
from halaltrade.research.regime import atr_ratio, classify_regime, ma_gap_pct, rsi

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def mk(close: float, *, i: int = 0, high: float | None = None,
       low: float | None = None) -> Candle:
    return Candle(
        symbol="BTCUSDT", timeframe="1h",
        open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1.0,
        timestamp=BASE + timedelta(hours=i), source="synthetic",
    )


def uptrend(n: int = 60) -> list[Candle]:
    return [mk(100 + i * 2.0, i=i) for i in range(n)]


def downtrend(n: int = 60) -> list[Candle]:
    return [mk(220 - i * 2.0, i=i) for i in range(n)]


def ranging(n: int = 60) -> list[Candle]:
    return [mk(100 + (1 if i % 2 == 0 else -1), i=i) for i in range(n)]


def volatile(n: int = 60) -> list[Candle]:
    out = []
    for i in range(n):
        c = 100 + i * 0.5
        out.append(mk(c, i=i, high=c * 1.06, low=c * 0.94))
    return out


def test_uptrend_classified_trending_up() -> None:
    reg = classify_regime(uptrend())
    assert reg.name == "trending-up"
    assert 0.0 < reg.confidence <= 1.0
    assert reg.evidence


def test_downtrend_classified_trending_down() -> None:
    reg = classify_regime(downtrend())
    assert reg.name == "trending-down"
    assert 0.0 < reg.confidence <= 1.0


def test_ranging_classified_ranging() -> None:
    reg = classify_regime(ranging())
    assert reg.name == "ranging"


def test_high_volatility_flagged() -> None:
    reg = classify_regime(volatile())
    assert reg.name == "high-volatility"


def test_too_short_is_unknown() -> None:
    reg = classify_regime([mk(100, i=i) for i in range(5)])
    assert reg.name == "unknown"
    assert reg.confidence == 0.0


def test_deterministic_same_input_same_output() -> None:
    a = classify_regime(uptrend())
    b = classify_regime(uptrend())
    assert a.name == b.name and a.confidence == b.confidence


def test_indicator_helpers_sane() -> None:
    closes = [float(100 + i) for i in range(40)]
    gap = ma_gap_pct(closes)
    assert gap is not None and gap > 0
    assert ma_gap_pct([100.0, 101.0]) is None  # too short
    assert rsi([100.0, 101.0]) is None
    rsi_up = rsi(closes)
    assert rsi_up is not None and rsi_up > 50
    assert atr_ratio([mk(100, i=i) for i in range(3)]) is None
    vr = atr_ratio(volatile())
    assert vr is not None and vr > 0.03


def test_confidence_in_unit_range() -> None:
    for series in (uptrend(), downtrend(), ranging(), volatile()):
        reg = classify_regime(series)
        assert 0.0 <= reg.confidence <= 1.0
        assert reg.lookback > 0
