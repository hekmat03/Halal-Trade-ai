"""Core pydantic v2 models shared across the HalalTrade engine.

Models here are pure data (no gate logic). Every gate returns one of the
structured ``*Result`` models, and the pipeline returns a ``PipelineResult``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

__all__ = [
    "Side",
    "InstrumentType",
    "Signal",
    "GateResult",
    "ShariahResult",
    "RiskResult",
    "SecurityResult",
    "ExecutionResult",
    "PipelineDecision",
    "PipelineResult",
    "utcnow",
]


def utcnow() -> datetime:
    """Timezone-aware UTC now, used as the deterministic clock for age checks."""
    return datetime.now(timezone.utc)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class InstrumentType(str, Enum):
    """Explicit list of instrument kinds an order may declare.

    Only SPOT is allowed by the Shariah gate; everything else is hard-rejected.
    The exhaustive list makes the "anything not ordinary Spot is rejected" rule
    mechanical rather than a keyword guess.
    """

    SPOT = "SPOT"
    FUTURES = "FUTURES"
    PERPETUAL = "PERPETUAL"
    LEVERAGED_TOKEN = "LEVERAGED_TOKEN"
    MARGIN = "MARGIN"
    OPTIONS = "OPTIONS"
    DERIVATIVE = "DERIVATIVE"
    STAKING = "STAKING"
    LENDING = "LENDING"
    BORROWED = "BORROWED"


class Signal(BaseModel):
    """A proposed order/decision coming from any (possibly AI) signal source.

    A signal is a *recommendation*. It only becomes a trade after it passes every
    gate in the pipeline. Size must be explicit — if the user/strategy did not
    provide an exact size, the Risk gate rejects (no trade).
    """

    side: Side
    symbol: str = "BTCUSDT"
    instrument_type: InstrumentType = InstrumentType.SPOT
    leverage: float = Field(1.0, description="Any value != 1.0 is rejected by Shariah.")
    price: Optional[float] = None
    quantity: Optional[float] = Field(
        None, description="Base-asset quantity (e.g. BTC). Mutually usable with amount."
    )
    amount: Optional[float] = Field(
        None, description="Quote notional (e.g. USDT value of the order)."
    )
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason: str = ""
    proposed_entry: Optional[float] = None
    proposed_exit: Optional[float] = None
    stop_loss: Optional[float] = Field(
        None, description="Mandatory stop-loss on every non-HOLD trade."
    )
    risk_reward: Optional[float] = None
    data_timestamp: Optional[datetime] = Field(
        None, description="When the market data backing this signal was observed."
    )
    client_order_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def notional(self) -> Optional[float]:
        """Explicit order value in quote currency, or None if it cannot be computed."""
        if self.amount is not None:
            return self.amount
        if self.quantity is not None and self.price is not None:
            return self.quantity * self.price
        return None


# --------------------------------------------------------------------------------------
# Gate results
# --------------------------------------------------------------------------------------


class GateResult(BaseModel):
    """Structured outcome of a single gate in the pipeline.

    Attributes
    ----------
    gate_name:
        A stable, unique identifier for the gate (e.g. ``"shariah"``).
    passed:
        Whether the gate allowed the signal to continue.
    reasons:
        Human/machine-readable list of checks that passed/failed.
    stopped:
        Set on fatal conditions (e.g. invalid security credentials) — the pipeline
        halts entirely and reports ``NO_TRADE``.
    data:
        Optional structured details a caller may inspect.
    """

    gate_name: str
    passed: bool
    reasons: list[str] = Field(default_factory=list)
    stopped: bool = False
    data: dict[str, Any] = Field(default_factory=dict)


class ShariahResult(GateResult):
    gate_name: str = "shariah"


class RiskResult(GateResult):
    gate_name: str = "risk"


class SecurityResult(GateResult):
    gate_name: str = "security"


class ExecutionResult(GateResult):
    gate_name: str = "execution"


# --------------------------------------------------------------------------------------
# Pipeline result
# --------------------------------------------------------------------------------------


class PipelineDecision(str, Enum):
    TRADE = "TRADE"
    NO_TRADE = "NO_TRADE"


class PipelineResult(BaseModel):
    """Full, structured outcome of running every gate in order.

    Also serves as the audit/debug record — every gate's outcome is retained even
    when the pipeline short-circuits, so a rejection is always explainable.
    """

    decision: PipelineDecision
    signal_id: str = "n/a"
    reasons: list[str] = Field(default_factory=list)
    gates: dict[str, GateResult] = Field(default_factory=dict)
    stopped: bool = False
