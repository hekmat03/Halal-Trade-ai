"""Modular strategy library (Delivery 6).

Research-grade, spot-long-only BTC/USDT strategies for 1h/4h-style bars.

INVARIANTS (non-negotiable):
* Every strategy is RESEARCH OUTPUT — illustrative, NOT validated trading
  advice. Nothing here is a profit guarantee; every signal must still pass the
  four-gate pipeline (Signal -> Shariah -> Risk -> Security -> Execution).
* Spot, 1x, long-only: BUY opens a position, SELL disposes of an owned
  position (exit only — never short). No futures/leverage/margin language.
* Strategies recommend; they never size, never execute.

Each strategy is a class implementing the ``Strategy`` protocol
``(candles, position, equity) -> Signal`` with research metadata:
``name``, ``timeframe``, ``params``, ``risk_class``, ``validated``
(always False until a real walk-forward validation says otherwise), and
``notes``.

SELL exits carry an explicit ``amount`` + ``stop_loss`` so they satisfy the
Risk gate's mandatory-stop + explicit-size rules (the same rule that applies
to every non-HOLD trade). The backtest engine only stamps amount/stop on BUY,
so without this the position could only exit via stop/TP/flat-close.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..marketdata.models import Candle
from ..models import Side, Signal

__all__ = [
    "StrategyMeta",
    "SmaCrossStrategy",
    "EmaTrendStrategy",
    "RsiMeanReversionStrategy",
    "DonchianBreakoutStrategy",
    "REGISTRY",
    "STRATEGY_NAMES",
    "make_sma_cross",
    "make_ema_trend",
    "make_rsi_mean_reversion",
    "make_rsi_mean_reversion_1d",
    "make_donchian_breakout",
    "make_donchian_1d",
    "get_strategy",
    "list_strategies",
]


@dataclass(frozen=True)
class StrategyMeta:
    """Research metadata attached to every library strategy."""

    name: str
    timeframe: str
    params: dict[str, Any]
    risk_class: str
    validated: bool = False
    notes: str = ""


def _closes(candles: list[Candle]) -> list[float]:
    return [float(c.close) for c in candles]


def _sma(values: list[float], window: int) -> float:
    return sum(values[-window:]) / window


def _ema_series(values: list[float], window: int) -> list[float]:
    """Wilder-style EMA over the whole series (returns per-index values)."""
    if not values:
        return []
    k = 2.0 / (window + 1)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(float(v) * k + out[-1] * (1.0 - k))
    return out


def _ma_last(values: list[float], window: int, kind: str) -> float:
    """Last value of a simple or Wilder-style exponential moving average.

    ``kind="sma"`` -> mean of the last ``window`` values; ``kind="ema"`` ->
    last point of the EMA series seeded at ``values[0]`` (same convention as
    ``_ema_series``, so SMA/EMA strategies stay comparable).
    """
    if kind == "ema":
        return _ema_series(values, window)[-1]
    if kind == "sma":
        return _sma(values, window)
    raise ValueError("kind must be 'sma' or 'ema'")


def _volume_ok(candles: list[Candle], lookback: int) -> bool:
    """Volume-confirmation filter: last bar's volume > mean of prior bars.

    ``lookback <= 0`` disables the filter (always True). Research-only —
    it only gates entries, it never changes sizes or exits.
    """
    if lookback <= 0:
        return True
    if len(candles) < lookback + 1:
        return False
    prior = [float(c.volume) for c in candles[-(lookback + 1):-1]]
    return float(candles[-1].volume) > sum(prior) / len(prior)


def _regime_ok(candles: list[Candle], regime_filter: str | None) -> bool:
    """Regime-confirmation filter: trade only when the detected regime matches.

    ``regime_filter is None`` disables the filter (always True). Uses the
    existing ``research.regime`` classifier — research-only, entry gating.
    """
    if not regime_filter:
        return True
    from ..research.regime import classify_regime  # local import: no cycles
    return classify_regime(candles).name == regime_filter


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder RSI over the last ``period`` deltas; None if not enough data."""
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains += delta
        else:
            losses -= delta
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))


