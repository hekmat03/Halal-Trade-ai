"""Half-Kelly position sizing.

This module answers ONE question: "given a strategy's historical win-rate and
payoff ratio, how much should a single trade risk?" It produces a SUGGESTION
in USDT. It never has the final word — the Risk Gate (``gates/risk.py``) still
enforces ``max_position_size``, ``max_exposure``, ``max_loss_per_trade`` etc as
hard ceilings regardless of what this module proposes. Treat this as advisory
sizing logic sitting *before* the gate, not a replacement for it.

Why half-Kelly, not full Kelly:
Full Kelly maximizes long-run geometric growth but produces large swings in
equity and is extremely sensitive to estimation error in win-rate/payoff
(which are always estimates from a finite backtest sample, never certainties).
Half-Kelly gives up some theoretical growth for a large reduction in variance
and drawdown risk — the standard, conservative choice for real capital.

No lookahead, no magic: every input here must come from CLOSED, historical
trades (backtest or walk-forward results). Never feed it numbers derived from
the trade currently being considered.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["KellyInputs", "kelly_fraction", "half_kelly_position_size"]


@dataclass(frozen=True)
class KellyInputs:
    """Historical stats for one strategy, from backtest/walk-forward results.

    win_rate: fraction of trades that were winners, in [0, 1].
    avg_win: average winning-trade return as a positive fraction (e.g. 0.02 = 2%).
    avg_loss: average losing-trade return as a positive MAGNITUDE (e.g. 0.01 = 1%
      lost), not negative. Callers must pass abs(loss).
    """

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
    """Return the full-Kelly fraction of equity to risk. Never negative.

    f* = W - (1 - W) / R,  where R = avg_win / avg_loss (the payoff ratio).

    If there's no edge (no wins, or losses outweigh wins so badly f* < 0),
    returns 0.0 — meaning "do not size this trade with Kelly at all."
    """
    if inputs.avg_win == 0.0:
        return 0.0  # no positive payoff on record — no statistical edge to size
    if inputs.avg_loss == 0.0:
        # No losing trades on record. This is a red flag for overfitting/too
        # small a sample, not a green light for unlimited sizing. Refuse to
        # extrapolate — force the caller to fall back to a flat/manual size.
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
    """Suggested position size in USDT (quote currency), conservative by design.

    equity: current account equity in USDT.
    inputs: historical win-rate/payoff stats (see KellyInputs).
    max_fraction: hard ceiling on the fraction of equity ever risked in one
      trade, applied regardless of what Kelly math says. Default 25% is
      already generous for a single BTC/USDT spot position; most operators
      should run this much lower (e.g. 0.05-0.10).
    max_position_size: optional absolute USDT cap (mirrors Settings.max_position_size).
      If provided, the final size is also clamped to this value so this
      function can never suggest more than the Risk Gate would allow anyway.

    Returns 0.0 if equity <= 0 or there is no statistical edge.
    """
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
  
