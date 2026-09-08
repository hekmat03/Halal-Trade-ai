"""Strategy interface for the backtest engine.

A strategy is a pure callable that turns the current market window and account
state into a :class:`Signal` (a *recommendation*). It never sizes the trade —
the engine supplies the exact per-trade amount (the manual-size rule is
injected by :class:`BacktestEngine`). The signal then must still pass the
four-gate pipeline just like any other signal.

Signature::

    def strategy(candles: list[Candle], position: float, equity: float) -> Signal

* ``candles``  — candles up to and including the current bar (oldest first).
* ``position`` — current open base-asset (BTC) position (0 if flat).
* ``equity``   — current mark-to-market equity in quote (USDT).

Return a ``Signal`` whose ``side`` is BUY / SELL / HOLD. The engine overrides
``amount`` (exact, from config) and stamps ``price`` / ``data_timestamp`` and a
unique ``client_order_id``. ``stop_loss`` and ``proposed_exit`` (take-profit)
are honored when present.
"""
from __future__ import annotations

from typing import Callable, Protocol

from ..marketdata.models import Candle
from ..models import Signal

__all__ = ["Strategy"]


class Strategy(Protocol):
    """A callable that recommends a trade direction given the market window."""

    def __call__(
        self, candles: list[Candle], position: float, equity: float
    ) -> Signal:  # pragma: no cover - interface only
        ...
        raise NotImplementedError