class SmaCrossStrategy:
    """Fast/slow simple-moving-average crossover (illustrative, not validated).

    BUY when fast SMA crosses above slow SMA while flat; SELL (close only)
    when fast SMA crosses below slow SMA while holding. Every BUY carries a
    mandatory stop-loss and an optional take-profit; every SELL carries an
    explicit amount + stop so the Risk gate can verify it.
    """

    def __init__(
        self,
        fast: int = 10,
        slow: int = 30,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.04,
        timeframe: str = "1h",
        trade_amount: float = 100.0,
        volume_filter_bars: int = 0,
        regime_filter: str | None = None,
        ma_type: str = "sma",
    ) -> None:
        if fast < 1 or slow < 2 or fast >= slow:
            raise ValueError("require 1 <= fast < slow")
        if ma_type not in ("sma", "ema"):
            raise ValueError("ma_type must be 'sma' or 'ema'")
        self.fast = fast
        self.slow = slow
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.timeframe = timeframe
        self.trade_amount = trade_amount
        self.volume_filter_bars = volume_filter_bars
        self.regime_filter = regime_filter
        self.ma_type = ma_type
        self.name = f"{ma_type}_{fast}x{slow}"
        self.meta = StrategyMeta(
            name=self.name,
            timeframe=timeframe,
            params={"fast": fast, "slow": slow, "ma_type": ma_type,
                    "stop_loss_pct": stop_loss_pct,
                    "take_profit_pct": take_profit_pct,
                    "volume_filter_bars": volume_filter_bars,
                    "regime_filter": regime_filter},
            risk_class="trend-following/medium",
            validated=False,
            notes="Illustrative trend-following example; not validated trading advice.",
        )

    def __call__(self, candles: list[Candle], position: float, equity: float) -> Signal:
        closes = _closes(candles)
        if len(closes) < self.slow + 1:
            return Signal(side=Side.HOLD)
        price = closes[-1]
        fast_now = _ma_last(closes, self.fast, self.ma_type)
        slow_now = _ma_last(closes, self.slow, self.ma_type)
        fast_prev = _ma_last(closes[:-1], self.fast, self.ma_type)
        slow_prev = _ma_last(closes[:-1], self.slow, self.ma_type)
        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now
        if not position and crossed_up and _regime_ok(candles, self.regime_filter) \
                and _volume_ok(candles, self.volume_filter_bars):
            return Signal(
                side=Side.BUY,
                price=price,
                stop_loss=price * (1 - self.stop_loss_pct),
                proposed_exit=price * (1 + self.take_profit_pct),
                reason=(f"{self.ma_type.upper()}({self.fast}) crossed above "
                        f"{self.ma_type.upper()}({self.slow}) (research only)"),
            )
        if position and crossed_down:
            return Signal(
                side=Side.SELL,
                price=price,
                amount=self.trade_amount,
                stop_loss=price * (1 - self.stop_loss_pct),
                reason=(f"{self.ma_type.upper()}({self.fast}) crossed below "
                        f"{self.ma_type.upper()}({self.slow}) — close only "
                        "(research only)"),
            )
        return Signal(side=Side.HOLD)

    def with_params(self, **over: Any) -> "SmaCrossStrategy":
        kw = {"fast": self.fast, "slow": self.slow,
              "stop_loss_pct": self.stop_loss_pct,
              "take_profit_pct": self.take_profit_pct,
              "timeframe": self.timeframe, "trade_amount": self.trade_amount,
              "volume_filter_bars": self.volume_filter_bars,
              "regime_filter": self.regime_filter,
              "ma_type": self.ma_type}
        kw.update(over)
        return SmaCrossStrategy(**kw)  # type: ignore[arg-type]


class EmaTrendStrategy(SmaCrossStrategy):
    """EMA fast/slow trend-following (research output — NOT validated advice).

    Identical mechanics to :class:`SmaCrossStrategy` with ``ma_type="ema"``:
    BUY when the fast EMA crosses above the slow EMA while flat (mandatory
    stop-loss + optional take-profit), SELL only to dispose of an owned
    position. The optional volume/regime filters gate **entries only** — they
    never change size and never block an exit. Spot, 1x, long-only.

    Registered as its own family so the walk-forward sweep can report the
    EMA-trend family separately from SMA-crossover (see
    ``docs/strategy_research.md``).
    """

    def __init__(
        self,
        fast: int = 10,
        slow: int = 30,
        stop_loss_pct: float = 0.03,
        take_profit_pct: float = 0.06,
        timeframe: str = "1d",
        trade_amount: float = 100.0,
        volume_filter_bars: int = 0,
        regime_filter: str | None = None,
        ma_type: str = "ema",
    ) -> None:
        super().__init__(fast=fast, slow=slow, stop_loss_pct=stop_loss_pct,
                         take_profit_pct=take_profit_pct, timeframe=timeframe,
                         trade_amount=trade_amount,
                         volume_filter_bars=volume_filter_bars,
                         regime_filter=regime_filter, ma_type=ma_type)
        self.meta = StrategyMeta(
            name=self.name,
            timeframe=timeframe,
            params={"fast": fast, "slow": slow, "ma_type": ma_type,
                    "stop_loss_pct": stop_loss_pct,
                    "take_profit_pct": take_profit_pct,
                    "volume_filter_bars": volume_filter_bars,
                    "regime_filter": regime_filter},
            risk_class="trend-following/medium",
            validated=False,
            notes="EMA fast/slow crossover (research candidate; not validated "
                  "trading advice).",
        )

    def with_params(self, **over: Any) -> "EmaTrendStrategy":
        kw = {"fast": self.fast, "slow": self.slow,
              "stop_loss_pct": self.stop_loss_pct,
              "take_profit_pct": self.take_profit_pct,
              "timeframe": self.timeframe, "trade_amount": self.trade_amount,
              "volume_filter_bars": self.volume_filter_bars,
              "regime_filter": self.regime_filter,
              "ma_type": self.ma_type}
        kw.update(over)
        return EmaTrendStrategy(**kw)  # type: ignore[arg-type]


