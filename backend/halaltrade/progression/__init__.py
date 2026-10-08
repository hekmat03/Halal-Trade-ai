"""Live-trading safety progression (Delivery 7).

Public API::

    from halaltrade.progression import (
        ProgressionMachine,        # the state machine (BACKTEST -> ... -> SCALE)
        ProgressionStage,          # the five stages
        ProgressionLimits,         # bounded, configurable bars for each stage
        StageEvidence,             # everything a stage entry is judged on
        DbProgressionStore,        # persistence (kill-switch pattern)
        NullProgressionStore,      # in-memory store for library/tests
        ConfirmationStore,         # single-use human confirmation tokens
        TradeConfirmationGate,     # explicit user size + token per trade
        ProgressionNotifier,       # fail-safe Telegram wiring
        live_execution_implemented,  # always False in this delivery
    )

Live trading is **disabled by default** and this package contains no order
placement path: it only decides whether a human *may* be asked to authorize the
next stage, and records what they answered.
"""
from .confirmation import (
    DEFAULT_CONFIRMATION_TTL_SECONDS,
    ConfirmationPurpose,
    ConfirmationResult,
    ConfirmationStore,
    PendingConfirmation,
    TradeAuthorization,
    TradeConfirmationGate,
)
from .notifications import (
    ProgressionNotifier,
    format_confirmation_request,
    format_kill_switch_event,
    format_live_enable_result,
    format_progression_event,
)
from .state_machine import (
    LIVE_EXECUTION_IMPLEMENTED,
    DbProgressionStore,
    NullProgressionStore,
    ProgressionMachine,
    ProgressionResult,
    ProgressionState,
    ProgressionStore,
    live_execution_implemented,
)
from .stages import (
    LIVE_CAPABLE_STAGES,
    STAGE_ORDER,
    ProgressionLimits,
    ProgressionStage,
    StageEvidence,
    coerce_stage,
    is_live_capable,
    stage_index,
)

__all__ = [
    "ConfirmationPurpose",
    "ConfirmationResult",
    "ConfirmationStore",
    "DEFAULT_CONFIRMATION_TTL_SECONDS",
    "DbProgressionStore",
    "LIVE_CAPABLE_STAGES",
    "LIVE_EXECUTION_IMPLEMENTED",
    "NullProgressionStore",
    "PendingConfirmation",
    "ProgressionLimits",
    "ProgressionMachine",
    "ProgressionNotifier",
    "ProgressionResult",
    "ProgressionStage",
    "ProgressionState",
    "ProgressionStore",
    "STAGE_ORDER",
    "StageEvidence",
    "TradeAuthorization",
    "TradeConfirmationGate",
    "coerce_stage",
    "format_confirmation_request",
    "format_kill_switch_event",
    "format_live_enable_result",
    "format_progression_event",
    "is_live_capable",
    "live_execution_implemented",
    "stage_index",
]
