"""Tests for the position tracker (Delivery 5) + strengthened risk checks.

Fully offline: fills come from the shared SimAccount economics (same math the
paper/backtest engines use), exits are driven by direct on_price() pumps.
"""
from __future__ import annotations

import pytest

from halaltrade.config import Settings
from halaltrade.gates.base import Context, RiskAccount
from halaltrade.gates.risk import RiskGate
from halaltrade.models import Side
from halaltrade.positions import PositionTracker, TrailingConfig
from halaltrade.simulation import FeeConfig, SimAccount

from conftest import make_signal


def settings(**over) -> Settings:
    defaults = dict(
        trading_mode="paper", live_enabled=False,
        max_position_size=10000.0, max_exposure=20000.0,
        max_loss_per_trade=5000.0, max_daily_loss=1000.0,
        max_drawdown=0.5, min_account_balance=100.0,
        min_notional=5.0, max_data_age_seconds=5.0,
        qty_step=0.00001,
    )
    defaults.update(over)
    return Settings(**defaults)


def tracker(price: float = 100.0, **over) -> PositionTracker:
    acct = SimAccount(starting_usdt=5000.0,
                      fee_config=FeeConfig(taker_bps=0, slippage_bps=0))
    t = PositionTracker(acct, settings(**over))
    return t


# --------------------------------------------------------------------------------------
# Position state from fills (qty, avg entry, realized/unrealized P&L)
# --------------------------------------------------------------------------------------

def test_tracks_qty_avg_entry_and_pnl() -> None:
    t = tracker()
    t.apply_buy_fill(1000.0, 100.0, stop_loss=95.0)
    view = t.view(110.0)
    assert view.has_position is True
    assert view.quantity == pytest.approx(10.0)
    assert view.avg_entry_price == pytest.approx(100.0)
    assert view.unrealized_pnl == pytest.approx(10.0 * (110.0 - 100.0))
    assert view.stop_loss == pytest.approx(95.0)
    # Realizing half the position books realized P&L.
    t.apply_sell_fill(5.0, 110.0)
    assert t.account.realized_pnl == pytest.approx(5.0 * 10.0)
    assert t.view(110.0).quantity == pytest.approx(5.0)


# --------------------------------------------------------------------------------------
# Mandatory stop-loss flattens; take-profit + trailing work
# --------------------------------------------------------------------------------------

def test_stop_loss_triggers_flatten() -> None:
    t = tracker()
    t.apply_buy_fill(1000.0, 100.0, stop_loss=95.0)
    assert t.on_price(96.0).closed is False       # above the stop: nothing
    report = t.on_price(94.0)                      # at/under the stop: flatten
    assert report.closed is True
    assert report.reason == "STOP_LOSS"
    assert report.price == pytest.approx(95.0)     # filled AT the stop level
    assert t.account.has_position is False


def test_take_profit_triggers_flatten() -> None:
    t = tracker()
    t.apply_buy_fill(1000.0, 100.0, stop_loss=95.0, take_profit=110.0)
    assert t.on_price(109.0).closed is False
    report = t.on_price(111.0)
    assert report.closed is True
    assert report.reason == "TAKE_PROFIT"
    assert t.account.has_position is False


def test_stop_has_priority_over_tp_on_same_print() -> None:
    t = tracker()
    t.apply_buy_fill(1000.0, 100.0, stop_loss=100.0, take_profit=100.0)
    report = t.on_price(100.0)  # touches both -> stop wins
    assert report.closed is True
    assert report.reason == "STOP_LOSS"


def test_trailing_stop_ratchets_and_fires() -> None:
    t = tracker()
    t.apply_buy_fill(1000.0, 100.0, stop_loss=90.0,
                     trailing=TrailingConfig(enabled=True, distance=5.0))
    assert t.on_price(110.0).closed is False       # peak 110 -> trail at 105
    assert t.on_price(107.0).closed is False       # above the trail: hold
    report = t.on_price(104.0)                     # below 105: trailing exit
    assert report.closed is True
    assert report.reason == "TRAILING_STOP"
    assert t.account.has_position is False


def test_trailing_never_moves_down() -> None:
    t = tracker()
    t.apply_buy_fill(1000.0, 100.0, stop_loss=90.0,
                     trailing=TrailingConfig(enabled=True, distance=5.0))
    t.on_price(110.0)                              # trail ratchets to 105
    t.on_price(106.0)                              # dip: trail must NOT follow down
    assert t._trailing_stop == pytest.approx(105.0)


# --------------------------------------------------------------------------------------
# Dust handling: residual below step is flattened once, not retried
# --------------------------------------------------------------------------------------

def test_dust_residual_flattened_without_repeated_orders() -> None:
    t = tracker()
    t.apply_buy_fill(1000.0, 100.0, stop_loss=90.0)
    # Sell everything but a sub-step crumb.
    t.apply_sell_fill(t.account.btc - 0.000001, 100.0)
    assert 0.0 < t.account.btc < t.settings.qty_step
    report = t.flatten_dust(100.0)
    assert report.closed is True
    assert report.reason == "DUST_FLATTEN"
    assert t.account.has_position is False


