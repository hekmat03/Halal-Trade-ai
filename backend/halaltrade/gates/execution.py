"""EXECUTION VALIDATION GATE — confirm reality before any order.

Rules (all hard):
* Stale data: if the market data backing the signal is older than
  ``max_data_age_seconds`` (default 5s), reject. Missing timestamp = unknown
  freshness = reject (never trade on un-verifiable data).
* Minimum notional: orders below Binance's BTCUSDT minimum notional are rejected.
* Idempotency: a unique clientOrderId may only be used once; a duplicate is
  rejected (prevents double-submission on retries).
* Order status is NEVER assumed: it must be confirmed from the source of truth.
  If ``context.order_status_confirmed`` is not set, the order is not executed.
"""
from __future__ import annotations

from ..models import ExecutionResult, Signal
from .base import Context, Gate

__all__ = ["ExecutionValidationGate"]


class ExecutionValidationGate(Gate):
    name: str = "execution"

    def evaluate(self, signal: Signal, context: Context) -> ExecutionResult:
        settings = context.settings
        reasons: list[str] = []
        failed: list[str] = []
        now = context.now()

        # --- 1. Stale-data protection ---
        if signal.data_timestamp is None:
            failed.append(
                "REJECT: market data timestamp is missing — freshness is unknown."
            )
        else:
            age = None
            try:
                age = abs((now - signal.data_timestamp).total_seconds())
            except TypeError:  # naive vs aware datetime mismatch
                failed.append("REJECT: malformed data timestamp (tz mismatch).")
            if age is not None:
                if age > settings.max_data_age_seconds:
                    failed.append(
                        f"REJECT: stale data — age {age:.2f}s > "
                        f"max_data_age {settings.max_data_age_seconds}s."
                    )
                else:
                    reasons.append(f"OK: data age {age:.2f}s within limit.")

        # --- 2. Minimum notional ---
        notional = signal.notional()
        if notional is None or notional < settings.min_notional:
            failed.append(
                f"REJECT: order notional {notional} below BTCUSDT minimum "
                f"{settings.min_notional} USDT."
            )
        else:
            reasons.append(f"OK: notional {notional:.6f} >= min {settings.min_notional}.")

        # --- 3. Idempotency (unique clientOrderId) ---
        if signal.client_order_id:
            if signal.client_order_id in context.idempotency_registry:
                failed.append(
                    f"REJECT: duplicate clientOrderId {signal.client_order_id!r} — "
                    "already used (idempotency)."
                )
            else:
                reasons.append(
                    f"OK: clientOrderId {signal.client_order_id!r} is unique."
                )

        # --- 4. Order status must be confirmed from the source ---
        if context.order_status_confirmed is not True:
            failed.append(
                "REJECT: order status has NOT been confirmed from the source of "
                "truth — never assume a fill without confirmation."
            )
        else:
            reasons.append("OK: order status confirmed from source.")

        # Record the idempotency token only once the whole gate passes, so a failed
        # attempt does not poison a legitimate retry.
        if not failed and signal.client_order_id:
            context.idempotency_registry.add(signal.client_order_id)

        reasons.extend(failed)
        return ExecutionResult(gate_name=self.name, passed=not failed, reasons=reasons)
