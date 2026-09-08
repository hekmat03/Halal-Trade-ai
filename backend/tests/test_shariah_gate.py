"""Tests for the ShariahGate — Spot-only, FORCED, non-bypassable."""
from __future__ import annotations

import pytest

from halaltrade.gates.base import Context
from halaltrade.gates.shariah import ShariahGate, FORBIDDEN_INSTRUMENTS
from halaltrade.models import (
    InstrumentType,
    ShariahResult,
    Side,
    Signal,
)

from conftest import make_signal


def run(signal: Signal, context: Context) -> ShariahResult:
    gate = ShariahGate()
    return gate.evaluate(signal, context)


def test_gate_returns_structured_result(fresh_signal) -> None:
    result = run(fresh_signal, Context())
    assert isinstance(result, ShariahResult)
    assert result.gate_name == "shariah"
    assert isinstance(result.passed, bool)
    assert isinstance(result.reasons, list)


def test_accepts_spot_buy() -> None:
    result = run(make_signal(side=Side.BUY, instrument_type=InstrumentType.SPOT), Context())
    assert result.passed is True


def test_accepts_spot_sell() -> None:
    result = run(make_signal(side=Side.SELL, instrument_type=InstrumentType.SPOT), Context())
    assert result.passed is True


@pytest.mark.parametrize(
    "instrument",
    [
        InstrumentType.FUTURES,
        InstrumentType.PERPETUAL,
        InstrumentType.LEVERAGED_TOKEN,
        InstrumentType.MARGIN,
        InstrumentType.OPTIONS,
        InstrumentType.DERIVATIVE,
        InstrumentType.STAKING,
        InstrumentType.LENDING,
        InstrumentType.BORROWED,
    ],
)
def test_rejects_every_non_spot_instrument(instrument) -> None:
    result = run(make_signal(side=Side.BUY, instrument_type=instrument), Context())
    assert result.passed is False
    assert result.reasons  # every rejection is recorded


def test_forbidden_instruments_exhaustively_excludes_spot() -> None:
    assert InstrumentType.SPOT not in FORBIDDEN_INSTRUMENTS
    assert len(FORBIDDEN_INSTRUMENTS) == len(InstrumentType) - 1


def test_rejects_leverage() -> None:
    result = run(
        make_signal(side=Side.BUY, instrument_type=InstrumentType.SPOT, leverage=3.0),
        Context(),
    )
    assert result.passed is False
    assert any("leverage" in r for r in result.reasons)


def test_rejects_unrecognized_side() -> None:
    signal = make_signal(side=Side.BUY, instrument_type=InstrumentType.SPOT).model_copy(
        update={"side": "SHORT"}
    )
    result = run(signal, Context())
    assert result.passed is False
    assert any("side" in r.lower() for r in result.reasons)


# --------------------------------------------------------------------------------------
# FORCED / no bypass — the critical invariant
# --------------------------------------------------------------------------------------


def test_gate_is_forced_and_not_bypassable() -> None:
    assert ShariahGate.FORCED is True
    assert ShariahGate.BYPASSABLE is False  # no bypass exists


def test_no_disable_attribute_exists_on_gate() -> None:
    """There is no knob, flag or config to turn the Shariah gate off."""
    for disabling_name in ("disable", "enabled", "bypass", "skip", "force_off"):
        assert not hasattr(ShariahGate, disabling_name), f"found disabling attr {disabling_name}"
        assert not hasattr(ShariahGate(), disabling_name)


def test_evaluate_rejects_a_bypass_kwarg() -> None:
    """Even attempting to pass a bypass flag fails loudly (TypeError)."""
    gate = ShariahGate()
    signal = make_signal(side=Side.BUY, instrument_type=InstrumentType.SPOT)
    with pytest.raises(TypeError):
        gate.evaluate(signal, Context(), bypass=True)  # type: ignore[call-arg]


def test_no_bypass_returns_same_result_every_time(fresh_signal) -> None:
    """Forced means deterministic: a non-spot signal can never pass, by any route."""
    gate = ShariahGate()
    futures = make_signal(side=Side.BUY, instrument_type=InstrumentType.FUTURES)
    for _ in range(5):
        assert gate.evaluate(futures, Context()).passed is False
    assert gate.evaluate(fresh_signal, Context()).passed is True
