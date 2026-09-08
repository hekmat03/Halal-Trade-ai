"""Tests for the Pipeline — ordering, short-circuit, NO TRADE, no skipped gates."""
from __future__ import annotations

import pytest

from halaltrade.config import Settings
from halaltrade.gates.base import Context, Gate, GateResult, RiskAccount
from halaltrade.gates.execution import ExecutionValidationGate
from halaltrade.gates.risk import RiskGate
from halaltrade.gates.security import SecurityGate
from halaltrade.gates.shariah import ShariahGate
from halaltrade.models import (
    InstrumentType,
    PipelineDecision,
    PipelineResult,
    Side,
    Signal,
)

from conftest import make_signal


class RecordingGate(Gate):
    """Wraps a real gate and records whether it was reached."""

    calls: int = 0
    inner: Gate

    def __init__(self, inner: Gate):
        self.inner = inner
        self.calls = 0
        self.name = inner.name

    def evaluate(self, signal: Signal, context: Context) -> GateResult:
        self.calls += 1
        return self.inner.evaluate(signal, context)


def build_pipeline(context: Context, *, with_spies: bool = False):
    if not with_spies:
        return ShariahGate(), RiskGate(), SecurityGate(), ExecutionValidationGate()
    spies = (
        RecordingGate(RiskGate()),
        RecordingGate(SecurityGate()),
        RecordingGate(ExecutionValidationGate()),
    )
    return ShariahGate(), *spies


def test_full_pipeline_passes_valid_spot_buy(context) -> None:
    from halaltrade.pipeline import Pipeline

    pipeline = Pipeline(context=context)
    signal = make_signal(side=Side.BUY, instrument_type=InstrumentType.SPOT)
    result = pipeline.evaluate(signal)
    assert result.decision == PipelineDecision.TRADE
    # NO gate may be skipped on a trade.
    assert set(result.gates.keys()) == {"shariah", "risk", "security", "execution"}


def test_no_trade_when_shariah_fails(context) -> None:
    from halaltrade.pipeline import Pipeline

    pipeline = Pipeline(context=context)
    signal = make_signal(side=Side.BUY, instrument_type=InstrumentType.FUTURES)
    result = pipeline.evaluate(signal)
    assert result.decision == PipelineDecision.NO_TRADE
    assert not result.gates["shariah"].passed


def test_short_circuits_after_first_failure(context) -> None:
    from halaltrade.pipeline import Pipeline

    shariah, risk, security, execution = build_pipeline(context, with_spies=True)
    pipeline = Pipeline(context=context, risk=risk, security=security, execution=execution)
    # futures signal fails the Shariah gate first
    signal = make_signal(side=Side.BUY, instrument_type=InstrumentType.FUTURES)
    result = pipeline.evaluate(signal)
    assert result.decision == PipelineDecision.NO_TRADE
    # later gates were NEVER reached
    assert risk.calls == 0
    assert security.calls == 0
    assert execution.calls == 0


def test_short_circuits_at_risk(context) -> None:
    from halaltrade.pipeline import Pipeline

    shariah, risk, security, execution = build_pipeline(context, with_spies=True)
    pipeline = Pipeline(context=context, risk=risk, security=security, execution=execution)
    # spot but missing size -> Risk gate fails
    signal = make_signal(side=Side.BUY, instrument_type=InstrumentType.SPOT, quantity=None, amount=None)
    result = pipeline.evaluate(signal)
    assert result.decision == PipelineDecision.NO_TRADE
    assert risk.calls == 1  # risk was reached
    assert security.calls == 0  # security and execution were NOT
    assert execution.calls == 0


def test_security_stop_halts_pipeline(context) -> None:
    from halaltrade.pipeline import Pipeline

    context.settings.system_healthy = False
    pipeline = Pipeline(context=context)
    signal = make_signal(side=Side.BUY, instrument_type=InstrumentType.SPOT)
    result = pipeline.evaluate(signal)
    assert result.decision == PipelineDecision.NO_TRADE
    assert result.stopped is True
    assert result.gates["security"].stopped is True


def test_gate_order_is_fixed(context) -> None:
    from halaltrade.pipeline import Pipeline

    pipeline = Pipeline(context=context)
    assert pipeline.gate_names == ["shariah", "risk", "security", "execution"]


def test_result_contains_every_gate_context_for_audit(context) -> None:
    from halaltrade.pipeline import Pipeline

    pipeline = Pipeline(context=context)
    result = pipeline.evaluate(
        make_signal(side=Side.BUY, instrument_type=InstrumentType.FUTURES)
    )
    assert isinstance(result, PipelineResult)
    assert "shariah" in result.gates
    assert len(result.reasons) > 0
