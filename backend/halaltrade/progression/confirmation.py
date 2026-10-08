"""Human confirmation tokens (Delivery 7).

The agent NEVER decides size and NEVER auto-executes. Every stage transition,
every live-enable attempt and every trade (paper or live-capable) must carry a
final, explicit, single-use confirmation token that the *user* supplied, bound
to the exact values the user typed.

Fail-closed rules implemented here (all tested):

* a token must exist, be unused, be unexpired and be for the right purpose;
* every required field must be present **and** supplied by the user — a missing
  ``size`` is a refusal, never a default, never a guess;
* the confirmed values must match the values the token was issued for;
* a token is consumed on use (single use), including when it is consumed by a
  refusal-free successful authorization.

Nothing in this module talks to a network, a DB, or an exchange.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "ConfirmationPurpose",
    "PendingConfirmation",
    "ConfirmationResult",
    "ConfirmationStore",
    "TradeAuthorization",
    "TradeConfirmationGate",
    "DEFAULT_CONFIRMATION_TTL_SECONDS",
    "TRADE_CONFIRMATION_REQUIRED_FIELDS",
]

#: A pending human confirmation is short-lived: the user should confirm what
#: they just read, not a request from an hour ago.
DEFAULT_CONFIRMATION_TTL_SECONDS = 900.0

#: The only field a trade confirmation must carry from the user's own hands.
TRADE_CONFIRMATION_REQUIRED_FIELDS: tuple[str, ...] = ("size",)


class ConfirmationPurpose(str, Enum):
    """What a confirmation token authorizes. Purposes are not interchangeable."""

    TRANSITION = "transition"
    LIVE_ENABLE = "live_enable"
    TRADE = "trade"
    LIVE_DISABLE = "live_disable"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_token_factory() -> str:
    return secrets.token_hex(16)


@dataclass(frozen=True)
class PendingConfirmation:
    """One outstanding request for a human decision."""

    token: str
    purpose: str
    target: str
    required_fields: tuple[str, ...]
    expected: dict[str, Any]
    created_at: datetime
    expires_at: datetime
    used: bool = False

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "purpose": self.purpose,
            "target": self.target,
            "required_fields": list(self.required_fields),
            "expected": dict(self.expected),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "used": self.used,
        }


@dataclass(frozen=True)
class ConfirmationResult:
    """Outcome of validating/consuming a confirmation token."""

    confirmed: bool
    token: str
    purpose: str | None = None
    target: str | None = None
    reason: str = ""
    failed: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        head = f"CONFIRMATION {'ACCEPTED' if self.confirmed else 'REFUSED'} ({self.token[:8]}…)"
        lines = [head]
        for reason in self.failed:
            lines.append(f"  FAIL: {reason}")
        if self.reason:
            lines.append(f"  {self.reason}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmed": self.confirmed,
            "token": self.token,
            "purpose": self.purpose,
            "target": self.target,
            "reason": self.reason,
            "failed": list(self.failed),
            "payload": dict(self.payload),
        }


class ConfirmationStore:
    """Single-owner, in-memory store of pending human confirmations.

    In-memory is deliberate for the MVP: a confirmation that does not survive a
    process restart simply has to be asked again, which fails *closed*. Tokens
    are single-use and are consumed even by a successful validation.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_CONFIRMATION_TTL_SECONDS,
        clock: Optional[Callable[[], datetime]] = None,
        token_factory: Optional[Callable[[], str]] = None,
        max_pending: int = 50,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("confirmation ttl_seconds must be positive")
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock or _utcnow
        self._token_factory = token_factory or _default_token_factory
        self._max_pending = max_pending
        self._pending: dict[str, PendingConfirmation] = {}

    # ------------------------------------------------------------------ helpers
    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now

    def _purge_expired(self) -> None:
        now = self._now()
        for token, pending in list(self._pending.items()):
            if pending.is_expired(now):
                del self._pending[token]

    # ------------------------------------------------------------------ public
    def request(
        self,
        *,
        purpose: ConfirmationPurpose | str,
        target: str,
        required_fields: tuple[str, ...] = (),
        expected: Optional[dict[str, Any]] = None,
    ) -> PendingConfirmation:
        """Issue a fresh single-use token for a human decision."""
        self._purge_expired()
        if len(self._pending) >= self._max_pending:
            # Drop the oldest so a stuck request can never wedge the system.
            oldest = min(self._pending.values(), key=lambda p: p.created_at)
            del self._pending[oldest.token]
        purpose_value = purpose.value if isinstance(purpose, ConfirmationPurpose) else str(purpose)
        now = self._now()
        pending = PendingConfirmation(
            token=self._token_factory(),
            purpose=purpose_value,
            target=target,
            required_fields=tuple(required_fields or ()),
            expected=dict(expected or {}),
            created_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        self._pending[pending.token] = pending
        return pending

    def peek(self, token: str) -> PendingConfirmation | None:
        """Look at a pending confirmation without consuming it."""
        if not token:
            return None
        return self._pending.get(token)

    def pending(self) -> list[PendingConfirmation]:
        self._purge_expired()
        return list(self._pending.values())

    def pending_summaries(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.pending()]

    def cancel(self, token: str) -> bool:
        return self._pending.pop(token, None) is not None

    def confirm(
        self,
        token: str,
        *,
        payload: Optional[dict[str, Any]] = None,
        purpose: ConfirmationPurpose | str | None = None,
    ) -> ConfirmationResult:
        """Validate and consume ``token`` against a user-supplied ``payload``.

        Never fills in, defaults or coerces a missing value: a blank/absent
        required field is a refusal and the token is left for the user to look
        at (it stays unconsumed, so it can still be used correctly).
        """
        payload = dict(payload or {})
        purpose_value = (
            purpose.value if isinstance(purpose, ConfirmationPurpose) else purpose
        )
        if not token:
            return ConfirmationResult(
                confirmed=False,
                token="",
                purpose=purpose_value,
                failed=["no confirmation token supplied — a human confirmation is required"],
                reason="CONFIRMATION REQUIRED",
            )
        pending = self._pending.get(token)
        if pending is None:
            return ConfirmationResult(
                confirmed=False,
                token=token,
                purpose=purpose_value,
                failed=["confirmation token not recognised (unknown, expired or already used)"],
                reason="CONFIRMATION REQUIRED",
            )
        failed: list[str] = []
        if purpose_value is not None and pending.purpose != purpose_value:
            failed.append(
                f"confirmation token is for '{pending.purpose}', not '{purpose_value}'"
            )
        if pending.used:
            failed.append("confirmation token has already been used")
        now = self._now()
        if pending.is_expired(now):
            failed.append(
                f"confirmation token expired at {pending.expires_at.isoformat()}"
            )
        for name in pending.required_fields:
            value = payload.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                failed.append(
                    f"required field '{name}' missing — the agent never supplies it for you"
                )
        for name, wanted in pending.expected.items():
            if name in payload and payload[name] != wanted:
                failed.append(
                    f"confirmed '{name}'={payload[name]!r} does not match the "
                    f"authorized value {wanted!r}"
                )
        if failed:
            return ConfirmationResult(
                confirmed=False,
                token=token,
                purpose=pending.purpose,
                target=pending.target,
                failed=failed,
                reason="CONFIRMATION REFUSED",
                payload=payload,
            )
        consumed = PendingConfirmation(
            token=pending.token,
            purpose=pending.purpose,
            target=pending.target,
            required_fields=pending.required_fields,
            expected=pending.expected,
            created_at=pending.created_at,
            expires_at=pending.expires_at,
            used=True,
        )
        self._pending[token] = consumed
        del self._pending[token]  # single use: it is gone once confirmed
        return ConfirmationResult(
            confirmed=True,
            token=token,
            purpose=pending.purpose,
            target=pending.target,
            reason="CONFIRMATION ACCEPTED",
            payload=payload,
        )


@dataclass(frozen=True)
class TradeAuthorization:
    """Result of asking "may this exact trade run?"."""

    authorized: bool
    status: str
    token: str
    client_order_id: str
    size: float | None
    failed: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "authorized": self.authorized,
            "status": self.status,
            "token": self.token,
            "client_order_id": self.client_order_id,
            "size": self.size,
            "failed": list(self.failed),
            "passed": list(self.passed),
        }


