"""HalalTrade AI — Shariah-compliant, SPOT-only BTC/USDT trading-agent foundation.

Delivery 1: four non-bypassable policy gates + the fixed-order pipeline, fully
tested, usable as a library (FastAPI/Next.js wiring comes in later deliveries).
"""
from .config import Settings
from .models import (
    ExecutionResult,
    GateResult,
    InstrumentType,
    PipelineDecision,
    PipelineResult,
    RiskResult,
    SecurityResult,
    ShariahResult,
    Side,
    Signal,
)
from .pipeline import Pipeline

__version__ = "0.1.0"

__all__ = [
    "Settings",
    "Signal",
    "Side",
    "InstrumentType",
    "GateResult",
    "ShariahResult",
    "RiskResult",
    "SecurityResult",
    "ExecutionResult",
    "PipelineDecision",
    "PipelineResult",
    "Pipeline",
    "__version__",
]