from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class DrawdownLockout:
    max_drawdown: float
    _peak: float | None = field(default=None, init=False)
    _locked: bool = field(default=False, init=False)

    def update(self, equity: float) -> bool:
        if equity <= 0:
            return self._locked
        if self._peak is None or equity > self._peak:
            self._peak = equity
        if not self._locked and (self._peak - equity) / self._peak > self.max_drawdown:
            self._locked = True
        return self._locked

    def unlock(self):
        self._locked = False