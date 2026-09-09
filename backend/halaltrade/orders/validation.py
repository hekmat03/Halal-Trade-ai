"""Pre-submission order validation (Binance-shaped, no live calls).

Mirrors the Binance Spot constraints the live backend will enforce:

* ``quantity`` must be positive and a multiple of the LOT_SIZE step
  (``Settings.qty_step``); floating-point dust is rounded to the step and any
  non-multiple is rejected (never silently rounded into a different size).
* Notional (``quantity x price`` for limit orders, explicit ``amount`` for
  market orders) must be >= ``Settings.min_notional`` (MIN_NOTIONAL).
* Side must be BUY or SELL (spot; HOLD is not an order).
* Limit orders need a positive limit price; market orders need a reference price.

Validation NEVER touches the network — it is pure arithmetic against Settings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from ..config import Settings

__all__ = ["OrderValidationError", "ValidatedOrder", "validate_order"]


class OrderValidationError(ValueError):
    """Raised when an order fails pre-submission validation (no order is placed)."""


@dataclass(frozen=True)
class ValidatedOrder:
    """A normalized, submission-ready order (quantities already step-aligned)."""

    side: str
    order_type: str
    quantity: float
    price: Optional[float]
    notional: float


def _is_step_multiple(quantity: float, step: float) -> bool:
    """True when *quantity* is an exact multiple of *step* (within float tolerance)."""
    if step <= 0:
        return True
    steps = round(quantity / step)
    return abs(steps * step - quantity) <= max(1e-12, step * 1e-6)


def validate_order(
    *,
    side: str,
    order_type: Literal["MARKET", "LIMIT"] = "MARKET",
    quantity: Optional[float] = None,
    amount: Optional[float] = None,
    price: Optional[float] = None,
    settings: Settings | None = None,
) -> ValidatedOrder:
    """Validate + normalize an order request. Raises ``OrderValidationError``."""
    settings = settings or Settings()
    side_u = (side or "").upper()
    type_u = (order_type or "MARKET").upper()

    if side_u not in ("BUY", "SELL"):
        raise OrderValidationError(
            f"REJECT: unsupported order side {side!r} — spot orders are BUY or SELL only."
        )
    if type_u not in ("MARKET", "LIMIT"):
        raise OrderValidationError(
            f"REJECT: unsupported order type {order_type!r} — MARKET or LIMIT only."
        )

    if quantity is None:
        if amount is None or price is None or price <= 0:
            raise OrderValidationError(
                "REJECT: need an explicit quantity, or amount + positive price "
                "to derive it (manual size control — never auto-sized)."
            )
        quantity = amount / price

    if quantity <= 0:
        raise OrderValidationError(f"REJECT: non-positive quantity {quantity}.")

    step = settings.qty_step
    if not _is_step_multiple(quantity, step):
        raise OrderValidationError(
            f"REJECT: quantity {quantity:.8f} is not a multiple of step {step} "
            "(LOT_SIZE — submit a step-aligned size)."
        )

    if type_u == "LIMIT":
        if price is None or price <= 0:
            raise OrderValidationError(
                "REJECT: LIMIT orders need a positive limit price."
            )
        notional = quantity * price
    else:
        if price is not None and price <= 0:
            raise OrderValidationError("REJECT: non-positive reference price.")
        notional = quantity * price if price else (amount if amount else 0.0)

    if notional < settings.min_notional:
        raise OrderValidationError(
            f"REJECT: order notional {notional:.6f} USDT below minimum "
            f"{settings.min_notional} USDT (MIN_NOTIONAL)."
        )

    return ValidatedOrder(
        side=side_u, order_type=type_u, quantity=quantity,
        price=price, notional=notional,
    )
