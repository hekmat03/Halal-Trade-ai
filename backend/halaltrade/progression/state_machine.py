"""The live-trading safety progression state machine (Delivery 7).

BACKTEST -> PAPER -> EXTENDED_PAPER_30D -> SMALL_LIVE -> SCALE

Hard properties enforced here (and tested in ``tests/test_progression_*.py``):

* a fresh/empty database means ``BACKTEST`` with **live disabled**;
* there is exactly one transition function per stage and each one demands the
  evidence that stage is defined by — stages cannot be skipped, reversed or
  entered without their evidence;
* every transition, and every live-enable attempt, additionally requires a
  single-use human confirmation token (see ``confirmation.py``);
* ``SMALL_LIVE`` is only reachable through
  ``validation.live_readiness.LiveReadinessCheck.evaluate().ready`` (reused
  verbatim, never re-implemented) after >= 30 paper days;
* ``SCALE`` requires an extra, explicit human authorization plus a clip inside
  a hard, bounded ceiling;
* every refusal returns a *structured list of reasons*, in the same
  ``passed``/``failed`` style as ``LiveReadinessResult``;
* the machine can mark live as *authorized*, but this delivery contains **no
  code path that places a real order on Binance mainnet** —
  :data:`LIVE_EXECUTION_IMPLEMENTED` is ``False`` and
  :func:`live_execution_implemented` always returns ``False``.

Notifications are best-effort and can never break a transition.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..config import Settings
from ..validation.live_readiness import LiveReadinessCheck, LiveReadinessResult

from .confirmation import (
    ConfirmationPurpose,
    ConfirmationStore,
    PendingConfirmation,
    TradeConfirmationGate,
)
from .notifications import (
    ProgressionNotifier,
    format_confirmation_request,
    format_kill_switch_event,
    format_live_enable_result,
    format_progression_event,
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

logger = logging.getLogger(__name__)

__all__ = [
    "LIVE_EXECUTION_IMPLEMENTED",
    "DbProgressionStore",
    "NullProgressionStore",
    "ProgressionMachine",
    "ProgressionResult",
    "ProgressionState",
    "ProgressionStore",
    "live_execution_implemented",
]

#: Hard, code-level statement that this delivery does not enable live trading.
#: There is exactly one place that could ever change this, and it is not
#: reachable from the API, the progression machine or any policy file.
LIVE_EXECUTION_IMPLEMENTED = False

#: Event types written to the DB progression log.
STAGE_EVENTS = ("STAGE_ENTERED", "LIVE_ENABLED", "LIVE_DISABLED")


def live_execution_implemented() -> bool:
    """Whether this build can place a real (mainnet) order. Always False."""
    return LIVE_EXECUTION_IMPLEMENTED


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProgressionState:
    """The persisted position on the ladder. Default = BACKTEST, live OFF."""

    stage: ProgressionStage = ProgressionStage.BACKTEST
    live_enabled: bool = False
    clip_usdt: float | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "stage_index": stage_index(self.stage),
            "live_enabled": self.live_enabled,
            "clip_usdt": self.clip_usdt,
            "updated_at": self.updated_at,
            "live_capable": is_live_capable(self.stage),
        }


@dataclass(frozen=True)
class ProgressionResult:
    """Structured outcome of a transition / live-enable request or confirmation.

    ``status`` is one of REFUSED, AWAITING_CONFIRMATION, APPLIED, UNCHANGED.
    ``failed`` is a list of reasons whenever ``status`` is REFUSED.
    """

    status: str
    from_stage: ProgressionStage
    to_stage: ProgressionStage
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    token: str | None = None
    required_fields: tuple[str, ...] = ()
    live_enabled: bool = False
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "APPLIED"

    def summary(self) -> str:
        lines = [f"PROGRESSION {self.status}: {self.from_stage.value} -> {self.to_stage.value}"]
        for reason in self.passed:
            lines.append(f"  OK: {reason}")
        for reason in self.failed:
            lines.append(f"  FAIL: {reason}")
        if self.token:
            lines.append(f"  token: {self.token}")
        if self.note:
            lines.append(f"  {self.note}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "from_stage": self.from_stage.value,
            "to_stage": self.to_stage.value,
            "passed": list(self.passed),
            "failed": list(self.failed),
            "requires_confirmation": self.status == "AWAITING_CONFIRMATION",
            "required_fields": list(self.required_fields),
            "token": self.token,
            "live_enabled": self.live_enabled,
            "live_execution_implemented": LIVE_EXECUTION_IMPLEMENTED,
            "note": self.note,
        }


# --------------------------------------------------------------------------------------
# Persistence (mirrors the kill-switch pattern: latest row wins, empty DB = safe default)
# --------------------------------------------------------------------------------------
class ProgressionStore:
    """Storage port for the progression (load a snapshot, append an event)."""

    def load(self) -> tuple[ProgressionState, StageEvidence]:  # pragma: no cover - port
        raise NotImplementedError

    def append(  # pragma: no cover - port
        self, *, event_type: str, snapshot: dict[str, Any], detail: str = ""
    ) -> None:
        raise NotImplementedError

    def history(self, limit: int = 50) -> list[dict[str, Any]]:  # pragma: no cover - port
        return []


class NullProgressionStore(ProgressionStore):
    """In-memory store (library use, tests, and a DB-less API instance)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def load(self) -> tuple[ProgressionState, StageEvidence]:
        if not self.events:
            return ProgressionState(), StageEvidence()
        last = self.events[-1]
        return (
            ProgressionState(
                stage=coerce_stage(last["stage"]),
                live_enabled=bool(last.get("live_enabled", False)),
                clip_usdt=last.get("clip_usdt"),
                updated_at=last.get("timestamp"),
            ),
            StageEvidence.from_dict(last.get("evidence")),
        )

    def append(self, *, event_type: str, snapshot: dict[str, Any], detail: str = "") -> None:
        record = dict(snapshot)
        record["event_type"] = event_type
        record["timestamp"] = _iso(_utcnow())
        record["detail"] = detail
        self.events.append(record)

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(reversed(self.events[-limit:]))


