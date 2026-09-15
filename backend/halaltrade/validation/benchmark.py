"""Buy & Hold benchmark (master spec section 15 validation criteria):
"Must outperform benchmark (Buy & Hold) OR have higher Sharpe ratio than
benchmark." This has never actually been checked in this project until now —
every walk-forward result so far reported a strategy's own numbers in
isolation, with no comparison point to say whether "-0.09% avg return" is
actually bad, or just reflects a bad period for BTC itself.

Pure function, dependency-free apart from plain floats — testable in
isolation, and reusable by any backtest/walk-forward report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["BuyAndHoldResult", "buy_and_hold_return", "compare_to_benchmark"]


@dataclass(frozen=True)
class BuyAndHoldResult:
    total_return: float
    sharpe: float
    max_drawdown: float


def buy_and_hold_return(
    closes: list[float], periods_per_year: float = 8760.0
) -> BuyAndHoldResult:
    """Compute Buy & Hold return/Sharpe/drawdown over closing prices."""
    if len(closes) < 2:
        raise ValueError("need at least 2 closing prices to compute a return")
    if any(c <= 0 for c in closes):
        raise ValueError("all prices must be positive")

    total_return = (closes[-1] - closes[0]) / closes[0]

    bar_returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
    ]

    mean_r = sum(bar_returns) / len(bar_returns)

    if len(bar_returns) > 1:
        variance = sum(
            (r - mean_r) ** 2 for r in bar_returns
        ) / (len(bar_returns) - 1)
        std_r = math.sqrt(variance)
    else:
        std_r = 0.0

    sharpe = (
        (mean_r / std_r) * math.sqrt(periods_per_year)
        if std_r > 0
        else 0.0
    )

    peak = closes[0]
    max_dd = 0.0

    for price in closes:
        if price > peak:
            peak = price

        dd = (peak - price) / peak

        if dd > max_dd:
            max_dd = dd

    return BuyAndHoldResult(
        total_return=total_return,
        sharpe=sharpe,
        max_drawdown=max_dd,
    )


def compare_to_benchmark(
    strategy_return: float,
    strategy_sharpe: float,
    benchmark: BuyAndHoldResult,
) -> str:
    """Pass if strategy beats Buy & Hold on return OR Sharpe."""
    beats_return = strategy_return > benchmark.total_return
    beats_sharpe = strategy_sharpe > benchmark.sharpe

    verdict = "PASSES" if (beats_return or beats_sharpe) else "FAILS"

    return (
        f"{verdict} vs Buy&Hold: "
        f"strategy return={strategy_return:+.2%} "
        f"(B&H={benchmark.total_return:+.2%}), "
        f"strategy Sharpe={strategy_sharpe:.2f} "
        f"(B&H={benchmark.sharpe:.2f})"
    )
