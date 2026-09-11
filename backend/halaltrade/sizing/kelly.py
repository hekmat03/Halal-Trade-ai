"""Half-Kelly position sizing."""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["KellyInputs", "kelly_fraction", "half_kelly_position_size"]


@dataclass(frozen=True)
class KellyInputs:
    win_rate: float
    avg_win: float
    avg_loss: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.win_rate <= 1.0):
            raise ValueError(f"win_rate must be in [0, 1], got {self.win_rate}")
        if self.avg_win < 0:
            raise ValueError(f"avg_win must be >= 0, got {self.avg_win}")
        if self.avg_loss < 0:
            raise ValueError(
                f"avg_loss must be a positive magnitude, got {self.avg_loss} "
                "(pass abs(loss), not a negative number)"
            )


def kelly_fraction(inputs: KellyInputs) -> float:
    if inputs.avg_win == 0.0:
        return 0.0
    if inputs.avg_loss == 0.0:
        return 0.0
    payoff_ratio = inputs.avg_win / inputs.avg_loss
    f_star = inputs.win_rate - (1.0 - inputs.win_rate) / payoff_ratio
    return max(0.0, f_star)


def half_kelly_position_size(
    equity: float,
    inputs: KellyInputs,
    max_fraction: float = 0.25,
    max_position_size: float | None = None,
) -> float:
    if equity <= 0:
        return 0.0
    if not (0.0 < max_fraction <= 1.0):
        raise ValueError(f"max_fraction must be in (0, 1], got {max_fraction}")

    f_star = kelly_fraction(inputs)
    half = f_star / 2.0
    fraction = min(half, max_fraction)
    size = equity * fraction

    if max_position_size is not None:
        size = min(size, max_position_size)

    return round(size, 2)
