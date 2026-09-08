"""SECURITY GATE — credentials, secret handling and system health.

Rules:
* Secrets (Binance API key/secret) must come from the environment and be present
  whenever live trading requires them. Anything hardcoded is unacceptable by
  design; this gate only ever reads ``Settings``.
* API keys must be withdrawal-restricted. If withdrawals are allowed → reject
  AND STOP.
* System health / credential state must be valid. If invalid → reject AND STOP.

A stopped gate halts the entire pipeline: even a single otherwise-valid signal
must not trade while the system is in an insecure state.
"""
from __future__ import annotations

from ..models import SecurityResult, Signal
from .base import Context, Gate

__all__ = ["SecurityGate"]


class SecurityGate(Gate):
    name: str = "security"

    def evaluate(self, signal: Signal, context: Context) -> SecurityResult:
        settings = context.settings
        reasons: list[str] = []
        failed: list[str] = []
        stopped = False

        # --- 1. Withdrawals must never be permitted ---
        if settings.api_key_restricted is not True:
            failed.append(
                "REJECT+STOP: API key is NOT withdrawal-restricted — withdrawal "
                "capability is forbidden by security policy."
            )
            stopped = True
        else:
            reasons.append("OK: API key is restricted (no withdrawals).")

        # --- 2. System / credential health ---
        if settings.system_healthy is not True:
            failed.append(
                "REJECT+STOP: system/credential health state is invalid."
            )
            stopped = True
        else:
            reasons.append("OK: system health state is valid.")

        # --- 3. Secret handling (environment only) ---
        if settings.requires_binance_credentials():
            if not settings.secrets_present():
                failed.append(
                    "REJECT+STOP: live trading requires Binance API credentials but "
                    "they are missing from the environment."
                )
                stopped = True
            else:
                reasons.append("OK: Binance credentials present from environment.")
        else:
            reasons.append(
                "OK: live credentials not required for current "
                f"mode={settings.trading_mode!r}."
            )

        # Credentials must never be introduced by code directly; the Settings object
        # is the only allowed source and it reads the environment. We assert here that
        # this gate never receives secrets as a parameter — there is no route for them.

        reasons.extend(failed)
        return SecurityResult(
            gate_name=self.name,
            passed=not failed,
            reasons=reasons,
            stopped=stopped,
        )
