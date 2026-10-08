"""Fail-safe notification wiring for the safety progression (Delivery 7).

This module does NOT re-implement notifications: it *uses* the existing
``notifications.telegram.TelegramNotifier`` (fail-safe by design — it returns
``False`` instead of raising) and adds plain-text formatters for the four
progression events the business plan requires alerts for:

* a progression stage transition,
* a pending human-confirmation request,
* a live-enable attempt (success AND refusal, with reasons),
* a kill-switch event.

Every send goes through :meth:`ProgressionNotifier.notify`, which swallows
*any* exception (and any non-``True`` return) and only logs a warning. A
notification can therefore never block, crash or fail a transition, a trade or
a kill-switch action.

Alert-type mapping (the existing ``AlertType`` members are reused rather than
extending the owner's enum):

===============  ==========================
event            AlertType
===============  ==========================
transition       STRATEGY_CHANGE
progression_refused / live_enable_refused  RISK_REJECTION
confirmation_requested   SYSTEM_ERROR
live_enabled     STRATEGY_CHANGE
live_disabled / kill_switch  EMERGENCY_STOP
===============  ==========================
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..config import Settings
from ..notifications.telegram import AlertType, TelegramNotifier

logger = logging.getLogger(__name__)

__all__ = [
    "ALERT_TYPE_BY_EVENT",
    "format_confirmation_request",
    "format_kill_switch_event",
    "format_live_enable_result",
    "format_progression_event",
    "ProgressionNotifier",
]

ALERT_TYPE_BY_EVENT: dict[str, AlertType] = {
    "progression_transition": AlertType.STRATEGY_CHANGE,
    "progression_refused": AlertType.RISK_REJECTION,
    "confirmation_requested": AlertType.SYSTEM_ERROR,
    "live_enable_attempt": AlertType.STRATEGY_CHANGE,
    "live_enable_refused": AlertType.RISK_REJECTION,
    "live_enabled": AlertType.STRATEGY_CHANGE,
    "live_disabled": AlertType.EMERGENCY_STOP,
    "kill_switch": AlertType.EMERGENCY_STOP,
}


def _bullets(title: str, items: list[str], limit: int = 6) -> list[str]:
    lines = [title]
    for item in items[:limit]:
        lines.append(f"  - {item}")
    if len(items) > limit:
        lines.append(f"  ... and {len(items) - limit} more")
    return lines


def format_progression_event(
    *,
    event: str,
    from_stage: str,
    to_stage: str,
    live_enabled: bool,
    passed: Optional[list[str]] = None,
    failed: Optional[list[str]] = None,
    note: str = "",
) -> str:
    """Pure formatter for a progression stage / refusal event."""
    lines = [f"PROGRESSION ({event}): {from_stage} -> {to_stage}"]
    lines.append(f"Live authorized: {'YES' if live_enabled else 'NO (default)'}")
    lines.extend(_bullets("Evidence OK:", list(passed or [])))
    lines.extend(_bullets("Evidence MISSING/FAILED:", list(failed or [])))
    if note:
        lines.append(f"Note: {note}")
    lines.append("Nothing is executed without the user's own size + confirmation.")
    return "\n".join(lines)


def format_confirmation_request(
    *,
    purpose: str,
    target: str,
    required_fields: tuple[str, ...] | list[str],
    token: str,
    expires_at: Any,
) -> str:
    """Pure formatter for a pending human-confirmation request."""
    fields = ", ".join(required_fields) if required_fields else "(re-confirm the values you typed)"
    return (
        f"\U0001F510 HUMAN CONFIRMATION REQUIRED — {purpose} ({target})\n"
        f"Fields you must supply yourself: {fields}\n"
        f"Token: {token}\n"
        f"Expires: {expires_at}\n"
        "No size is ever chosen for you and nothing runs until you confirm."
    )


def format_live_enable_result(
    *,
    stage: str,
    authorized: bool,
    passed: Optional[list[str]] = None,
    failed: Optional[list[str]] = None,
    execution_implemented: bool = False,
) -> str:
    """Pure formatter for a live-enable attempt (success or refusal + reasons)."""
    header = (
        "LIVE ENABLE AUTHORIZED"
        if authorized
        else "LIVE ENABLE REFUSED"
    )
    lines = [f"\U0001F6A6 {header} — stage {stage}"]
    lines.extend(_bullets("Readiness OK:", list(passed or [])))
    lines.extend(_bullets("Refused because:", list(failed or [])))
    lines.append(
        "Live order execution implemented in this build: "
        f"{'YES' if execution_implemented else 'NO (disabled by design)'}"
    )
    return "\n".join(lines)


def format_kill_switch_event(*, enabled: bool, detail: str = "") -> str:
    """Pure formatter for a kill-switch event."""
    state = "ENGAGED — all trading halted" if enabled else "RELEASED — trading may resume"
    lines = [f"\U0001F6A8 KILL SWITCH {state}"]
    if detail:
        lines.append(detail)
    return "\n".join(lines)


class ProgressionNotifier:
    """Thin, fail-safe wrapper around the existing ``TelegramNotifier``.

    ``notify`` can never raise: a missing token, a network error, a disabled
    alert type or any other failure resolves to a logged warning plus ``False``.
    """

    def __init__(
        self,
        notifier: Any | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._notifier = notifier if notifier is not None else TelegramNotifier(settings)
        self.delivered = 0
        self.failures = 0

    async def notify(self, event: str, text: str) -> bool:
        """Best-effort send. Returns True only on confirmed delivery."""
        alert_type = ALERT_TYPE_BY_EVENT.get(event, AlertType.SYSTEM_ERROR)
        try:
            delivered = await self._notifier.send(alert_type, text)
        except Exception as exc:  # noqa: BLE001 - notification must never break the flow
            self.failures += 1
            logger.warning("progression notification %s failed (ignored): %s", event, exc)
            return False
        if delivered:
            self.delivered += 1
        else:
            self.failures += 1
            logger.info("progression notification %s not delivered (notifier returned False)", event)
        return bool(delivered)

    async def send_text(self, alert_type: AlertType, text: str) -> bool:
        """Send an arbitrary alert through the same fail-safe wrapper."""
        try:
            return bool(await self._notifier.send(alert_type, text))
        except Exception as exc:  # noqa: BLE001
            self.failures += 1
            logger.warning("notification send failed (ignored): %s", exc)
            return False
