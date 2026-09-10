"""Walk-forward validation (master spec section 15).

"A strategy should NOT go live merely because it performs well on historical
data. Walk-forward validation is required before approval."

Method: roll a fixed-size TRAIN window and a following TEST window forward
through the candle history, non-overlapping test windows by default. The
strategy is not fit/optimized inside this module (this codebase's strategies
are fixed-parameter, not curve-fit) — what this validates is CONSISTENCY:
does the strategy hold up across many different, non-overlapping stretches
of history, or does it only work on one lucky period?

No look-ahead: every test window strictly follows its train window in time.
generate_walk_forward_windows() is pure index arithmetic (no candle data
touched) so it's fully unit-testable in isolation. run_walk_forward() wires
it to BacktestEngine and requires the project's real dependencies (pydantic
etc) — see the module-level note in ``backtest/engine.py`` testing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..backtest.base import Strategy
    from ..backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
    from ..marketdata.models import Candle

__all__ = ["WalkForwardWindow", "generate_walk_forward_windows", "run_walk_forward"]


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: int
    train_end: int   # exclusive
    test_start: int
    test_end: int    # exclusive


def generate_walk_forward_windows(
    n_bars: int,
    train_size: int,
    test_size: int,
    step: Optional[int] = None,
) -> list[WalkForwardWindow]:
    """Generate rolling (train, test) index windows over ``n_bars`` bars.

    ``step`` defaults to ``test_size`` (non-overlapping test windows, the
    standard walk-forward layout). All indices are into a candle list of
    length ``n_bars``; ``test_end`` never exceeds ``n_bars``. Returns an
    empty list if there isn't enough data for even one full window — never
    a partial/truncated window that could distort the results.
    """
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must both be > 0")
    step = step if step is not None else test_size
    if step <= 0:
        raise ValueError("step must be > 0")

    windows: list[WalkForwardWindow] = []
    train_start = 0
    while True:
        train_end = train_start + train_size
        test_start = train_end
        test_end = test_start + test_size
        if test_end > n_bars:
            break
        windows.append(
            WalkForwardWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        train_start += step
    return windows


def run_walk_forward(
    engine: "BacktestEngine",
    strategy_factory,
    candles: list["Candle"],
    config: Optional["BacktestConfig"] = None,
    *,
    train_size: int,
    test_size: int,
    step: Optional[int] = None,
    strategy_name: str | None = None,
) -> list[tuple[WalkForwardWindow, "BacktestResult"]]:
    """Run the OUT-OF-SAMPLE (test) leg of every walk-forward window.

    ``strategy_factory`` is a zero-argument callable returning a fresh
    Strategy instance for each window (e.g. ``lambda: make_ema_trend()``) —
    a fresh instance per window avoids any accidental state leaking from one
    window's test run into the next, which would itself be a subtle form of
    look-ahead.

    This codebase's strategies use fixed, hand-chosen parameters rather than
    being fit to the train window — so "train" here is not used for parameter
    optimization (there is nothing to fit). It exists so the windowing is a
    genuine, standard walk-forward layout that a future parameter-search step
    could plug into, and so the reported results are always evaluated on data
    the strategy has not "seen" immediately before.

    Returns one (window, BacktestResult) pair per window, computed ONLY over
    each window's test slice — never the train slice — so every reported
    result is genuinely out-of-sample.
    """
    windows = generate_walk_forward_windows(len(candles), train_size, test_size, step)
    results: list[tuple[WalkForwardWindow, "BacktestResult"]] = []
    for window in windows:
        test_candles = candles[window.test_start : window.test_end]
        strategy = strategy_factory()
        result = engine.run(strategy, test_candles, config, strategy_name=strategy_name)
        results.append((window, result))
    return results