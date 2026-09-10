"""Daily P&L reset — the missing piece behind ``max_daily_loss``.

Problem this fixes: ``PositionTracker._realized_today`` (in
``positions/tracker.py``) is computed once at construction time and never
rolls over. If the bot process stays up across midnight, "today's" realized
P&L silently keeps accumulating yesterday's numbers forever, and the Risk
Gate's ``max_daily_loss`` cap starts comparing against a bucket that no longer
means "today."

``DailyPnLTracker`` is a small, dependency-free (stdlib only) piece of state
that:
* tracks which UTC calendar day the current "today" bucket belongs to
* detects a day rollover on every call to ``update()``
* resets the running total to 0.0 exactly once when the day changes, using
  the cumulative realized P&L at that instant as the new baseline

It does not talk to the database, the exchange, or pydantic — it is pure
bookkeeping so it can be tested and reasoned about in isolation, then wired
into ``PositionTracker``/``RiskAccount`` by whichever layer owns the clock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Optional

__all__ = ["DailyPnLTracker"]


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DailyPnLTracker:
    """Tracks realized P&L for "today" (UTC calendar day), resetting on rollover.

    Usage:
        tracker = DailyPnLTracker()
        ...
        # each time cumulative realized P&L changes (e.g. after a fill):
        today_pnl = tracker.update(cumulative_realized_pnl, now=some_datetime)
        # `today_pnl` is what should feed RiskAccount.realized_pnl_today

    ``now`` defaults to real UTC time but is injectable for deterministic tests.
    """

    _day: Optional[date] = field(default=None, init=False, repr=False)
    _baseline: float = field(default=0.0, init=False, repr=False)
    _today_pnl: float = field(default=0.0, init=False, repr=False)

    def update(
        self,
        cumulative_realized_pnl: float,
        *,
        now: Optional[Callable[[], datetime]] = None,
    ) -> float:
        """Feed the latest cumulative realized P&L; returns today's bucket.

        On the first call, or whenever the UTC calendar date has advanced
        since the last call, the baseline resets to the current cumulative
        value (today's bucket becomes 0.0 at the moment of rollover).
        """
        current_time = (now or _default_now)()
        current_day = current_time.date()

        if self._day is None or current_day != self._day:
            self._day = current_day
            self._baseline = cumulative_realized_pnl

        self._today_pnl = cumulative_realized_pnl - self._baseline
        return self._today_pnl

    @property
    def today_pnl(self) -> float:
        return self._today_pnl

    @property
    def current_day(self) -> Optional[date]:
        return self._day

    def force_reset(
        self,
        cumulative_realized_pnl: float,
        *,
        day: Optional[date] = None,
    ) -> None:
        """Explicit manual reset (e.g. on process restart or operator action)."""
        self._day = day or date.today()
        self._baseline = cumulative_realized_pnl
        self._today_pnl = 0.0