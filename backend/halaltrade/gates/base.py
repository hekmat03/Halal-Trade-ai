"""Shared gate abstractions.

Every gate is an independent module exposing a small, uniform interface:

    result: GateResult = gate.evaluate(signal, context)

- ``GateResult.gate_name`` is stable and unique per gate.
- Gates never raise for a policy rejection; they return ``passed=False``.
- A gate may set ``stopped=True`` for fatal conditions that should halt the
  whole pipeline (not just reject this one signal).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import Settings
from ..models import GateResult, Signal, utcnow

__all__ = ["Gate", "RiskAccount", "Context", "GateResult", "Signal"]


@dataclass
class RiskAccount:
    """Snapshot of the account state used by the Risk gate.

    This is intentionally simple data — a real broker/adapter supplies it later.
    """

    balance: float = 0.0
    current_position_value: float = 0.0
    daily_pnl: float = 0.0
    equity_peak: Optional[float] = None
    # --- Position-aware fields (Delivery 5: fed from live position state) ---
    # Number of currently open positions (single-asset bot: 0 or 1).
    open_position_count: int = 0
    # Owned base-asset quantity (BTC) — the never-short SELL check consults this.
    base_holdings: float = 0.0
    # Realized P&L for the current day (USDT). When None, the Risk gate falls
    # back to ``daily_pnl`` so older callers behave exactly as before.
    realized_pnl_today: Optional[float] = None

    def equity(self) -> float:
        return self.balance + self.current_position_value


@dataclass
class Context:
    """Everything the gates may need, injected at pipeline build time.

    Holding it here keeps each gate pure, idempotent, and trivially unit-testable.
    """

    settings: Settings = field(default_factory=Settings)
    account: Optional[RiskAccount] = None
    # Registry of clientOrderIds already seen — enforces idempotency.
    idempotency_registry: set[str] = field(default_factory=set)
    # Whether order status has been confirmed from the source of truth. The
    # Execution gate must NEVER assume success.
    order_status_confirmed: bool = False
    # Injectable clock so tests can control "time" deterministically.
    now: Callable[[], object] = field(default_factory=lambda: utcnow)


class Gate(ABC):
    """Base class for a policy gate. Subclass and implement ``evaluate``."""

    name: str = "gate"

    @abstractmethod
    def evaluate(self, signal: Signal, context: Context) -> GateResult:
        """Return a structured result. Never raises for a policy rejection."""
        raise NotImplementedError
