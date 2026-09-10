from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

@dataclass
class DailyPnLTracker:
    _day: date | None = field(default=None, init=False)
    _baseline: float = field(default=0.0, init=False)

    def update(self, cumulative_realized_pnl: float, now=None) -> float:
        current_time = (now or (lambda: datetime.now(timezone.utc)))()
        if self._day is None or current_time.date() != self._day:
            self._day = current_time.date()
            self._baseline = cumulative_realized_pnl
        return cumulative_realized_pnl - self._baseline