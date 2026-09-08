"""Tests for the RiskGate — hard caps, mandatory stop-loss, explicit size."""
from __future__ import annotations

from halaltrade.gates.base import Context, RiskAccount
from halaltrade.gates.risk import RiskGate
from halaltrade.models import InstrumentType, Side

from conftest import make_signal


def run(signal, context: Context):
    return RiskGate().evaluate(signal, context)


def test_rejects_when_no_size(context) -> None:
    # quantity AND amount both missing -> no explicit size -> reject
    signal = make_signal(quantity=None, amount=None, stop_loss=59000.0)
    result = run(signal, context)
    assert result.passed is False
    assert any("size" in r or "amount" in r for r in result.reasons)


def test_rejects_when_amount_missing(context) -> None:
    signal = make_signal(quantity=None, amount=None)
    assert run(signal, context).passed is False


def test_rejects_hold_without_size(context) -> None:
    signal = make_signal(side=Side.HOLD, quantity=None, amount=None)
    assert run(signal, context).passed is False


def test_rejects_missing_stop_loss(context) -> None:
    signal = make_signal(stop_loss=None)
    result = run(signal, context)
    assert result.passed is False
    assert any("stop-loss" in r for r in result.reasons)


def test_accepts_valid_buy_with_stop(context) -> None:
    assert run(make_signal(side=Side.BUY, stop_loss=39000.0), context).passed is True


def test_enforces_max_position_size(context) -> None:
    context.settings.max_position_size = 500.0
    signal = make_signal(quantity=0.05, price=20000.0)  # notional 1000 > 500
    assert run(signal, context).passed is False


def test_enforces_max_exposure(context) -> None:
    context.settings.max_exposure = 2000.0
    # existing position 1900 + new 500 notional = 2400 > 2000
    context.account = RiskAccount(balance=1000.0, current_position_value=1900.0)
    signal = make_signal(quantity=0.01, price=50000.0)  # 500 notional
    assert run(signal, context).passed is False


def test_enforces_max_loss_per_trade(context) -> None:
    context.settings.max_loss_per_trade = 50.0
    # entry 100, stop 50 -> loss 50/unit * 1.5 qty = 75 > 50
    signal = make_signal(price=100.0, quantity=1.5, stop_loss=50.0)
    assert run(signal, context).passed is False


def test_enforces_min_account_balance(context) -> None:
    context.settings.min_account_balance = 100.0
    context.account = RiskAccount(balance=30.0, current_position_value=0.0)
    assert run(make_signal(), context).passed is False


def test_enforces_max_daily_loss(context) -> None:
    context.settings.max_daily_loss = 100.0
    context.account = RiskAccount(balance=1000.0, daily_pnl=-150.0)
    assert run(make_signal(), context).passed is False


def test_enforces_max_drawdown(context) -> None:
    context.settings.max_drawdown = 0.1
    # equity 900 from peak 1200 -> drawdown 25% > 10%
    context.account = RiskAccount(balance=900.0, current_position_value=0.0, equity_peak=1200.0)
    assert run(make_signal(), context).passed is False
