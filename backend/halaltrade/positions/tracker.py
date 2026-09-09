"""Position tracker — open-position state from confirmed fills.

The tracker wraps a ``SimAccount`` (the single source of fill/economics truth)
and layers the Delivery 5 position policy on top:

* ``apply_buy_fill`` / ``apply_sell_fill`` update the account AND the position
  record (avg entry, stop, TP, optional trailing state).
* ``on_price(price)`` is the monitor pump the caller drives with each new price
  observation: stop-loss first, then take-profit, then trailing stop. Each
  trigger flattens through the simulated account and returns a close report.
* Dust: when a SELL leaves a residual below ``qty_step`` that cannot be traded,
  ``flatten_dust`` closes it in ONE final fill (or marks it dust-closed when
  even that is untradeable) instead of retrying failed orders.
* ``risk_account(...)`` builds the position-aware ``RiskAccount`` snapshot the
  Risk gate needs (exposure value, open count, holdings, realized-day P&L).

Long-only, 1x spot. A SELL larger than holdings raises (never shorts) — the
same invariant the Risk gate and the simulation enforce.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Literal, Optional

from ..config import Settings
from ..gates.base import RiskAccount
from ..models import utcnow
from ..simulation import SimAccount
from ..simulation.account import Fill, FillError, PositionError, ShortError

logger = logging.getLogger(__name__)

__all__ = ["TrailingConfig", "PositionView", "CloseReport", "PositionTracker"]


@dataclass(frozen=True)
class TrailingConfig:
    """Trailing-stop parameters (long position)."""

    enabled: bool = False
    # Distance below the running peak that triggers the exit (USDT per BTC).
    distance: float = 0.0
    # Optional activation profit: trail only once price >= entry + this.
    activation_profit: float = 0.0


@dataclass(frozen=True)
class PositionView:
    """Immutable snapshot of the tracked position."""

    symbol: str
    quantity: float
    avg_entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    trailing_stop: Optional[float]
    realized_pnl: float
    unrealized_pnl: float
    has_position: bool


@dataclass(frozen=True)
class CloseReport:
    """Outcome of a triggered exit (stop / TP / trailing / dust flatten)."""

    closed: bool
    reason: Literal["STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP", "DUST_FLATTEN", "NONE"]
    quantity: float = 0.0
    price: Optional[float] = None
    realized_pnl: Optional[float] = None
    detail: str = ""


class PositionTracker:
    """Tracks one spot position; monitors stop/TP/trailing on every price tick."""

    def __init__(
        self,
        account: SimAccount,
        settings: Settings | None = None,
        *,
        symbol: str = "BTCUSDT",
        session=None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.account = account
        self.settings = settings or Settings()
        self.symbol = symbol
        self.session = session
        self._now = now or utcnow
        self._trailing = TrailingConfig()
        self._trailing_stop: Optional[float] = None
        self._peak: Optional[float] = None
        # Baseline of account.realized_pnl at tracker creation: today's bucket is
        # (current realized total - baseline), so pre-existing history never
        # leaks into the daily-loss limit.
        self._realized_base: float = account.realized_pnl
        self._realized_today: float = 0.0

    # -- introspection ------------------------------------------------------
    def view(self, price: float) -> PositionView:
        return PositionView(
            symbol=self.symbol,
            quantity=self.account.btc,
            avg_entry_price=self.account.avg_entry_price,
            stop_loss=self.account.stop_loss,
            take_profit=self.account.take_profit,
            trailing_stop=self._trailing_stop,
            realized_pnl=self.account.realized_pnl,
            unrealized_pnl=self.account.unrealized_pnl(price),
            has_position=self.account.has_position,
        )

    def risk_account(self, price: float, *, usdt_balance: Optional[float] = None) -> RiskAccount:
        """Position-aware snapshot for the Risk gate.

        * ``current_position_value`` carries the open exposure (exposure cap).
        * ``open_position_count`` is 1 when holding (position cap).
        * ``base_holdings`` carries owned BTC (never-short SELL check).
        * ``realized_pnl_today`` feeds the daily-loss limit.
        """
        return RiskAccount(
            balance=self.account.usdt if usdt_balance is None else usdt_balance,
            current_position_value=self.account.btc * price if price else 0.0,
            daily_pnl=self._realized_today,
            open_position_count=1 if self.account.has_position else 0,
            base_holdings=self.account.btc,
            realized_pnl_today=self._realized_today,
        )

    # -- fills --------------------------------------------------------------
    def apply_buy_fill(
        self,
        amount_usdt: float,
        price: float,
        *,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing: Optional[TrailingConfig] = None,
        client_order_id: Optional[str] = None,
    ) -> Fill:
        """Record a confirmed BUY fill and (re)arm position protection."""
        if stop_loss is not None:
            self.account.stop_loss = stop_loss
        if take_profit is not None:
            self.account.take_profit = take_profit
        if trailing is not None:
            self._trailing = trailing
            self._trailing_stop = None
            self._peak = None
        fill = self.account.buy(
            amount_usdt, price,
            client_order_id=client_order_id or f"pos-{uuid.uuid4().hex[:8]}",
            timestamp=self._now(),
        )
        self._arm_trailing(price)
        self._record_position("OPEN", realized_pnl=None)
        return fill

    def apply_sell_fill(
        self, quantity: float, price: float, *,
        client_order_id: Optional[str] = None,
    ) -> Fill:
        """Record a confirmed SELL fill. Never shorts (raises ``ShortError``)."""
        fill = self.account.sell(
            quantity, price,
            client_order_id=client_order_id or f"pos-{uuid.uuid4().hex[:8]}",
            timestamp=self._now(),
        )
        # Mirror the account's realized total into today's bucket. (SimAccount
        # books the economics; the tracker owns the per-day attribution.)
        self._realized_today = self.account.realized_pnl - self._realized_base
        if not self.account.has_position:
            self._trailing_stop = None
            self._peak = None
        self._record_position("CLOSED" if not self.account.has_position else "OPEN")
        return fill

    # -- monitor pump -------------------------------------------------------
    def on_price(self, price: float) -> CloseReport:
        """Check stop-loss, take-profit, then trailing stop — in that order.

        Returns a ``CloseReport``; ``closed=False`` when nothing fired. The
        first trigger wins (stop has priority over TP on the same print).
        """
        if not self.account.has_position:
            return CloseReport(closed=False, reason="NONE", detail="no open position.")
        if price <= 0:
            return CloseReport(closed=False, reason="NONE", detail="non-positive price.")

        self._ratchet_trailing(price)

        if self.account.stop_loss is not None and price <= self.account.stop_loss:
            return self._flatten(price, "STOP_LOSS", self.account.stop_loss)
        if self.account.take_profit is not None and price >= self.account.take_profit:
            return self._flatten(price, "TAKE_PROFIT", self.account.take_profit)
        if (
            self._trailing.enabled
            and self._trailing_stop is not None
            and price <= self._trailing_stop
        ):
            return self._flatten(price, "TRAILING_STOP", self._trailing_stop)
        return CloseReport(closed=False, reason="NONE", detail="no trigger hit.")

    # -- dust ---------------------------------------------------------------
    def flatten_dust(self, price: float) -> CloseReport:
        """Close an untradeable residual (< qty_step) in one final fill.

        Instead of emitting repeated orders that fail step validation, the
        residual is sold in a single direct close. Returns a non-closed report
        when there is no position or the residual is a normal tradeable size.
        """
        if not self.account.has_position:
            return CloseReport(closed=False, reason="NONE", detail="no open position.")
        step = self.settings.qty_step
        if self.account.btc >= step:
            return CloseReport(
                closed=False, reason="NONE",
                detail=f"residual {self.account.btc:.8f} >= step {step} — normal close path.",
            )
        return self._flatten(price, "DUST_FLATTEN", price)

    # -- trailing -----------------------------------------------------------
    def configure_trailing(self, config: TrailingConfig, price: float) -> None:
        self._trailing = config
        self._trailing_stop = None
        self._peak = None
        self._arm_trailing(price)

    def _arm_trailing(self, price: float) -> None:
        if not self._trailing.enabled or self._trailing.distance <= 0:
            return
        entry = self.account.avg_entry_price
        if self._trailing.activation_profit > 0 and price < entry + self._trailing.activation_profit:
            self._peak = None
            self._trailing_stop = None
            return
        self._peak = price
        self._trailing_stop = price - self._trailing.distance

    def _ratchet_trailing(self, price: float) -> None:
        if not self._trailing.enabled or self._trailing.distance <= 0:
            return
        if not self.account.has_position:
            return
        entry = self.account.avg_entry_price
        if self._trailing.activation_profit > 0 and price < entry + self._trailing.activation_profit:
            return  # not yet activated
        if self._peak is None or price > self._peak:
            self._peak = price
            self._trailing_stop = price - self._trailing.distance

    # -- internals ----------------------------------------------------------
    def _flatten(self, price: float, reason: str, fill_price: float) -> CloseReport:
        realized_before = self.account.realized_pnl
        try:
            fill = self.account.close_position(
                fill_price, client_order_id=f"exit-{uuid.uuid4().hex[:8]}",
                timestamp=self._now(),
            )
        except (FillError, PositionError, ShortError) as exc:
            return CloseReport(closed=False, reason="NONE", detail=f"flatten refused: {exc}")
        delta = self.account.realized_pnl - realized_before
        self._realized_today = self.account.realized_pnl - self._realized_base
        self._trailing_stop = None
        self._peak = None
        self._record_position("CLOSED", close_reason=reason)
        logger.info("position flattened (%s) qty=%s @ %s", reason, fill.quantity, fill.price)
        return CloseReport(
            closed=True, reason=reason,  # type: ignore[arg-type]
            quantity=fill.quantity, price=fill.price,
            realized_pnl=delta, detail=f"{reason} exit confirmed @ {fill.price}.",
        )

    def _record_position(self, status: str, *, close_reason: str | None = None,
                         realized_pnl=None) -> None:
        if self.session is None:
            return
        from ..db.recorder import record_position
        try:
            record_position(
                self.session, mode="paper", symbol=self.symbol,
                quantity=self.account.btc,
                avg_entry_price=self.account.avg_entry_price,
                stop_loss=self.account.stop_loss,
                take_profit=self.account.take_profit,
                status=status,
                realized_pnl=self.account.realized_pnl,
                close_reason=close_reason,
            )
        except Exception:  # audit must never break position flow
            logger.exception("position audit record failed")