class TradeConfirmationGate:
    """Enforces "explicit user size + explicit user confirmation" per trade.

    Two separate calls, mirroring the two human actions:

    ``request_authorization(size=...)``  -> the user states the size and a
    single-use token is issued for that exact size and clientOrderId;
    ``authorize(size=..., token=...)``   -> the order may proceed only if the
    token is valid and the size still matches what was authorized.

    The gate never invents a size. A missing/non-positive size is a refusal.
    """

    def __init__(self, store: ConfirmationStore) -> None:
        self.store = store

    @staticmethod
    def _coerce_size(size: Any) -> float | None:
        if size is None or isinstance(size, bool):
            return None
        try:
            value = float(size)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return value

    def request_authorization(
        self,
        *,
        client_order_id: str,
        side: str,
        size: Any,
        symbol: str = "BTCUSDT",
    ) -> TradeAuthorization:
        """Issue a single-use confirmation token for one exact trade."""
        failed: list[str] = []
        value = self._coerce_size(size)
        if not client_order_id:
            failed.append(
                "clientOrderId is required so the confirmation is bound to one exact order"
            )
        if value is None:
            failed.append(
                "user-supplied size is missing or not a positive number — "
                "the agent never chooses or defaults the size"
            )
        if failed:
            return TradeAuthorization(
                authorized=False,
                status="REFUSED",
                token="",
                client_order_id=client_order_id,
                size=value,
                failed=failed,
            )
        pending = self.store.request(
            purpose=ConfirmationPurpose.TRADE,
            target=client_order_id,
            required_fields=TRADE_CONFIRMATION_REQUIRED_FIELDS,
            expected={"client_order_id": client_order_id, "size": value},
        )
        return TradeAuthorization(
            authorized=False,
            status="AWAITING_CONFIRMATION",
            token=pending.token,
            client_order_id=client_order_id,
            size=value,
            passed=[
                f"size {value} supplied by the user",
                f"confirmation token issued for {side} {symbol} (clientOrderId {client_order_id})",
            ],
        )

    def authorize(
        self,
        *,
        client_order_id: str,
        size: Any,
        token: str,
        side: str = "",
        symbol: str = "BTCUSDT",
    ) -> TradeAuthorization:
        """Validate (and consume) the human confirmation for this exact trade."""
        value = self._coerce_size(size)
        if value is None:
            return TradeAuthorization(
                authorized=False,
                status="REFUSED",
                token=token or "",
                client_order_id=client_order_id,
                size=None,
                failed=[
                    "user-supplied size is missing or not a positive number — "
                    "the agent never chooses or defaults the size"
                ],
            )
        if not client_order_id:
            return TradeAuthorization(
                authorized=False,
                status="REFUSED",
                token=token or "",
                client_order_id="",
                size=value,
                failed=[
                    "clientOrderId is required so the confirmation is bound to one exact order"
                ],
            )
        result = self.store.confirm(
            token,
            payload={"client_order_id": client_order_id, "size": value},
            purpose=ConfirmationPurpose.TRADE,
        )
        if not result.confirmed:
            return TradeAuthorization(
                authorized=False,
                status="REFUSED",
                token=token or "",
                client_order_id=client_order_id,
                size=value,
                failed=list(result.failed),
            )
        return TradeAuthorization(
            authorized=True,
            status="AUTHORIZED",
            token=token,
            client_order_id=client_order_id,
            size=value,
            passed=[
                f"human confirmation accepted for {side or 'trade'} {symbol} "
                f"(clientOrderId {client_order_id})",
                f"size {value} confirmed by the user",
            ],
        )
