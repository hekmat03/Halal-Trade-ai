"""Order manager — idempotent lifecycle over a simulated (paper) fill source.

Lifecycle (Binance-shaped):

    NEW -> PARTIALLY_FILLED -> FILLED
    NEW -> CANCELED | EXPIRED | REJECTED
    PARTIALLY_FILLED -> CANCELED   (remainder cancelled after partial fills)

Rules:
* ``submit()`` assigns a fresh UUID4 ``clientOrderId`` when the caller does not
  supply one; a supplied id that was already seen returns the ORIGINAL recorded
  result (idempotent retry — never a duplicate order, never a second fill).
* Limit orders support TIF (``GTC``/``IOC``/``FOK``) and ``POST_ONLY``; a
  POST_ONLY limit that would cross immediately is REJECTED (maker-only), an
  IOC/FOK limit that cannot fill (fully) immediately is CANCELED/EXPIRED.
* ``cancel(client_order_id)`` is idempotent: cancelling a terminal order returns
  its current view; cancelling an unknown id is a rejection, never invented.
* ``apply_fill(client_order_id, qty, price)`` accumulates ``executed_qty`` /
  ``cummulative_quote_qty``; the order is complete only when executed reaches
  the ordered quantity (within tolerance) -> FILLED.
* Status is NEVER assumed: transitions only happen inside ``apply_fill``,
  ``cancel``, ``expire`` or an explicit rejection — the caller confirms.
* Every event is recorded to the DB ``orders`` table when a session is given.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Literal, Optional

from ..config import Settings
from ..models import utcnow
from .validation import OrderValidationError, validate_order

logger = logging.getLogger(__name__)

__all__ = [
    "OrderStatus",
    "OrderState",
    "OrderRequest",
    "OrderView",
    "OrderManager",
    "new_client_order_id",
]

_TOL = 1e-12


def new_client_order_id() -> str:
    """A fresh unique idempotency key (UUID4 hex). Never reused on retry."""
    return f"ht-{uuid.uuid4().hex}"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


TERMINAL = frozenset({
    OrderStatus.FILLED, OrderStatus.CANCELED,
    OrderStatus.EXPIRED, OrderStatus.REJECTED,
})


@dataclass
class OrderRequest:
    """What the caller wants to place. ``client_order_id`` is optional."""

    side: Literal["BUY", "SELL"]
    symbol: str = "BTCUSDT"
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    quantity: Optional[float] = None
    amount: Optional[float] = None
    price: Optional[float] = None          # reference price (market) / limit price
    time_in_force: Literal["GTC", "IOC", "FOK"] = "GTC"
    post_only: bool = False
    signal_id: str = "n/a"
    client_order_id: Optional[str] = None


@dataclass
class OrderState:
    """Mutable lifecycle state of one tracked order (in-memory source of truth)."""

    client_order_id: str
    side: str
    symbol: str
    order_type: str
    quantity: float
    price: Optional[float]
    notional: float
    time_in_force: str
    post_only: bool
    signal_id: str
    status: OrderStatus = OrderStatus.NEW
    executed_qty: float = 0.0
    cummulative_quote_qty: float = 0.0
    created_at: Optional[datetime] = None

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.quantity - self.executed_qty)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    @property
    def is_complete(self) -> bool:
        """An order is complete only at FILLED (full execution confirmed)."""
        return self.status == OrderStatus.FILLED


@dataclass(frozen=True)
class OrderView:
    """Immutable snapshot returned to callers (Binance-shaped fields)."""

    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: Optional[float]
    status: str
    executed_qty: float
    cummulative_quote_qty: float
    time_in_force: str
    post_only: bool
    is_complete: bool
    reason: str = ""

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.quantity - self.executed_qty)


class OrderManager:
    """Tracks order lifecycle with idempotent submit / cancel / fill / expire."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session=None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.session = session
        self._now = now or utcnow
        self._orders: dict[str, OrderState] = {}
        # Idempotency: clientOrderId -> the ORIGINAL result view of first submission.
        self._results: dict[str, OrderView] = {}

    # -- introspection ------------------------------------------------------
    def get(self, client_order_id: str) -> Optional[OrderView]:
        state = self._orders.get(client_order_id)
        return self._view(state, state.status.value if state else "") if state else None

    def open_orders(self) -> list[OrderView]:
        return [self._view(s, "") for s in self._orders.values() if not s.is_terminal]

    # -- submission ---------------------------------------------------------
    def submit(
        self, request: OrderRequest, *, market_price: Optional[float] = None,
    ) -> OrderView:
        """Place an order (paper). Idempotent on ``client_order_id``.

        ``market_price`` is an optional reference used ONLY for the POST_ONLY
        cross check (a POST_ONLY limit that would take immediately is
        REJECTED as maker-only). Without a reference the order rests — a cross
        is never assumed.
        """
        coid = request.client_order_id or new_client_order_id()
        if coid in self._results:
            # Idempotent retry: return the ORIGINAL result, never a duplicate.
            logger.info("idempotent retry for %s — returning original result", coid)
            return self._results[coid]

        try:
            valid = validate_order(
                side=request.side,
                order_type=request.order_type,
                quantity=request.quantity,
                amount=request.amount,
                price=request.price,
                settings=self.settings,
            )
        except OrderValidationError as exc:
            view = OrderView(
                client_order_id=coid, symbol=request.symbol,
                side=str(request.side).upper(), order_type=str(request.order_type).upper(),
                quantity=request.quantity or 0.0, price=request.price,
                status=OrderStatus.REJECTED.value,
                executed_qty=0.0, cummulative_quote_qty=0.0,
                time_in_force=request.time_in_force, post_only=request.post_only,
                is_complete=False, reason=str(exc),
            )
            self._results[coid] = view
            self._record(request, view)
            return view

        tif = request.time_in_force.upper()
        if tif not in ("GTC", "IOC", "FOK"):
            view = self._reject(
                request, coid, valid,
                f"REJECT: unsupported TIF {request.time_in_force!r} — GTC/IOC/FOK only.",
            )
            return view

        state = OrderState(
            client_order_id=coid, side=valid.side, symbol=request.symbol,
            order_type=valid.order_type, quantity=valid.quantity,
            price=valid.price, notional=valid.notional,
            time_in_force=tif, post_only=request.post_only,
            signal_id=request.signal_id, status=OrderStatus.NEW,
            created_at=self._now(),
        )
        self._orders[coid] = state
        view = self._view(state, "ACCEPTED: order NEW (confirmed submission).")
        self._results[coid] = view
        self._record(request, view)

        # POST_ONLY limit that would cross the market immediately is REJECTED
        # (maker-only): BUY limit >= ref or SELL limit <= ref would take.
        if (
            state.order_type == "LIMIT"
            and state.post_only
            and market_price is not None
            and state.price is not None
        ):
            crosses = (
                (state.side == "BUY" and state.price >= market_price)
                or (state.side == "SELL" and state.price <= market_price)
            )
            if crosses:
                state.status = OrderStatus.REJECTED
                view = self._view(
                    state,
                    "REJECT: POST_ONLY limit would cross immediately (taker) — "
                    "maker-only violated.",
                )
                self._results[coid] = view
                self._record(request, view)
        return view

    # -- fills --------------------------------------------------------------
    def apply_fill(
        self, client_order_id: str, quantity: float, price: float,
        *, reason: str = "", is_final: bool = False,
    ) -> OrderView:
        """Confirm an (often partial) fill. Accumulates until FILLED.

        ``is_final`` marks that the fill SOURCE confirms no further fills are
        coming (e.g. an immediate market fill, or a Binance execution report
        with status FILLED) — the order then closes as FILLED with the
        confirmed executed totals. It must only be set from a confirmed fill
        report, never speculatively: the manager never self-transitions.
        """
        state = self._orders.get(client_order_id)
        if state is None:
            raise KeyError(f"unknown clientOrderId {client_order_id!r} — never invent status.")
        if state.is_terminal:
            # Terminal orders never change: return the recorded view as-is.
            return self._results[client_order_id]
        if quantity <= 0 or price <= 0:
            raise ValueError("fill quantity and price must be positive.")
        fill_qty = min(quantity, state.remaining_qty)
        state.executed_qty += fill_qty
        state.cummulative_quote_qty += fill_qty * price
        # A market fill can confirm a hair more/less than the nominal ordered
        # quantity (fee/slippage rounding): trust the SOURCE's confirmed totals
        # when it declares the fill final, never invent the difference.
        if is_final:
            state.executed_qty += max(0.0, quantity - fill_qty)
            state.cummulative_quote_qty += max(0.0, quantity - fill_qty) * price
        if state.executed_qty >= state.quantity - max(_TOL, state.quantity * 1e-9):
            state.executed_qty = state.quantity  # snap shut on float dust
            state.status = OrderStatus.FILLED
            msg = reason or "FILLED: full execution confirmed."
        else:
            state.status = OrderStatus.PARTIALLY_FILLED
            msg = reason or (
                f"PARTIALLY_FILLED: {state.executed_qty:.8f}/{state.quantity:.8f} confirmed."
            )
        view = self._view(state, msg)
        self._results[client_order_id] = view
        self._record_state(state, msg)
        return view

    # -- cancel / expire ----------------------------------------------------
    def cancel(self, client_order_id: str) -> OrderView:
        """Cancel by clientOrderId. Idempotent: terminal orders return as-is."""
        state = self._orders.get(client_order_id)
        if state is None:
            raise KeyError(f"unknown clientOrderId {client_order_id!r} — cannot cancel.")
        if state.is_terminal:
            return self._results[client_order_id]
        state.status = OrderStatus.CANCELED
        view = self._view(state, "CANCELED: confirmed by cancel request.")
        self._results[client_order_id] = view
        self._record_state(state, view.reason)
        return view

    def expire(self, client_order_id: str, *, reason: str = "EXPIRED") -> OrderView:
        """Mark a resting order expired (GTC expiry / IOC/FOK unfilled window)."""
        state = self._orders.get(client_order_id)
        if state is None:
            raise KeyError(f"unknown clientOrderId {client_order_id!r} — cannot expire.")
        if state.is_terminal:
            return self._results[client_order_id]
        state.status = OrderStatus.EXPIRED
        view = self._view(state, f"EXPIRED: {reason}.")
        self._results[client_order_id] = view
        self._record_state(state, view.reason)
        return view

    # -- internals ----------------------------------------------------------
    def _reject(self, request: OrderRequest, coid: str, valid, reason: str) -> OrderView:
        view = OrderView(
            client_order_id=coid, symbol=request.symbol,
            side=valid.side, order_type=valid.order_type,
            quantity=valid.quantity, price=valid.price,
            status=OrderStatus.REJECTED.value,
            executed_qty=0.0, cummulative_quote_qty=0.0,
            time_in_force=request.time_in_force, post_only=request.post_only,
            is_complete=False, reason=reason,
        )
        self._results[coid] = view
        self._record(request, view)
        return view

    def _view(self, state: OrderState, reason: str) -> OrderView:
        prev_reason = ""
        if state.client_order_id in self._results and not reason:
            prev_reason = self._results[state.client_order_id].reason
        return OrderView(
            client_order_id=state.client_order_id, symbol=state.symbol,
            side=state.side, order_type=state.order_type,
            quantity=state.quantity, price=state.price,
            status=state.status.value,
            executed_qty=state.executed_qty,
            cummulative_quote_qty=state.cummulative_quote_qty,
            time_in_force=state.time_in_force, post_only=state.post_only,
            is_complete=state.is_complete, reason=reason or prev_reason,
        )

    def _record(self, request: OrderRequest, view: OrderView) -> None:
        if self.session is None:
            return
        from ..db.recorder import record_order_event
        try:
            record_order_event(
                self.session, signal_id=request.signal_id,
                client_order_id=view.client_order_id, symbol=view.symbol,
                side=view.side, order_type=view.order_type,
                quantity=view.quantity, price=view.price,
                status="CONFIRMED", lifecycle=view.status,
                executed_qty=view.executed_qty,
                cummulative_quote_qty=view.cummulative_quote_qty,
                time_in_force=view.time_in_force, post_only=view.post_only,
                limit_price=view.price if view.order_type == "LIMIT" else None,
            )
        except Exception:  # audit must never break order flow
            logger.exception("order audit record failed for %s", view.client_order_id)

    def _record_state(self, state: OrderState, reason: str) -> None:
        if self.session is None:
            return
        from ..db.recorder import record_order_event
        try:
            record_order_event(
                self.session, signal_id=state.signal_id,
                client_order_id=state.client_order_id, symbol=state.symbol,
                side=state.side, order_type=state.order_type,
                quantity=state.quantity, price=state.price,
                status="CONFIRMED", lifecycle=state.status.value,
                executed_qty=state.executed_qty,
                cummulative_quote_qty=state.cummulative_quote_qty,
                time_in_force=state.time_in_force, post_only=state.post_only,
                limit_price=state.price if state.order_type == "LIMIT" else None,
            )
        except Exception:  # audit must never break order flow
            logger.exception("order audit record failed for %s", state.client_order_id)