def test_flatten_dust_noop_on_tradeable_size() -> None:
    t = tracker()
    t.apply_buy_fill(1000.0, 100.0, stop_loss=90.0)
    report = t.flatten_dust(100.0)  # 10 BTC is a normal size -> normal close path
    assert report.closed is False
    assert t.account.has_position is True


# --------------------------------------------------------------------------------------
# Never-short invariant
# --------------------------------------------------------------------------------------

def test_cannot_sell_more_than_held_gate_and_fill() -> None:
    t = tracker()
    # Gate level: explicit SELL quantity above holdings is rejected.
    ctx = Context(
        settings=settings(),
        account=RiskAccount(balance=5000.0, base_holdings=1.0),
        order_status_confirmed=True,
    )
    sig = make_signal(side=Side.SELL, quantity=2.0, price=100.0,
                      amount=None, stop_loss=90.0)
    result = RiskGate().evaluate(sig, ctx)
    assert result.passed is False
    assert any("short" in r.lower() for r in result.reasons)
    # Fill level: the simulation still refuses as the final backstop.
    with pytest.raises(Exception):
        t.apply_sell_fill(1.0, 100.0)  # flat account: nothing owned


def test_sell_within_holdings_passes_gate() -> None:
    ctx = Context(
        settings=settings(),
        account=RiskAccount(balance=5000.0, base_holdings=1.0),
        order_status_confirmed=True,
    )
    sig = make_signal(side=Side.SELL, quantity=0.5, price=100.0,
                      amount=None, stop_loss=90.0)
    assert RiskGate().evaluate(sig, ctx).passed is True


# --------------------------------------------------------------------------------------
# Strengthened risk checks: position cap, daily-loss, exposure counts position
# --------------------------------------------------------------------------------------

def test_second_position_rejected_when_cap_is_one() -> None:
    ctx = Context(
        settings=settings(),  # max_open_positions defaults to 1
        account=RiskAccount(balance=5000.0, open_position_count=1),
        order_status_confirmed=True,
    )
    result = RiskGate().evaluate(make_signal(), ctx)  # default is a BUY
    assert result.passed is False
    assert any("max_open_positions" in r for r in result.reasons)


def test_first_position_allowed_when_flat() -> None:
    ctx = Context(
        settings=settings(),
        account=RiskAccount(balance=5000.0, open_position_count=0),
        order_status_confirmed=True,
    )
    assert RiskGate().evaluate(make_signal(), ctx).passed is True


def test_daily_loss_cap_consults_realized_pnl() -> None:
    ctx = Context(
        settings=settings(max_daily_loss=100.0),
        account=RiskAccount(balance=5000.0, realized_pnl_today=-150.0),
        order_status_confirmed=True,
    )
    result = RiskGate().evaluate(make_signal(), ctx)
    assert result.passed is False
    assert any("daily" in r.lower() for r in result.reasons)


def test_daily_loss_ok_when_within_cap() -> None:
    ctx = Context(
        settings=settings(max_daily_loss=100.0),
        account=RiskAccount(balance=5000.0, realized_pnl_today=-20.0),
        order_status_confirmed=True,
    )
    assert RiskGate().evaluate(make_signal(), ctx).passed is True


def test_every_rejection_carries_audit_friendly_reason() -> None:
    ctx = Context(
        settings=settings(max_open_positions=1),
        account=RiskAccount(balance=5000.0, open_position_count=5),
        order_status_confirmed=True,
    )
    result = RiskGate().evaluate(make_signal(), ctx)
    assert result.passed is False
    assert all(r.startswith(("OK:", "REJECT:")) for r in result.reasons)
    assert any(r.startswith("REJECT:") for r in result.reasons)


def test_exposure_cap_counts_open_position() -> None:
    t = tracker()
    t.apply_buy_fill(1000.0, 100.0, stop_loss=90.0)  # open exposure = 10*100
    price = 100.0
    # A new BUY whose notional alone fits max_exposure, but stacked on the open
    # position exceeds it, must be rejected.
    ctx = Context(
        settings=settings(max_exposure=1200.0),
        account=t.risk_account(price),
        order_status_confirmed=True,
    )
    sig = make_signal(price=price, quantity=0.005, amount=None, stop_loss=90.0)
    result = RiskGate().evaluate(sig, ctx)
    assert result.passed is False
    assert any("exposure" in r.lower() for r in result.reasons)


def test_position_aware_risk_account_snapshot() -> None:
    t = tracker()
    t.apply_buy_fill(1000.0, 100.0, stop_loss=90.0)
    snap = t.risk_account(100.0)
    assert snap.open_position_count == 1
    assert snap.base_holdings == pytest.approx(10.0)
    assert snap.current_position_value == pytest.approx(1000.0)
