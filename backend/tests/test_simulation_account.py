"""Tests for the shared simulation account (fill, fee, slippage, P&L math).

These prove the core economics both the paper and backtest engines build on:
fees & slippage are always applied, BUY spends amount+fee, shorting is refused.
"""
from __future__ import annotations

import pytest

from halaltrade.simulation import FeeConfig, SimAccount
from halaltrade.simulation.account import PositionError, ShortError


def test_buy_applies_fee_and_decreases_balance_by_amount_plus_fee() -> None:
    acct = SimAccount(starting_usdt=5000.0, fee_config=FeeConfig(taker_bps=10))
    fill = acct.buy(1000.0, 100.0)
    # fill at 100, quantity = 1000/100 = 10, fee = 1000 * 0.001 = 1
    assert fill.price == 100.0
    assert fill.quantity == pytest.approx(10.0)
    assert fill.fee == pytest.approx(1.0)
    # balance falls by amount + fee
    assert acct.usdt == pytest.approx(5000.0 - 1000.0 - 1.0)
    assert acct.btc == pytest.approx(10.0)


def test_buy_applies_slippage_bps() -> None:
    acct = SimAccount(starting_usdt=5000.0, fee_config=FeeConfig(taker_bps=10, slippage_bps=50))
    fill = acct.buy(1050.0, 100.0)
    # BUY fills at a WORSE (higher) price: 100 * (1 + 50/10000) = 100.5
    assert fill.price == pytest.approx(100.5)
    assert fill.quantity == pytest.approx(1050.0 / 100.5)
    # fee charged on the notional the user deployed
    assert fill.fee == pytest.approx(1050.0 * 0.001)


def test_sell_applies_slippage_lower() -> None:
    acct = SimAccount(starting_usdt=5000.0, fee_config=FeeConfig(taker_bps=10, slippage_bps=50))
    acct.buy(1000.0, 100.0)
    # SELL fills at a WORSE (lower) price: 100 * (1 - 50/10000) = 99.5
    fill = acct.sell_amount(1000.0, 100.0)
    assert fill.price == pytest.approx(99.5)
    assert fill.fee == pytest.approx(fill.notional * 0.001)


def test_round_trip_math_is_consistent() -> None:
    acct = SimAccount(starting_usdt=5000.0, fee_config=FeeConfig(taker_bps=10))
    acct.buy(1000.0, 100.0)          # usdt 3999, btc 10, fee 1
    assert acct.usdt == pytest.approx(3999.0)
    assert acct.btc == pytest.approx(10.0)
    acct.sell_amount(1000.0, 100.0)  # sell 10 @100, fee 1, realized -1
    assert acct.usdt == pytest.approx(4998.0)
    assert acct.btc == pytest.approx(0.0)
    assert acct.realized_pnl == pytest.approx(-1.0)
    # equity at the original 100 price == ending cash
    assert acct.equity(100.0) == pytest.approx(4998.0)


def test_refuses_short_without_position() -> None:
    acct = SimAccount(starting_usdt=5000.0)
    with pytest.raises(PositionError):
        acct.sell_amount(100.0, 100.0)
    with pytest.raises(ShortError):
        acct.sell(1.0, 100.0)


def test_refuses_short_above_held_position() -> None:
    acct = SimAccount(starting_usdt=5000.0)
    acct.buy(1000.0, 100.0)   # 10 BTC owned
    with pytest.raises(ShortError):
        acct.sell(12.0, 100.0)  # selling more than owned would short
    # position is untouched after a failed attempt
    assert acct.btc == pytest.approx(10.0)


def test_unrealized_pnl() -> None:
    acct = SimAccount(starting_usdt=5000.0)
    acct.buy(1000.0, 100.0)   # 10 BTC @ 100
    assert acct.unrealized_pnl(110.0) == pytest.approx(10.0 * 10.0)  # +100
    assert acct.unrealized_pnl(90.0) == pytest.approx(-100.0)


def test_stop_loss_closes_position_at_stop_price() -> None:
    acct = SimAccount(starting_usdt=5000.0, fee_config=FeeConfig(taker_bps=10))
    acct.buy(1000.0, 100.0)
    acct.stop_loss = 95.0
    assert acct.hit_stop_loss(94.0) is True
    assert acct.btc == pytest.approx(0.0)
    # position was sold at the stop level: 10 BTC @95, fee 0.95
    assert acct.realized_pnl == pytest.approx(10.0 * (95.0 - 100.0) - 0.95)
