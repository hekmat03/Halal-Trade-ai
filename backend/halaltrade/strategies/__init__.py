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
    "RsiMeanReversionStrategy",
    "DonchianBreakoutStrategy",
    "REGISTRY",
    "make_sma_cross",
    "make_rsi_mean_reversion",
    "make_donchian_breakout",
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
    ) -> None:
        if fast < 1 or slow < 2 or fast >= slow:
            raise ValueError("require 1 <= fast < slow")
        self.fast = fast
        self.slow = slow
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.timeframe = timeframe
        self.trade_amount = trade_amount
        self.name = f"sma_{fast}x{slow}"
        self.meta = StrategyMeta(
            name=self.name,
            timeframe=timeframe,
            params={"fast": fast, "slow": slow,
                    "stop_loss_pct": stop_loss_pct,
                    "take_profit_pct": take_profit_pct},
            risk_class="trend-following/medium",
            validated=False,
            notes="Illustrative trend-following example; not validated trading advice.",
        )

    def __call__(self, candles: list[Candle], position: float, equity: float) -> Signal:
        closes = _closes(candles)
        if len(closes) < self.slow + 1:
            return Signal(side=Side.HOLD)
        price = closes[-1]
        fast_now = _sma(closes, self.fast)
        slow_now = _sma(closes, self.slow)
        fast_prev = _sma(closes[:-1], self.fast)
        slow_prev = _sma(closes[:-1], self.slow)
        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now
        if not position and crossed_up:
            return Signal(
                side=Side.BUY,
                price=price,
                stop_loss=price * (1 - self.stop_loss_pct),
                proposed_exit=price * (1 + self.take_profit_pct),
                reason=f"SMA({self.fast}) crossed above SMA({self.slow}) (research only)",
            )
        if position and crossed_down:
            return Signal(
                side=Side.SELL,
                price=price,
                amount=self.trade_amount,
                stop_loss=price * (1 - self.stop_loss_pct),
                reason=f"SMA({self.fast}) crossed below SMA({self.slow}) — close only (research only)",
            )
        # Also emit a plain level signal so walk-forward/backtest exercises real
        # round-trips even on short synthetic series: flat + fast above slow = BUY.
        if not position and len(closes) >= self.slow and fast_now > slow_now:
            # Only when the cross just happened this bar to avoid repeat BUYs —
            # the engine holds at most one position, so repeats are harmless.
            pass
        return Signal(side=Side.HOLD)

    def with_params(self, **over: Any) -> "SmaCrossStrategy":
        kw = {"fast": self.fast, "slow": self.slow,
              "stop_loss_pct": self.stop_loss_pct,
              "take_profit_pct": self.take_profit_pct,
              "timeframe": self.timeframe, "trade_amount": self.trade_amount}
        kw.update(over)
        return SmaCrossStrategy(**kw)  # type: ignore[arg-type]


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
        self.name = f"rsi_{period}_{oversold:g}_{overbought:g}"
        self.meta = StrategyMeta(
            name=self.name,
            timeframe=timeframe,
            params={"period": period, "oversold": oversold,
                    "overbought": overbought,
                    "stop_loss_pct": stop_loss_pct,
                    "take_profit_pct": take_profit_pct},
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
        if not position and rsi < self.oversold:
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
              "timeframe": self.timeframe, "trade_amount": self.trade_amount}
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
    ) -> None:
        if channel < 2:
            raise ValueError("channel must be >= 2")
        self.channel = channel
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.timeframe = timeframe
        self.trade_amount = trade_amount
        self.name = f"donchian_{channel}"
        self.meta = StrategyMeta(
            name=self.name,
            timeframe=timeframe,
            params={"channel": channel,
                    "stop_loss_pct": stop_loss_pct,
                    "take_profit_pct": take_profit_pct},
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
        if not position and price > highest:
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
              "timeframe": self.timeframe, "trade_amount": self.trade_amount}
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
) -> SmaCrossStrategy:
    return SmaCrossStrategy(fast=fast, slow=slow, stop_loss_pct=stop_loss_pct,
                            take_profit_pct=take_profit_pct,
                            timeframe=timeframe, trade_amount=trade_amount)


def make_rsi_mean_reversion(
    period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.04,
    timeframe: str = "1h",
    trade_amount: float = 100.0,
) -> RsiMeanReversionStrategy:
    return RsiMeanReversionStrategy(period=period, oversold=oversold,
                                    overbought=overbought,
                                    stop_loss_pct=stop_loss_pct,
                                    take_profit_pct=take_profit_pct,
                                    timeframe=timeframe, trade_amount=trade_amount)


def make_donchian_breakout(
    channel: int = 20,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.05,
    timeframe: str = "4h",
    trade_amount: float = 100.0,
) -> DonchianBreakoutStrategy:
    return DonchianBreakoutStrategy(channel=channel, stop_loss_pct=stop_loss_pct,
                                    take_profit_pct=take_profit_pct,
                                    timeframe=timeframe, trade_amount=trade_amount)


REGISTRY: dict[str, Callable[..., Any]] = {
    "sma_cross": make_sma_cross,
    "rsi_mean_reversion": make_rsi_mean_reversion,
    "donchian_breakout": make_donchian_breakout,
}


def get_strategy(name: str, **params: Any) -> Any:
    """Build a library strategy by registry name (raises KeyError if unknown)."""
    try:
        factory = REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown strategy {name!r}; known: {sorted(REGISTRY)}")
    return factory(**params)


def list_strategies() -> list[str]:
    return sorted(REGISTRY)