class RsiMeanReversionStrategy:
    """RSI mean-reversion (illustrative, not validated).

    BUY when RSI(period) dips below ``oversold`` while flat (bounce thesis);
    SELL (close only) when RSI rises above ``overbought`` while holding.
    Spot-long-only: the oversold leg never shorts, the overbought leg only
    disposes of an owned position.
    """

    def __init__(
        self,
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.04,
        timeframe: str = "1h",
        trade_amount: float = 100.0,
        volume_filter_bars: int = 0,
        regime_filter: str | None = None,
    ) -> None:
        if period < 2:
            raise ValueError("RSI period must be >= 2")
        if not (0 < oversold < overbought < 100):
            raise ValueError("require 0 < oversold < overbought < 100")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.timeframe = timeframe
        self.trade_amount = trade_amount
        self.volume_filter_bars = volume_filter_bars
        self.regime_filter = regime_filter
        self.name = f"rsi_{period}_{oversold:g}_{overbought:g}"
        self.meta = StrategyMeta(
            name=self.name,
            timeframe=timeframe,
            params={"period": period, "oversold": oversold,
                    "overbought": overbought,
                    "stop_loss_pct": stop_loss_pct,
                    "take_profit_pct": take_profit_pct,
                    "volume_filter_bars": volume_filter_bars,
                    "regime_filter": regime_filter},
            risk_class="mean-reversion/medium",
            validated=False,
            notes="Illustrative mean-reversion example; not validated trading advice.",
        )

    def __call__(self, candles: list[Candle], position: float, equity: float) -> Signal:
        closes = _closes(candles)
        rsi = _rsi(closes, self.period)
        if rsi is None:
            return Signal(side=Side.HOLD)
        price = closes[-1]
        if not position and rsi < self.oversold and _regime_ok(candles, self.regime_filter) \
                and _volume_ok(candles, self.volume_filter_bars):
            return Signal(
                side=Side.BUY,
                price=price,
                stop_loss=price * (1 - self.stop_loss_pct),
                proposed_exit=price * (1 + self.take_profit_pct),
                reason=f"RSI({self.period})={rsi:.1f} below oversold {self.oversold} (research only)",
            )
        if position and rsi > self.overbought:
            return Signal(
                side=Side.SELL,
                price=price,
                amount=self.trade_amount,
                stop_loss=price * (1 - self.stop_loss_pct),
                reason=f"RSI({self.period})={rsi:.1f} above overbought {self.overbought} — close only (research only)",
            )
        return Signal(side=Side.HOLD)

    def with_params(self, **over: Any) -> "RsiMeanReversionStrategy":
        kw = {"period": self.period, "oversold": self.oversold,
              "overbought": self.overbought,
              "stop_loss_pct": self.stop_loss_pct,
              "take_profit_pct": self.take_profit_pct,
              "timeframe": self.timeframe, "trade_amount": self.trade_amount,
              "volume_filter_bars": self.volume_filter_bars,
              "regime_filter": self.regime_filter}
        kw.update(over)
        return RsiMeanReversionStrategy(**kw)  # type: ignore[arg-type]


