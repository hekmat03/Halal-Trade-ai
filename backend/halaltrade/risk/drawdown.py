"""Drawdown lockout — locks trading after a max-drawdown breach until explicit unlock."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

__all__ = ["DrawdownLockout"]


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DrawdownLockout:
    max_drawdown: float
    _peak: Optional[float] = field(default=None, init=False, repr=False)
    _locked: bool = field(default=False, init=False, repr=False)
    _locked_reason: Optional[str] = field(default=None, init=False, repr=False)
    _locked_at: Optional[datetime] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not (0.0 < self.max_drawdown <= 1.0):
            raise ValueError(f"max_drawdown must be in (0, 1], got {self.max_drawdown}")

    def update(self, equity: float, *, now: Optional[Callable[[], datetime]] = None) -> bool:
        if equity <= 0:
            return self._locked
        if self._peak is None or equity > self._peak:
            self._peak = equity
        if self._locked:
            return True
        drawdown = (self._peak - equity) / self._peak
        if drawdown > self.max_drawdown:
            self._locked = True
            self._locked_reason = (
                f"drawdown {drawdown:.2%} exceeded max_drawdown "
                f"{self.max_drawdown:.2%} (peak={self._peak:.2f}, equity={equity:.2f})"
            )
            self._locked_at = (now or _default_now)()
        return self._locked

    def unlock(self, *, reset_peak_to: Optional[float] = None) -> None:
        self._locked = False
        self._locked_reason = None
        self._locked_at = None
        if reset_peak_to is not None:
            self._peak = reset_peak_to

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def locked_reason(self) -> Optional[str]:
        return self._locked_reason

    @property
    def peak(self) -> Optional[float]:
        return self._peak