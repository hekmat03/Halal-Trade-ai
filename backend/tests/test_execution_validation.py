"""Tests for the ExecutionValidationGate — stale data, min notional, status."""
from __future__ import annotations

from datetime import timedelta

from halaltrade.gates.base import Context
from halaltrade.gates.execution import ExecutionValidationGate
from halaltrade.models import utcnow

from conftest import make_signal


def run(signal, context: Context):
    return ExecutionValidationGate().evaluate(signal, context)


def test_rejects_stale_data(context) -> None:
    context.settings.max_data_age_seconds = 5.0
    signal = make_signal(data_timestamp=utcnow() - timedelta(seconds=60))
    result = run(signal, context)
    assert result.passed is False
    assert any("stale" in r for r in result.reasons)


def test_accepts_fresh_data(context) -> None:
    context.settings.max_data_age_seconds = 5.0
    signal = make_signal(data_timestamp=utcnow())
    assert run(signal, context).passed is True


def test_rejects_missing_timestamp(context) -> None:
    signal = make_signal().model_copy(update={"data_timestamp": None})
    assert run(signal, context).passed is False


def test_fails_min_notional(context) -> None:
    context.settings.min_notional = 5.0
    # price 10 x qty 0.1 = notional 1.0 < 5
    signal = make_signal(price=10.0, quantity=0.1)
    result = run(signal, context)
    assert result.passed is False
    assert any("notional" in r for r in result.reasons)


def test_order_status_must_be_confirmed(context) -> None:
    context.order_status_confirmed = False
    signal = make_signal()
    result = run(signal, context)
    assert result.passed is False
    assert any("confirmed" in r for r in result.reasons)


def test_idempotency_rejects_duplicate_client_order_id(context) -> None:
    signal = make_signal(client_order_id="coid-dup")
    assert run(signal, context).passed is True
    # second identical order id -> rejected as duplicate
    assert run(signal, context).passed is False


def test_failed_attempt_does_not_poison_retry(context) -> None:
    # a stale order with a client id fails and must NOT register that id
    stale = make_signal(client_order_id="coid-retry", data_timestamp=utcnow() - timedelta(seconds=99))
    assert run(stale, context).passed is False
    # a fresh order reusing the same client id should now succeed
    fresh = make_signal(client_order_id="coid-retry", data_timestamp=utcnow())
    assert run(fresh, context).passed is True
