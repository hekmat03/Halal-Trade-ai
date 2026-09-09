"""Order management package (Delivery 5) — paper-first, Binance-shaped.

``halaltrade.orders`` owns the full order lifecycle for paper trading:

    NEW -> (PARTIALLY_FILLED -> FILLED | CANCELED | EXPIRED | REJECTED)

Execution against Binance remains OUT of scope: the ``OrderManager`` routes
fills through the simulated account, but every request/response shape
(``clientOrderId``, ``executedQty``, ``cummulativeQuoteQty``, TIF, POST_ONLY)
mirrors Binance Spot so a live backend can slot in later without changing the
lifecycle, validation, or audit contracts.

Hard invariants (never bypassed):
* Every order gets a unique ``clientOrderId`` (UUID4, never reused on retry).
* Retry of a submitted order returns the ORIGINAL result, never a duplicate.
* Status is never assumed — it only changes on confirmed state transitions.
* An order is complete only at FILLED; partial fills accumulate.
* Min-notional and quantity step/size validation run BEFORE submission.
* All order events (create/update/cancel/fill) are recorded in the DB.
"""
from __future__ import annotations

from .manager import (
    OrderManager,
    OrderRequest,
    OrderState,
    OrderStatus,
    OrderView,
    new_client_order_id,
)
from .validation import OrderValidationError, validate_order

__all__ = [
    "OrderManager",
    "OrderRequest",
    "OrderState",
    "OrderStatus",
    "OrderValidationError",
    "OrderView",
    "new_client_order_id",
    "validate_order",
]
