"""The stage ladder for the live-trading safety progression (Delivery 7).

Design rule (from the business plan): *AI recommends, policies decide, risk
controls, execution acts, logs remember, testing validates — the user keeps
full control of size and final authorization.*

This module holds only data: the ordered stages, the evidence record that a
stage entry is judged against, and the configurable (bounded) limits. It
contains no decision logic — see ``state_machine.py`` for that — so it can be
imported and unit-tested without any DB, settings or network.

NOTHING here enables live trading. ``LIVE_EXECUTION_IMPLEMENTED`` in
``state_machine.py`` is a hard ``False`` in this delivery: the progression can
reach an "authorized" state, but no code path in this repository can place a
real order on Binance mainnet.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

__all__ = [
    "ProgressionStage",
    "STAGE_ORDER",
    "LIVE_CAPABLE_STAGES",
    "stage_index",
    "is_live_capable",
    "coerce_stage",
    "ProgressionLimits",
    "StageEvidence",
]


class ProgressionStage(str, Enum):
    """The five stages, in the only order they may be entered."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    EXTENDED_PAPER_30D = "EXTENDED_PAPER_30D"
    SMALL_LIVE = "SMALL_LIVE"
    SCALE = "SCALE"


#: The one and only legal order. Stages may never be skipped or reversed.
STAGE_ORDER: tuple[ProgressionStage, ...] = (
    ProgressionStage.BACKTEST,
    ProgressionStage.PAPER,
    ProgressionStage.EXTENDED_PAPER_30D,
    ProgressionStage.SMALL_LIVE,
    ProgressionStage.SCALE,
)

#: Stages that talk about real money (even if nothing executes them here).
LIVE_CAPABLE_STAGES: frozenset[ProgressionStage] = frozenset(
    {ProgressionStage.SMALL_LIVE, ProgressionStage.SCALE}
)


def stage_index(stage: ProgressionStage) -> int:
    """Position of ``stage`` in :data:`STAGE_ORDER`."""
    return STAGE_ORDER.index(stage)


def is_live_capable(stage: ProgressionStage) -> bool:
    """Whether ``stage`` is allowed to even *consider* live authorization."""
    return stage in LIVE_CAPABLE_STAGES


def coerce_stage(value: ProgressionStage | str) -> ProgressionStage:
    """Accept an enum or a (case-insensitive) string; refuse anything else.

    Refusing loudly here is deliberate: a typo in a stage name must never be
    silently downgraded to a less risky stage.
    """
    if isinstance(value, ProgressionStage):
        return value
    if isinstance(value, str):
        try:
            return ProgressionStage(value.strip().upper())
        except ValueError as exc:
            valid = ", ".join(s.value for s in STAGE_ORDER)
            raise ValueError(
                f"unknown progression stage {value!r}; valid stages: {valid}"
            ) from exc
    raise ValueError(
        f"progression stage must be a string or ProgressionStage, got {type(value).__name__}"
    )


@dataclass(frozen=True)
class ProgressionLimits:
    """Every numeric bar a stage entry must clear.

    Defaults are deliberately conservative. They are *bounds*, not suggestions:
    the SCALE clip cap is a hard ceiling that no request may exceed.
    """

    # --- into PAPER (a finished, honest backtest) ---
    min_backtest_trades: int = 20
    min_backtest_profit_factor: float = 1.0
    max_backtest_drawdown: float = 0.30
    # --- into EXTENDED_PAPER_30D (>= 30 days of paper, per spec) ---
    min_extended_paper_days: float = 30.0
    min_extended_paper_trades: int = 30
    min_extended_paper_profit_factor: float = 1.0
    max_extended_paper_drawdown: float = 0.20
    # --- into SCALE (real live track record on the small clip) ---
    min_small_live_days: float = 30.0
    min_small_live_trades: int = 20
    min_small_live_profit_factor: float = 1.0
    max_small_live_drawdown: float = 0.20
    # --- hard clip ceilings (USDT notional per order/clip) ---
    small_live_clip_cap_usdt: float = 50.0
    scale_clip_cap_usdt: float = 250.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_backtest_trades": self.min_backtest_trades,
            "min_backtest_profit_factor": self.min_backtest_profit_factor,
            "max_backtest_drawdown": self.max_backtest_drawdown,
            "min_extended_paper_days": self.min_extended_paper_days,
            "min_extended_paper_trades": self.min_extended_paper_trades,
            "min_extended_paper_profit_factor": self.min_extended_paper_profit_factor,
            "max_extended_paper_drawdown": self.max_extended_paper_drawdown,
            "min_small_live_days": self.min_small_live_days,
            "min_small_live_trades": self.min_small_live_trades,
            "min_small_live_profit_factor": self.min_small_live_profit_factor,
            "max_small_live_drawdown": self.max_small_live_drawdown,
            "small_live_clip_cap_usdt": self.small_live_clip_cap_usdt,
            "scale_clip_cap_usdt": self.scale_clip_cap_usdt,
        }


