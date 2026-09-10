"""The gate approval pipeline.

Flow (order is fixed and nothing may be skipped):

    Signal -> ShariahGate -> PrayerTimeGate -> RiskGate -> SecurityGate -> ExecutionValidationGate

PrayerTimeGate is always present in the chain (the order/count of gates is a
fixed invariant), but it is a no-op pass-through unless the user has
explicitly enabled ``settings.pause_during_prayer_times`` — see
``gates/prayer_time.py``. It sits right after the Shariah gate because both
are Islamic-compliance concerns, but it is intentionally its own gate rather
than a knob on ShariahGate, which must remain unconditionally forced.

* If any gate returns ``passed=False`` the pipeline short-circuits with
  ``NO_TRADE`` and the remaining gates are NOT reached.
* A gate that sets ``stopped=True`` halts the pipeline entirely (fatal).
* The returned ``PipelineResult`` records EVERY gate outcome for audit even when
  short-circuited, so any rejection is fully explainable.
* ``Pipeline`` is the only entry point for turning a Signal into an executable
  decision. Execution/placement itself is out of scope for this delivery.
"""
from __future__ import annotations

import logging
import uuid

from .config import Settings
from .gates.base import Context, Gate, GateResult
from .gates.execution import ExecutionValidationGate
from .gates.prayer_time import PrayerTimeGate
from .gates.risk import RiskGate
from .gates.security import SecurityGate
from .gates.shariah import ShariahGate
from .models import PipelineDecision, PipelineResult, Signal

logger = logging.getLogger(__name__)

__all__ = ["Pipeline", "Gate", "GateResult"]


class Pipeline:
    """Default ordered pipeline. The gate order is a hard-coded invariant."""

    def __init__(
        self,
        settings: Settings | None = None,
        context: Context | None = None,
        *,
        shariah: Gate | None = None,
        prayer_time: Gate | None = None,
        risk: Gate | None = None,
        security: Gate | None = None,
        execution: Gate | None = None,
    ) -> None:

        self.settings = settings or Settings()

        self.context = context or Context(
            settings=self.settings
        )

        # Injectability exists ONLY to allow the tests to substitute spies that
        # prove short-circuiting. Gate ORDER is fixed here and never configurable.
        self._gates: list[Gate] = [
            shariah or ShariahGate(),
            prayer_time or PrayerTimeGate(),
            risk or RiskGate(),
            security or SecurityGate(),
            execution or ExecutionValidationGate(),
        ]

    @property
    def gate_names(self) -> list[str]:
        return [g.name for g in self._gates]

    def evaluate(
        self,
        signal: Signal
    ) -> PipelineResult:
        """Run every gate in fixed order; short-circuit on the first failure."""

        signal_id = str(uuid.uuid4())

        reasons: list[str] = []
        gate_results: dict[str, GateResult] = {}

        decision = PipelineDecision.TRADE

        for gate in self._gates:

            logger.debug(
                "Signal %s: running gate %s",
                signal_id,
                gate.name
            )

            result = gate.evaluate(
                signal,
                self.context
            )

            gate_results[gate.name] = result
            reasons.extend(result.reasons)

            if not result.passed:

                decision = PipelineDecision.NO_TRADE

                reasons.append(
                    f"BLOCKED by gate '{gate.name}': NO TRADE."
                )

                # Short-circuit — later gates are NOT reached.
                break

            if result.stopped:

                decision = PipelineDecision.NO_TRADE

                reasons.append(
                    f"HALTED by gate '{gate.name}' "
                    "(fatal/stopped)."
                )

                break

        if (
            decision == PipelineDecision.TRADE
            and len(gate_results) < len(self._gates)
        ):

            # Defensive: should never happen given the loop above,
            # but guarantees the invariant "all gates must pass in
            # full order before a trade".
            decision = PipelineDecision.NO_TRADE

            reasons.append(
                "BLOCKED: not all gates evaluated before "
                "a trade decision."
            )

        return PipelineResult(
            decision=decision,
            signal_id=signal_id,
            reasons=reasons,
            gates=gate_results,
            stopped=any(
                r.stopped
                for r in gate_results.values()
            ),
        )

    def evaluate_signal(
        self,
        signal: Signal
    ) -> PipelineResult:
        return self.evaluate(signal)