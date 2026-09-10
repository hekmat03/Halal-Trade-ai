"""Drawdown lockout — the missing enforcement behind ``max_drawdown``.

Problem this fixes: today, ``RiskGate`` checks drawdown per-trade (in
``gates/risk.py``) but a breach only rejects THAT ONE signal. The very next
signal is evaluated fresh with no memory of the breach, so the bot can keep
proposing trades one after another right through a blown drawdown limit.

``DrawdownLockout`` adds real memory: once equity falls more than
``max_drawdown`` below its peak, the account is LOCKED. While locked, every
trade must be rejected regardless of that trade's own merits — this state is
read by the Risk Gate via ``RiskAccount.trading_locked`` (see ``gates/base.py``
and ``gates/risk.py``).

Deliberately NOT auto-expiring: per the project's own controlled-improvement
rule ("hard risk controls CANNOT be changed by AI... all changes require user
approval"), a drawdown breach is a stop-and-look-at-it event. Only an explicit
``unlock()`` call (an operator/owner action) clears it. This is stdlib-only,
dependency-free logic so it can be tested and reasoned about in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

__all__ = ["DrawdownLockout"]


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DrawdownLockout:
    """Tracks equity peak and locks trading on a max-drawdown breach.

    Usage:
        lockout = DrawdownLockout(max_drawdown=0.10)
        ...
        locked = lockout.update(equity)
    """

    max_drawdown: float
    _peak: Optional[float] = field(default=None, init=False, repr=False)
    _locked: bool = field(default=False, init=False, repr=False)
    _locked_reason: Optional[str] = field(default=None, init=False, repr=False)
    _locked_at: Optional[datetime] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not (0.0 < self.max_drawdown <= 1.0):
            raise ValueError(
                f"max_drawdown must be in (0, 1], got {self.max_drawdown}"
            )

    def update(
        self,
        equity: float,
        *,
        now: Optional[Callable[[], datetime]] = None,
    ) -> bool:
        """Observe the latest equity value. Returns True if currently locked.

        Once locked, further calls do NOT unlock automatically even if equity
        recovers above the drawdown threshold — see ``unlock()``.
        """
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
                f"{self.max_drawdown:.2%} "
                f"(peak={self._peak:.2f}, equity={equity:.2f})"
            )
            self._locked_at = (now or _default_now)()

        return self._locked

    def unlock(self, *, reset_peak_to: Optional[float] = None) -> None:
        """Explicit operator action to resume trading after review.

        Optionally reset the tracked peak (e.g. to current equity) so the
        next drawdown calculation starts fresh rather than measuring against
        the pre-breach peak.
        """
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
    def locked_at(self) -> Optional[datetime]:
        return self._locked_at

    @property
    def peak(self) -> Optional[float]:
        return self._peak