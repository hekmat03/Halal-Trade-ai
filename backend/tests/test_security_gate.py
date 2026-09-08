"""Tests for the SecurityGate — secrets from env, restricted keys, health."""
from __future__ import annotations

from halaltrade.config import Settings
from halaltrade.gates.base import Context
from halaltrade.gates.security import SecurityGate
from halaltrade.models import InstrumentType, Side

from conftest import make_signal


def run(signal, context: Context):
    return SecurityGate().evaluate(signal, context)


def test_passes_in_healthy_paper_mode(settings, context) -> None:
    assert run(make_signal(), context).passed is True


def test_rejects_and_stops_when_system_unhealthy(settings, context) -> None:
    settings.system_healthy = False
    result = run(make_signal(), context)
    assert result.passed is False
    assert result.stopped is True  # STOP: no further trading at all


def test_rejects_and_stops_when_withdrawals_permitted(settings, context) -> None:
    settings.api_key_restricted = False
    result = run(make_signal(), context)
    assert result.passed is False
    assert result.stopped is True


def test_rejects_and_stops_when_live_credentials_missing(settings, context) -> None:
    settings.trading_mode = "live"
    settings.live_enabled = True
    settings.binance_api_key = ""
    settings.binance_api_secret = ""
    result = run(make_signal(), context)
    assert result.passed is False
    assert result.stopped is True
    assert any("credentials" in r for r in result.reasons)


def test_passes_when_live_credentials_present(settings, context) -> None:
    settings.trading_mode = "live"
    settings.live_enabled = True
    settings.binance_api_key = "k-abc"
    settings.binance_api_secret = "s-xyz"
    assert run(make_signal(), context).passed is True


def test_secrets_never_accepted_as_parameters() -> None:
    """The gate takes only (signal, context) — there is no route for a caller to
    hand secrets to this gate directly; they can only come from Settings (env)."""
    import inspect

    sig = inspect.signature(SecurityGate.evaluate)
    params = list(sig.parameters)
    assert params == ["self", "signal", "context"]