class DonchianBreakoutStrategy:
    """Donchian-channel breakout (illustrative, not validated).

    BUY when the close exceeds the highest high of the last ``channel``
    bars while flat; SELL (close only) when the close falls below the
    lowest low of the last ``channel`` bars while holding. A mandatory
    stop-loss + take-profit rides on every BUY.
    """

    def __init__(
        self,
        channel: int = 20,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.05,
        timeframe: str = "4h",
        trade_amount: float = 100.0,
        volume_filter_bars: int = 0,
        regime_filter: str | None = None,
    ) -> None:
        if channel < 2:
            raise ValueError("channel must be >= 2")
        self.channel = channel
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.timeframe = timeframe
        self.trade_amount = trade_amount
        self.volume_filter_bars = volume_filter_bars
        self.regime_filter = regime_filter
        self.name = f"donchian_{channel}"
        self.meta = StrategyMeta(
            name=self.name,
            timeframe=timeframe,
            params={"channel": channel,
                    "stop_loss_pct": stop_loss_pct,
                    "take_profit_pct": take_profit_pct,
                    "volume_filter_bars": volume_filter_bars,
                    "regime_filter": regime_filter},
            risk_class="breakout/high",
            validated=False,
            notes="Illustrative breakout example; not validated trading advice.",
        )

    def __call__(self, candles: list[Candle], position: float, equity: float) -> Signal:
        if len(candles) < self.channel + 1:
            return Signal(side=Side.HOLD)
        price = float(candles[-1].close)
        window = candles[-(self.channel + 1):-1]
        highest = max(float(c.high) for c in window)
        lowest = min(float(c.low) for c in window)
        if not position and price > highest and _regime_ok(candles, self.regime_filter) \
                and _volume_ok(candles, self.volume_filter_bars):
            return Signal(
                side=Side.BUY,
                price=price,
                stop_loss=price * (1 - self.stop_loss_pct),
                proposed_exit=price * (1 + self.take_profit_pct),
                reason=f"Donchian({self.channel}) breakout above {highest:.2f} (research only)",
            )
        if position and price < lowest:
            return Signal(
                side=Side.SELL,
                price=price,
                amount=self.trade_amount,
                stop_loss=price * (1 - self.stop_loss_pct),
                reason=f"Donchian({self.channel}) breakdown below {lowest:.2f} — close only (research only)",
            )
        return Signal(side=Side.HOLD)

    def with_params(self, **over: Any) -> "DonchianBreakoutStrategy":
        kw = {"channel": self.channel,
              "stop_loss_pct": self.stop_loss_pct,
              "take_profit_pct": self.take_profit_pct,
              "timeframe": self.timeframe, "trade_amount": self.trade_amount,
              "volume_filter_bars": self.volume_filter_bars,
              "regime_filter": self.regime_filter}
        kw.update(over)
        return DonchianBreakoutStrategy(**kw)  # type: ignore[arg-type]


# --- Factories (param-grid friendly) ------------------------------------------

def make_sma_cross(
    fast: int = 10,
    slow: int = 30,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.04,
    timeframe: str = "1h",
    trade_amount: float = 100.0,
    volume_filter_bars: int = 0,
    regime_filter: str | None = None,
    ma_type: str = "sma",
) -> SmaCrossStrategy:
    return SmaCrossStrategy(fast=fast, slow=slow, stop_loss_pct=stop_loss_pct,
                            take_profit_pct=take_profit_pct,
                            timeframe=timeframe, trade_amount=trade_amount,
                            volume_filter_bars=volume_filter_bars,
                            regime_filter=regime_filter, ma_type=ma_type)


def make_rsi_mean_reversion(
    period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.04,
    timeframe: str = "1h",
    trade_amount: float = 100.0,
    volume_filter_bars: int = 0,
    regime_filter: str | None = None,
) -> RsiMeanReversionStrategy:
    return RsiMeanReversionStrategy(period=period, oversold=oversold,
                                    overbought=overbought,
                                    stop_loss_pct=stop_loss_pct,
                                    take_profit_pct=take_profit_pct,
                                    timeframe=timeframe, trade_amount=trade_amount,
                                    volume_filter_bars=volume_filter_bars,
                                    regime_filter=regime_filter)


def make_donchian_breakout(
    channel: int = 20,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.05,
    timeframe: str = "4h",
    trade_amount: float = 100.0,
    volume_filter_bars: int = 0,
    regime_filter: str | None = None,
) -> DonchianBreakoutStrategy:
    return DonchianBreakoutStrategy(channel=channel, stop_loss_pct=stop_loss_pct,
                                    take_profit_pct=take_profit_pct,
                                    timeframe=timeframe, trade_amount=trade_amount,
                                    volume_filter_bars=volume_filter_bars,
                                    regime_filter=regime_filter)


