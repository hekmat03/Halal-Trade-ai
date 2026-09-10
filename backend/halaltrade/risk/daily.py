"""Daily P&L reset — resets the realized-P&L bucket at UTC day boundary."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Optional

__all__ = ["DailyPnLTracker"]


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DailyPnLTracker:
    _day: Optional[date] = field(default=None, init=False, repr=False)
    _baseline: float = field(default=0.0, init=False, repr=False)
    _today_pnl: float = field(default=0.0, init=False, repr=False)

    def update(
        self,
        cumulative_realized_pnl: float,
        *,
        now: Optional[Callable[[], datetime]] = None,
    ) -> float:
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

    def force_reset(self, cumulative_realized_pnl: float, *, day: Optional[date] = None) -> None:
        self._day = day or date.today()
        self._baseline = cumulative_realized_pnl
        self._today_pnl = 0.0