class DbProgressionStore(ProgressionStore):
    """SQLite/Postgres-backed store following the persisted-kill-switch pattern.

    The latest row is the truth (kill-switch pattern), so progression state and
    evidence survive a restart. An empty table means BACKTEST with live off.
    """

    def __init__(self, session_factory: Callable[[], Any] | None = None) -> None:
        self._session_factory = session_factory

    def _session(self):
        if self._session_factory is None:
            return None
        return self._session_factory()

    def load(self) -> tuple[ProgressionState, StageEvidence]:
        session = None
        try:
            session = self._session()
        except Exception as exc:  # noqa: BLE001 - no DB yet => safe default
            logger.warning("progression store unavailable (%s); defaulting to BACKTEST", exc)
            return ProgressionState(), StageEvidence()
        if session is None:
            return ProgressionState(), StageEvidence()
        try:
            from ..db.models import ProgressionEvent

            row = (
                session.query(ProgressionEvent)
                .order_by(ProgressionEvent.id.desc())
                .first()
            )
            if row is None:
                return ProgressionState(), StageEvidence()
            snapshot: dict[str, Any] = {}
            if row.detail:
                try:
                    parsed = json.loads(row.detail)
                    if isinstance(parsed, dict):
                        snapshot = parsed
                except (TypeError, ValueError):
                    logger.warning("unreadable progression snapshot; using safe default")
                    return ProgressionState(), StageEvidence()
            try:
                stage = coerce_stage(snapshot.get("stage", row.stage))
            except ValueError:
                logger.warning("unknown stage %r persisted; using BACKTEST", snapshot.get("stage"))
                stage = ProgressionStage.BACKTEST
            state = ProgressionState(
                stage=stage,
                live_enabled=bool(snapshot.get("live_enabled", row.live_enabled or False)),
                clip_usdt=snapshot.get("clip_usdt"),
                updated_at=row.timestamp,
            )
            evidence = StageEvidence.from_dict(snapshot.get("evidence"))
            return state, evidence
        except Exception as exc:  # noqa: BLE001 - never crash on a storage problem
            logger.warning("progression load failed (%s); defaulting to BACKTEST", exc)
            return ProgressionState(), StageEvidence()
        finally:
            session.close()

    def append(self, *, event_type: str, snapshot: dict[str, Any], detail: str = "") -> None:
        session = None
        try:
            session = self._session()
        except Exception as exc:  # noqa: BLE001
            logger.warning("progression event not persisted (%s)", exc)
            return
        if session is None:
            return
        try:
            from ..db.recorder import record_progression_event

            record_progression_event(
                session,
                event_type=event_type,
                stage=str(snapshot.get("stage", ProgressionStage.BACKTEST.value)),
                live_enabled=bool(snapshot.get("live_enabled", False)),
                snapshot=snapshot,
                detail=detail,
            )
        except Exception as exc:  # noqa: BLE001 - logging must never break decisions
            logger.warning("progression event not persisted (%s)", exc)
        finally:
            session.close()

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        session = None
        try:
            session = self._session()
            if session is None:
                return []
            from ..db.models import ProgressionEvent

            rows = (
                session.query(ProgressionEvent)
                .order_by(ProgressionEvent.id.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "timestamp": r.timestamp,
                    "event_type": r.event_type,
                    "stage": r.stage,
                    "live_enabled": bool(r.live_enabled),
                    "detail": r.detail,
                }
                for r in rows
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("progression history unavailable (%s)", exc)
            return []
        finally:
            if session is not None:
                session.close()


# --------------------------------------------------------------------------------------
# The machine
# --------------------------------------------------------------------------------------
class ProgressionMachine:
    """Owns the ladder, the evidence record and the human-confirmation flow.

    Mutating methods are ``async`` because they emit best-effort notifications
    before/after persisting; notification failure never changes the outcome.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        store: ProgressionStore | None = None,
        notifier: Any | None = None,
        confirmation_store: ConfirmationStore | None = None,
        limits: ProgressionLimits | None = None,
        clock: Callable[[], datetime] | None = None,
        state: ProgressionState | None = None,
        evidence: StageEvidence | None = None,
    ) -> None:
        self.settings = settings or Settings(trading_mode="paper", live_enabled=False)
        self.store = store or NullProgressionStore()
        self.notifier = (
            notifier
            if notifier is not None
            else ProgressionNotifier(settings=self.settings)
        )
        self.clock = clock or _utcnow
        self.confirmations = confirmation_store or ConfirmationStore(clock=self.clock)
        self.limits = limits or ProgressionLimits()
        self.gate = TradeConfirmationGate(self.confirmations)
        if state is None or evidence is None:
            loaded_state, loaded_evidence = self.store.load()
            state = state or loaded_state
            evidence = evidence or loaded_evidence
        self._state = state
        self._evidence = evidence

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> ProgressionState:
        return self._state

    @property
    def evidence(self) -> StageEvidence:
        return self._evidence

    def current_stage(self) -> ProgressionStage:
        return self._state.stage

    def next_stage(self) -> ProgressionStage | None:
        idx = stage_index(self._state.stage)
        if idx + 1 >= len(STAGE_ORDER):
            return None
        return STAGE_ORDER[idx + 1]

    def live_authorized(self) -> bool:
        """True only when the user explicitly authorized live *and* it is allowed."""
        return bool(self._state.live_enabled and is_live_capable(self._state.stage))

    def confirmation_gate(self) -> TradeConfirmationGate:
        return self.gate

    def _snapshot(self) -> dict[str, Any]:
        return {
            "stage": self._state.stage.value,
            "live_enabled": self._state.live_enabled,
            "clip_usdt": self._state.clip_usdt,
            "evidence": self._evidence.to_dict(),
        }

    def _persist(self, event_type: str, detail: str = "") -> None:
        try:
            self.store.append(event_type=event_type, snapshot=self._snapshot(), detail=detail)
        except Exception as exc:  # noqa: BLE001 - persistence never breaks a decision
            logger.warning("progression persist failed (%s)", exc)

    async def _notify(self, event: str, text: str) -> bool:
        try:
            return await self.notifier.notify(event, text)
        except Exception as exc:  # noqa: BLE001 - belt and braces
            logger.warning("progression notification %s failed (ignored): %s", event, exc)
            return False

    # -------------------------------------------------------------- evidence
    def record_evidence(self, **updates: Any) -> StageEvidence:
        """Record evidence about work already done (backtest/paper results...).

        Unknown keys raise ``ValueError`` — a typo must not be silently dropped.
        """
        self._evidence = self._evidence.merged(**updates)
        self._persist("EVIDENCE_RECORDED", f"evidence updated: {', '.join(sorted(updates))}")
        return self._evidence

    def _readiness(self) -> LiveReadinessResult:
        """Reuse ``LiveReadinessCheck`` verbatim with settings-derived inputs.

        Only the inputs the settings cannot know (paper track record, the two
        human acknowledgements) come from recorded evidence; every other input
        is read from Settings, and evidence may override any of them explicitly.
        """
        ev = self._evidence
        kwargs: dict[str, Any] = {
            "paper_trading_days": ev.effective_paper_days(),
            "paper_profit_factor": ev.effective_paper_profit_factor(),
            "walk_forward_passed": ev.walk_forward_passed,
            "user_enabled_live": bool(getattr(self.settings, "live_enabled", False)),
            "user_confirmed_risk": ev.user_confirmed_risk,
            "user_confirmed_shariah": ev.user_confirmed_shariah,
            "system_healthy": bool(getattr(self.settings, "system_healthy", False)),
            "api_key_restricted": bool(getattr(self.settings, "api_key_restricted", False)),
        }
        for key, value in (ev.live_readiness or {}).items():
            # Unknown keys are passed through on purpose: LiveReadinessCheck then
            # fails loudly (TypeError -> refusal) instead of quietly ignoring input.
            kwargs[key] = value
        try:
            return LiveReadinessCheck(**kwargs).evaluate()
        except TypeError as exc:  # pragma: no cover - defensive
            return LiveReadinessResult(
                ready=False,
                failed=[f"live readiness could not be evaluated: {exc}"],
            )

    def live_readiness(self) -> LiveReadinessResult:
        return self._readiness()

    # ----------------------------------------------------------- stage rules
    def _rule_paper(
        self, ev: StageEvidence, *, clip_usdt: float | None = None, human_authorized_scale: bool | None = None
    ) -> tuple[list[str], list[str]]:
        lim = self.limits
        return self._check(
            [
                (
                    bool(ev.backtest_run_id),
                    f"backtest run recorded ({ev.backtest_run_id})",
                    "no backtest run recorded — run a backtest and record its run_id first",
                ),
                (
                    ev.backtest_trades >= lim.min_backtest_trades,
                    f"backtest trades {ev.backtest_trades} >= required {lim.min_backtest_trades}",
                    f"backtest trades {ev.backtest_trades} < required {lim.min_backtest_trades}",
                ),
                (
                    ev.backtest_profit_factor > lim.min_backtest_profit_factor,
                    f"backtest profit factor {ev.backtest_profit_factor:.2f} > required "
                    f"{lim.min_backtest_profit_factor:.2f}",
                    f"backtest profit factor {ev.backtest_profit_factor:.2f} <= required "
                    f"{lim.min_backtest_profit_factor:.2f}",
                ),
                (
                    ev.backtest_max_drawdown <= lim.max_backtest_drawdown,
                    f"backtest max drawdown {ev.backtest_max_drawdown:.2%} <= allowed "
                    f"{lim.max_backtest_drawdown:.2%}",
                    f"backtest max drawdown {ev.backtest_max_drawdown:.2%} > allowed "
                    f"{lim.max_backtest_drawdown:.2%}",
                ),
                (
                    ev.walk_forward_passed,
                    "walk-forward validation was performed and passed",
                    "walk-forward validation was not performed or did not pass",
                ),
            ]
        )

    def _rule_extended_paper(
        self, ev: StageEvidence, *, clip_usdt: float | None = None, human_authorized_scale: bool | None = None
    ) -> tuple[list[str], list[str]]:
        lim = self.limits
        return self._check(
            [
                (
                    ev.paper_days >= lim.min_extended_paper_days,
                    f"paper trading duration {ev.paper_days:.1f} days >= required "
                    f"{lim.min_extended_paper_days:.1f} days",
                    f"paper trading duration {ev.paper_days:.1f} days < required "
                    f"{lim.min_extended_paper_days:.1f} days",
                ),
                (
                    ev.paper_trades >= lim.min_extended_paper_trades,
                    f"paper trades {ev.paper_trades} >= required {lim.min_extended_paper_trades}",
                    f"paper trades {ev.paper_trades} < required {lim.min_extended_paper_trades}",
                ),
                (
                    ev.paper_profit_factor > lim.min_extended_paper_profit_factor,
                    f"paper profit factor {ev.paper_profit_factor:.2f} > required "
                    f"{lim.min_extended_paper_profit_factor:.2f}",
                    f"paper profit factor {ev.paper_profit_factor:.2f} <= required "
                    f"{lim.min_extended_paper_profit_factor:.2f}",
                ),
                (
                    ev.paper_max_drawdown <= lim.max_extended_paper_drawdown,
                    f"paper max drawdown {ev.paper_max_drawdown:.2%} <= allowed "
                    f"{lim.max_extended_paper_drawdown:.2%}",
                    f"paper max drawdown {ev.paper_max_drawdown:.2%} > allowed "
                    f"{lim.max_extended_paper_drawdown:.2%}",
                ),
            ]
        )

    def _rule_small_live(
        self, ev: StageEvidence, *, clip_usdt: float | None = None, human_authorized_scale: bool | None = None
    ) -> tuple[list[str], list[str]]:
        """SMALL_LIVE: the >=30-day paper record + LiveReadinessCheck.ready."""
        passed, failed = self._rule_extended_paper(ev)
        readiness = self._readiness()
        passed = list(passed) + list(readiness.passed)
        failed = list(failed) + list(readiness.failed)
        if clip_usdt is not None:
            cap = self.limits.small_live_clip_cap_usdt
            ok = 0 < clip_usdt <= cap
            passed.append(
                f"small-live clip {clip_usdt:.2f} USDT inside the hard cap {cap:.2f} USDT"
                if ok
                else f"small-live clip {clip_usdt:.2f} USDT outside (0, {cap:.2f}] USDT — bounded by policy"
            )
        return passed, failed

    def _rule_scale(
        self, ev: StageEvidence, *, clip_usdt: float | None = None, human_authorized_scale: bool | None = None
    ) -> tuple[list[str], list[str]]:
        """SCALE: a real small-live track record + explicit authorization + capped clip."""
        lim = self.limits
        cap = lim.scale_clip_cap_usdt
        clip_ok = clip_usdt is not None and 0 < clip_usdt <= cap
        pairs: list[tuple[bool, str, str]] = [
            (
                clip_ok,
                f"user-authorized clip {clip_usdt} USDT inside the hard cap {cap:.2f} USDT",
                (
                    "no clip supplied for the SCALE stage — the user must state the "
                    f"maximum clip explicitly (hard cap {cap:.2f} USDT)"
                    if clip_usdt is None
                    else f"scale clip {clip_usdt} USDT outside (0, {cap:.2f}] USDT — refused, cap is hard"
                ),
            ),
            (
                ev.small_live_days >= lim.min_small_live_days,
                f"small-live track record {ev.small_live_days:.1f} days >= required "
                f"{lim.min_small_live_days:.1f} days",
                f"small-live track record {ev.small_live_days:.1f} days < required "
                f"{lim.min_small_live_days:.1f} days",
            ),
            (
                ev.small_live_trades >= lim.min_small_live_trades,
                f"small-live trades {ev.small_live_trades} >= required {lim.min_small_live_trades}",
                f"small-live trades {ev.small_live_trades} < required {lim.min_small_live_trades}",
            ),
            (
                ev.small_live_profit_factor > lim.min_small_live_profit_factor,
                f"small-live profit factor {ev.small_live_profit_factor:.2f} > required "
                f"{lim.min_small_live_profit_factor:.2f}",
                f"small-live profit factor {ev.small_live_profit_factor:.2f} <= required "
                f"{lim.min_small_live_profit_factor:.2f}",
            ),
            (
                ev.small_live_max_drawdown <= lim.max_small_live_drawdown,
                f"small-live max drawdown {ev.small_live_max_drawdown:.2%} <= allowed "
                f"{lim.max_small_live_drawdown:.2%}",
                f"small-live max drawdown {ev.small_live_max_drawdown:.2%} > allowed "
                f"{lim.max_small_live_drawdown:.2%}",
            ),
        ]
        if human_authorized_scale is not None:
            pairs.append(
                (
                    human_authorized_scale is True,
                    "the user gave an additional explicit authorization for the SCALE stage",
                    "no additional explicit human authorization for the SCALE stage — this "
                    "stage requires a second, separate confirmation",
                )
            )
        return self._check(pairs)

    @staticmethod
    def _check(pairs: list[tuple[bool, str, str]]) -> tuple[list[str], list[str]]:
        passed: list[str] = []
        failed: list[str] = []
        for ok, ok_msg, fail_msg in pairs:
            (passed if ok else failed).append(ok_msg if ok else fail_msg)
        return passed, failed

    def _rule_for(self, target: ProgressionStage):
        return {
            ProgressionStage.PAPER: self._rule_paper,
            ProgressionStage.EXTENDED_PAPER_30D: self._rule_extended_paper,
            ProgressionStage.SMALL_LIVE: self._rule_small_live,
            ProgressionStage.SCALE: self._rule_scale,
        }.get(target)

    # ------------------------------------------------------- transitions
    async def request_transition(
        self,
        target_stage: ProgressionStage | str,
        *,
        evidence_updates: dict[str, Any] | None = None,
        clip_usdt: float | None = None,
    ) -> ProgressionResult:
        """Ask to enter the next stage. Evidence-checked, then confirmation-gated."""
        current = self._state.stage
        if evidence_updates:
            try:
                self.record_evidence(**evidence_updates)
            except ValueError as exc:
                return ProgressionResult(
                    status="REFUSED",
                    from_stage=current,
                    to_stage=current,
                    failed=[str(exc)],
                    live_enabled=self._state.live_enabled,
                    note="evidence rejected",
                )
        try:
            target = coerce_stage(target_stage)
        except ValueError as exc:
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                failed=[str(exc)],
                live_enabled=self._state.live_enabled,
                note="unknown stage refused",
            )
        if target == current:
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                failed=[f"already at stage {current.value}; nothing to transition"],
                live_enabled=self._state.live_enabled,
            )
        if stage_index(target) != stage_index(current) + 1:
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                failed=[
                    f"stages must be entered one at a time in order "
                    f"{' -> '.join(s.value for s in STAGE_ORDER)}; "
                    f"refused {current.value} -> {target.value}"
                ],
                live_enabled=self._state.live_enabled,
                note="stage skipping/reversing is not allowed",
            )
        rule = self._rule_for(target)
        if rule is None:  # pragma: no cover - BACKTEST is the entry stage
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                failed=[f"stage {target.value} has no transition into it"],
                live_enabled=self._state.live_enabled,
            )
        passed, failed = rule(self._evidence, clip_usdt=clip_usdt)
        if failed:
            self._persist("TRANSITION_REFUSED", f"{current.value} -> {target.value}: {'; '.join(failed)}")
            await self._notify(
                "progression_refused",
                format_progression_event(
                    event="transition refused",
                    from_stage=current.value,
                    to_stage=target.value,
                    live_enabled=self._state.live_enabled,
                    passed=passed,
                    failed=failed,
                ),
            )
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                passed=passed,
                failed=failed,
                live_enabled=self._state.live_enabled,
            )
        pending = self.confirmations.request(
            purpose=ConfirmationPurpose.TRANSITION,
            target=target.value,
            required_fields=("stage",),
            expected={"stage": target.value},
        )
        self._persist("CONFIRMATION_REQUESTED", f"transition to {target.value} awaiting human confirmation")
        await self._notify(
            "confirmation_requested",
            format_confirmation_request(
                purpose=f"enter stage {target.value}",
                target=target.value,
                required_fields=pending.required_fields,
                token=pending.token,
                expires_at=pending.expires_at.isoformat(),
            ),
        )
        return ProgressionResult(
            status="AWAITING_CONFIRMATION",
            from_stage=current,
            to_stage=target,
            passed=passed,
            token=pending.token,
            required_fields=pending.required_fields,
            live_enabled=self._state.live_enabled,
            note=f"confirm with token {pending.token} to enter {target.value}",
        )

    async def confirm_transition(
        self,
        token: str,
        *,
        clip_usdt: float | None = None,
        human_authorized_scale: bool = False,
    ) -> ProgressionResult:
        """Apply a transition the user has confirmed. Evidence is re-checked here."""
        current = self._state.stage
        pending: PendingConfirmation | None = self.confirmations.peek(token)
        if (
            pending is None
            or pending.purpose != ConfirmationPurpose.TRANSITION.value
            or pending.used
        ):
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                failed=[
                    "confirmation token not recognised for a stage transition (unknown, "
                    "expired, already used, or issued for something else)"
                ],
                live_enabled=self._state.live_enabled,
            )
        try:
            target = coerce_stage(pending.target)
        except ValueError:  # pragma: no cover - tokens are issued from stage names
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                failed=[f"confirmation token targets an unknown stage {pending.target!r}"],
                live_enabled=self._state.live_enabled,
            )
        if stage_index(target) != stage_index(current) + 1:
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                failed=[
                    f"stage changed since the token was issued: cannot move "
                    f"{current.value} -> {target.value}"
                ],
                live_enabled=self._state.live_enabled,
            )
        rule = self._rule_for(target)
        effective_clip = clip_usdt if clip_usdt is not None else self._state.clip_usdt
        passed, failed = rule(  # type: ignore[misc]
            self._evidence,
            clip_usdt=effective_clip,
            human_authorized_scale=(human_authorized_scale if target is ProgressionStage.SCALE else None),
        )
        if failed:
            # Do not consume the token: the user can record the missing evidence and retry.
            self._persist("TRANSITION_REFUSED", f"{current.value} -> {target.value}: {'; '.join(failed)}")
            await self._notify(
                "progression_refused",
                format_progression_event(
                    event="transition refused at confirmation",
                    from_stage=current.value,
                    to_stage=target.value,
                    live_enabled=self._state.live_enabled,
                    passed=passed,
                    failed=failed,
                ),
            )
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                passed=passed,
                failed=failed,
                live_enabled=self._state.live_enabled,
                note="token left unused so it can be retried after recording evidence",
            )
        result = self.confirmations.confirm(
            token, payload={"stage": target.value}, purpose=ConfirmationPurpose.TRANSITION
        )
        if not result.confirmed:
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                failed=list(result.failed),
                live_enabled=self._state.live_enabled,
            )
        self._state = replace(
            self._state,
            stage=target,
            clip_usdt=effective_clip,
            updated_at=_iso(self.clock()),
        )
        self._persist("STAGE_ENTERED", f"user-confirmed transition {current.value} -> {target.value}")
        await self._notify(
            "progression_transition",
            format_progression_event(
                event="stage entered",
                from_stage=current.value,
                to_stage=target.value,
                live_enabled=self._state.live_enabled,
                passed=passed,
                note="live order execution is not implemented in this build",
            ),
        )
        return ProgressionResult(
            status="APPLIED",
            from_stage=current,
            to_stage=target,
            passed=passed,
            live_enabled=self._state.live_enabled,
            note=f"now at {target.value}; live is {'AUTHORIZED' if self._state.live_enabled else 'still OFF'}",
        )

    # ------------------------------------------------------- live on/off
    async def request_live_enable(self) -> ProgressionResult:
        """Ask to authorize live. Refuses with reasons unless everything passes."""
        current = self._state.stage
        if not is_live_capable(current):
            failed = [
                f"stage {current.value} is not live-capable; reach "
                f"{ProgressionStage.SMALL_LIVE.value} first "
                f"(live-capable stages: {', '.join(sorted(s.value for s in LIVE_CAPABLE_STAGES))})"
            ]
            self._persist("LIVE_ENABLE_REFUSED", "; ".join(failed))
            await self._notify(
                "live_enable_refused",
                format_live_enable_result(stage=current.value, authorized=False, failed=failed),
            )
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                failed=failed,
                live_enabled=self._state.live_enabled,
            )
        readiness = self._readiness()
        if not readiness.ready:
            self._persist("LIVE_ENABLE_REFUSED", "; ".join(readiness.failed))
            await self._notify(
                "live_enable_refused",
                format_live_enable_result(
                    stage=current.value,
                    authorized=False,
                    passed=readiness.passed,
                    failed=readiness.failed,
                ),
            )
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                passed=list(readiness.passed),
                failed=list(readiness.failed),
                live_enabled=self._state.live_enabled,
                note="LiveReadinessCheck did not pass",
            )
        pending = self.confirmations.request(
            purpose=ConfirmationPurpose.LIVE_ENABLE,
            target="live",
            required_fields=("live_enabled",),
            expected={"live_enabled": True},
        )
        self._persist("CONFIRMATION_REQUESTED", "live enable awaiting human confirmation")
        await self._notify(
            "confirmation_requested",
            format_confirmation_request(
                purpose="authorize live mode",
                target="live",
                required_fields=pending.required_fields,
                token=pending.token,
                expires_at=pending.expires_at.isoformat(),
            ),
        )
        return ProgressionResult(
            status="AWAITING_CONFIRMATION",
            from_stage=current,
            to_stage=current,
            passed=list(readiness.passed),
            token=pending.token,
            required_fields=pending.required_fields,
            live_enabled=self._state.live_enabled,
            note=(
                "confirm with this token to authorize live at your own risk; "
                "live order execution is NOT implemented in this build"
            ),
        )

    async def confirm_live_enable(self, token: str) -> ProgressionResult:
        """Apply a live authorization the user explicitly confirmed."""
        current = self._state.stage
        pending = self.confirmations.peek(token)
        if pending is None or pending.purpose != ConfirmationPurpose.LIVE_ENABLE.value:
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                failed=[
                    "confirmation token not recognised for live authorization "
                    "(unknown, expired, already used, or issued for something else)"
                ],
                live_enabled=self._state.live_enabled,
            )
        readiness = self._readiness()
        failed: list[str] = []
        if not is_live_capable(current):
            failed.append(
                f"stage {current.value} is not live-capable; reach "
                f"{ProgressionStage.SMALL_LIVE.value} first"
            )
        failed.extend(readiness.failed)
        if failed:
            self._persist("LIVE_ENABLE_REFUSED", "; ".join(failed))
            await self._notify(
                "live_enable_refused",
                format_live_enable_result(
                    stage=current.value,
                    authorized=False,
                    passed=list(readiness.passed),
                    failed=failed,
                ),
            )
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                passed=list(readiness.passed),
                failed=failed,
                live_enabled=self._state.live_enabled,
                note="token left unused so it can be retried",
            )
        result = self.confirmations.confirm(
            token, payload={"live_enabled": True}, purpose=ConfirmationPurpose.LIVE_ENABLE
        )
        if not result.confirmed:
            return ProgressionResult(
                status="REFUSED",
                from_stage=current,
                to_stage=current,
                failed=list(result.failed),
                live_enabled=self._state.live_enabled,
            )
        self._state = replace(self._state, live_enabled=True, updated_at=_iso(self.clock()))
        self._persist("LIVE_ENABLED", "user-confirmed live authorization (no execution path in this build)")
        await self._notify(
            "live_enabled",
            format_live_enable_result(
                stage=current.value,
                authorized=True,
                passed=list(readiness.passed),
                execution_implemented=LIVE_EXECUTION_IMPLEMENTED,
            ),
        )
        return ProgressionResult(
            status="APPLIED",
            from_stage=current,
            to_stage=current,
            passed=list(readiness.passed),
            live_enabled=True,
            note=(
                "live is AUTHORIZED by the user, but this build has no mainnet order "
                "path: live order execution is not implemented"
            ),
        )

    async def disable_live(self, *, reason: str = "", notify: bool = True) -> ProgressionResult:
        """Turn live authorization OFF. Always allowed — this is the safe direction."""
        current = self._state.stage
        was = self._state.live_enabled
        self._state = replace(self._state, live_enabled=False, updated_at=_iso(self.clock()))
        self._persist("LIVE_DISABLED", reason or "live disabled by user/risk control")
        if notify:
            await self._notify(
                "live_disabled",
                format_live_enable_result(
                    stage=current.value,
                    authorized=False,
                    failed=[reason] if reason else [],
                    execution_implemented=LIVE_EXECUTION_IMPLEMENTED,
                )
                + "\nLive authorization turned OFF (safety direction — always allowed).",
            )
        return ProgressionResult(
            status="APPLIED",
            from_stage=current,
            to_stage=current,
            passed=["live authorization disabled (kill-switch direction is always permitted)"],
            live_enabled=False,
            note=f"was_enabled={was}",
        )

    async def notify_kill_switch(self, *, enabled: bool, detail: str = "") -> bool:
        """Kill-switch event notification (best-effort, never raises)."""
        return await self._notify(
            "kill_switch", format_kill_switch_event(enabled=enabled, detail=detail)
        )

    # ------------------------------------------------------------- read models
    def required_evidence(self, target: ProgressionStage | None = None) -> list[str]:
        """Human-readable list of what the given (or next) stage demands."""
        target = target or self.next_stage()
        if target is None:
            return ["already at the final stage SCALE"]
        return {
            ProgressionStage.PAPER: [
                f"backtest run recorded (run_id)",
                f"backtest trades >= {self.limits.min_backtest_trades}",
                f"backtest profit factor > {self.limits.min_backtest_profit_factor}",
                f"backtest max drawdown <= {self.limits.max_backtest_drawdown:.0%}",
                "walk-forward validation passed",
                "human confirmation token (single use)",
            ],
            ProgressionStage.EXTENDED_PAPER_30D: [
                f"paper days >= {self.limits.min_extended_paper_days}",
                f"paper trades >= {self.limits.min_extended_paper_trades}",
                f"paper profit factor > {self.limits.min_extended_paper_profit_factor}",
                f"paper max drawdown <= {self.limits.max_extended_paper_drawdown:.0%}",
                "human confirmation token (single use)",
            ],
            ProgressionStage.SMALL_LIVE: [
                f"extended paper days >= {self.limits.min_extended_paper_days}",
                f"extended paper trades >= {self.limits.min_extended_paper_trades}",
                "walk-forward validation passed",
                "LiveReadinessCheck.evaluate().ready (all 8 spec conditions)",
                "human confirmation token (single use)",
            ],
            ProgressionStage.SCALE: [
                f"small-live days >= {self.limits.min_small_live_days}",
                f"small-live trades >= {self.limits.min_small_live_trades}",
                f"small-live profit factor > {self.limits.min_small_live_profit_factor}",
                f"small-live max drawdown <= {self.limits.max_small_live_drawdown:.0%}",
                f"explicit clip cap supplied, hard bounded at {self.limits.scale_clip_cap_usdt} USDT",
                "additional explicit human authorization (human_authorized_scale=True)",
                "human confirmation token (single use)",
            ],
        }[target]

    def snapshot(self, *, history_limit: int = 20) -> dict[str, Any]:
        """Everything the dashboard needs in one read-only structure."""
        readiness = self._readiness()
        return {
            "stage": self._state.stage.value,
            "stage_index": stage_index(self._state.stage),
            "stages": [s.value for s in STAGE_ORDER],
            "live_enabled": self._state.live_enabled,
            "live_authorized": self.live_authorized(),
            "live_capable": is_live_capable(self._state.stage),
            "live_execution_implemented": LIVE_EXECUTION_IMPLEMENTED,
            "clip_usdt": self._state.clip_usdt,
            "updated_at": self._state.updated_at,
            "next_stage": self.next_stage().value if self.next_stage() else None,
            "required_evidence": self.required_evidence(),
            "evidence": self._evidence.to_dict(),
            "limits": self.limits.to_dict(),
            "live_readiness": {
                "ready": readiness.ready,
                "passed": list(readiness.passed),
                "failed": list(readiness.failed),
                "summary": readiness.summary(),
            },
            "pending_confirmations": self.confirmations.pending_summaries(),
            "history": self.store.history(history_limit),
            "note": (
                "Nothing in this build places a real order. Live trading is disabled by "
                "default; these endpoints only record evidence and human authorizations."
            ),
        }