def make_ema_trend(
    fast: int = 10,
    slow: int = 30,
    stop_loss_pct: float = 0.03,
    take_profit_pct: float = 0.06,
    timeframe: str = "1d",
    trade_amount: float = 100.0,
    volume_filter_bars: int = 0,
    regime_filter: str | None = None,
    ma_type: str = "ema",
) -> EmaTrendStrategy:
    return EmaTrendStrategy(fast=fast, slow=slow, stop_loss_pct=stop_loss_pct,
                            take_profit_pct=take_profit_pct,
                            timeframe=timeframe, trade_amount=trade_amount,
                            volume_filter_bars=volume_filter_bars,
                            regime_filter=regime_filter, ma_type=ma_type)


def make_rsi_mean_reversion_1d(
    period: int = 7,
    oversold: float = 30.0,
    overbought: float = 70.0,
    stop_loss_pct: float = 0.03,
    take_profit_pct: float = 0.06,
    timeframe: str = "1d",
    trade_amount: float = 100.0,
    volume_filter_bars: int = 0,
    regime_filter: str | None = None,
) -> RsiMeanReversionStrategy:
    """1d RSI(7) mean-reversion — a researched candidate, NOT advice.

    Named 1d preset around the strongest RSI settings seen in the Delivery 6
    walk-forward sweep on real Binance BTC/USDT (data.binance.vision). Numbers
    and the honest verdict per sweep run live in ``docs/strategy_research.md``
    and ``data/results/walkforward_results.json``; ``validated`` stays False
    until a sweep actually clears the >=70% OOS-window bar, and even then this
    is research output, never a profit guarantee.
    """
    return RsiMeanReversionStrategy(period=period, oversold=oversold,
                                    overbought=overbought,
                                    stop_loss_pct=stop_loss_pct,
                                    take_profit_pct=take_profit_pct,
                                    timeframe=timeframe, trade_amount=trade_amount,
                                    volume_filter_bars=volume_filter_bars,
                                    regime_filter=regime_filter)


def make_donchian_1d(
    channel: int = 15,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.05,
    timeframe: str = "1d",
    trade_amount: float = 100.0,
    volume_filter_bars: int = 0,
    regime_filter: str | None = None,
) -> DonchianBreakoutStrategy:
    """1d Donchian breakout, channel 15 — a researched candidate, NOT advice.

    Exact parameter set that cleared the owner's >=70% out-of-sample-window bar
    in the Delivery 6 sweep: 5 of 6 folds positive, mean OOS +0.45% per window,
    worst OOS drawdown 0.44%, 19 OOS trades, on real Binance BTC/USDT 1d klines
    (2024-01..2026-09). The full table, the caveats (the margin over channels
    10/12 rests on one essentially-flat window) and the whole-history backtest
    live in ``docs/strategy_research.md``. Research output; ``validated`` stays
    False — not a profit guarantee, and it never bypasses a gate.
    """
    return DonchianBreakoutStrategy(channel=channel,
                                    stop_loss_pct=stop_loss_pct,
                                    take_profit_pct=take_profit_pct,
                                    timeframe=timeframe,
                                    trade_amount=trade_amount,
                                    volume_filter_bars=volume_filter_bars,
                                    regime_filter=regime_filter)


# The complete, documented set of strategies this library ships. The library
# test asserts ``list_strategies()`` matches this exactly, so the set is
# allowed to GROW as strategies are added — but only deliberately, and every
# entry must stay reachable through ``get_strategy(name)``. No entry is ever
# removed silently.
STRATEGY_NAMES: tuple[str, ...] = (
    "donchian_breakout",     # Donchian channel breakout (4h/1d)
    "donchian_1d",           # Donchian breakout, 1d research preset (ch 15)
    "ema_trend",             # EMA fast/slow trend-following (1d/4h)
    "rsi_mean_reversion",    # RSI mean-reversion (generic defaults)
    "rsi_mean_reversion_1d", # RSI mean-reversion, 1d research preset
    "sma_cross",             # SMA fast/slow crossover
)

REGISTRY: dict[str, Callable[..., Any]] = {
    "sma_cross": make_sma_cross,
    "ema_trend": make_ema_trend,
    "rsi_mean_reversion": make_rsi_mean_reversion,
    "donchian_breakout": make_donchian_breakout,
    "rsi_mean_reversion_1d": make_rsi_mean_reversion_1d,
    "donchian_1d": make_donchian_1d,
}
assert set(REGISTRY) == set(STRATEGY_NAMES), "REGISTRY/STRATEGY_NAMES drifted"


def get_strategy(name: str, **params: Any) -> Any:
    """Build a library strategy by registry name (raises KeyError if unknown)."""
    try:
        factory = REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown strategy {name!r}; known: {sorted(REGISTRY)}")
    return factory(**params)


def list_strategies() -> list[str]:
    return sorted(REGISTRY)