@dataclass(frozen=True)
class StageEvidence:
    """Recorded, user-supplied evidence about work already done.

    Every default is the *failing* value ("nothing recorded yet"), so a fresh
    database can never accidentally look ready for anything. Fields are plain
    JSON-safe scalars so the whole record persists in one DB text column.

    ``live_readiness`` holds optional overrides for the inputs of
    ``validation.live_readiness.LiveReadinessCheck`` (that class is reused
    verbatim, never re-implemented); anything not listed there is derived from
    Settings.
    """

    # backtest stage output
    backtest_run_id: str | None = None
    backtest_trades: int = 0
    backtest_profit_factor: float = 0.0
    backtest_max_drawdown: float = 1.0
    walk_forward_passed: bool = False
    # paper / extended paper track record
    paper_days: float = 0.0
    paper_trades: int = 0
    paper_profit_factor: float = 0.0
    paper_max_drawdown: float = 1.0
    extended_paper_days: float = 0.0
    extended_paper_trades: int = 0
    extended_paper_profit_factor: float = 0.0
    extended_paper_max_drawdown: float = 1.0
    # small-live track record (needed before SCALE)
    small_live_days: float = 0.0
    small_live_trades: int = 0
    small_live_profit_factor: float = 0.0
    small_live_max_drawdown: float = 1.0
    # explicit human acknowledgements (part of LiveReadinessCheck's inputs)
    user_confirmed_risk: bool = False
    user_confirmed_shariah: bool = False
    # optional overrides for LiveReadinessCheck inputs, e.g.
    # {"api_key_restricted": True, "system_healthy": True}
    live_readiness: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backtest_run_id": self.backtest_run_id,
            "backtest_trades": self.backtest_trades,
            "backtest_profit_factor": self.backtest_profit_factor,
            "backtest_max_drawdown": self.backtest_max_drawdown,
            "walk_forward_passed": self.walk_forward_passed,
            "paper_days": self.paper_days,
            "paper_trades": self.paper_trades,
            "paper_profit_factor": self.paper_profit_factor,
            "paper_max_drawdown": self.paper_max_drawdown,
            "extended_paper_days": self.extended_paper_days,
            "extended_paper_trades": self.extended_paper_trades,
            "extended_paper_profit_factor": self.extended_paper_profit_factor,
            "extended_paper_max_drawdown": self.extended_paper_max_drawdown,
            "small_live_days": self.small_live_days,
            "small_live_trades": self.small_live_trades,
            "small_live_profit_factor": self.small_live_profit_factor,
            "small_live_max_drawdown": self.small_live_max_drawdown,
            "user_confirmed_risk": self.user_confirmed_risk,
            "user_confirmed_shariah": self.user_confirmed_shariah,
            "live_readiness": dict(self.live_readiness or {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "StageEvidence":
        """Rebuild from persisted JSON. Unknown/missing keys are ignored so an
        older row can never crash a newer process."""
        data = data or {}
        fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
        clean = {k: v for k, v in data.items() if k in fields}
        if "live_readiness" in clean and not isinstance(clean["live_readiness"], dict):
            clean["live_readiness"] = {}
        return cls(**clean)

    def merged(self, **updates: Any) -> "StageEvidence":
        """Return a copy with ``updates`` applied. Unknown keys are refused."""
        unknown = [k for k in updates if k not in self.__dataclass_fields__]  # type: ignore[attr-defined]
        if unknown:
            raise ValueError(
                "unknown evidence field(s): " + ", ".join(sorted(unknown))
            )
        return replace(self, **updates)

    def effective_paper_days(self) -> float:
        """Longest honestly-recorded paper run (plain paper or the 30-day one)."""
        return max(self.paper_days, self.extended_paper_days)

    def effective_paper_profit_factor(self) -> float:
        return max(self.paper_profit_factor, self.extended_paper_profit_factor)
