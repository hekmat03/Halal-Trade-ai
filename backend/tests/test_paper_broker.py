"""Tests for the Paper trading engine (Delivery 3 — default operating mode).

Everything is mocked: a fake market-data source serves a deterministic price so
no network is touched. The broker must enforce the policy gates, no-shorting, no
stale data, the manual-size rule, mandatory stop-loss, and idempotent
clientOrderId — all before any simulated fill.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from halaltrade.config import Settings
from halaltrade.marketdata import (
    DataUnavailableError,
    MarketDataSource,
    Ticker,
)
from halaltrade.models import InstrumentType, Side, Signal, utcnow
from halaltrade.paper import PaperBroker
from halaltrade.simulation import FeeConfig


class FakeSource(MarketDataSource):
    """Deterministic offline price source."""

    def __init__(self, price=100.0, *, stale=False, fail=False, now=None) -> None:
        super().__init__(now=now or utcnow)
        self.price = price
        self._stale = stale
        self._fail = fail

    async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker:
        if self._fail:
            raise DataUnavailableError("down", source="fake")
        ts = self._now()
        if self._stale:
            ts = ts - timedelta(seconds=60)
        return Ticker(symbol=symbol, price=self.price, timestamp=ts, source="fake")

    async def get_candles(self, symbol="BTCUSDT", timeframe="1m", limit=100):
        return []


def make_buy(amount=1000.0, *, stop=95.0, instrument=InstrumentType.SPOT,
             coid="coid-buy", side=Side.BUY) -> Signal:
    return Signal(
        side=side, symbol="BTCUSDT", instrument_type=instrument, leverage=1.0,
        amount=amount, stop_loss=stop, price=100.0, data_timestamp=utcnow(),
        client_order_id=coid,
    )


def broker(price=100.0, *, fee=None, starting=5000.0, **kw):
    return PaperBroker(
        Settings(
            trading_mode="paper", live_enabled=False,
            max_position_size=10000.0, max_exposure=20000.0,
        ),
        data_source=FakeSource(price=price, **kw),
        starting_usdt=starting,
        fee_config=fee or FeeConfig(taker_bps=10, slippage_bps=0),
    )


@pytest.mark.asyncio
async def test_paper_buy_then_sell_round_trip_math() -> None:
    b = broker(price=100.0)
    bu = await b.execute(make_buy(amount=1000.0, stop=95.0))
    assert bu.executed is True
    assert b.usdt_balance == pytest.approx(3999.0)   # 5000 - 1000 - 1
    assert b.btc_holdings == pytest.approx(10.0)
    assert bu.equity_after == pytest.approx(5000.0 - 1.0)  # cash + 10*100

    sell = await b.execute(make_buy(amount=1000.0, stop=95.0, side=Side.SELL,
                                    coid="coid-sell"))
    assert sell.executed is True
    assert b.usdt_balance == pytest.approx(4998.0)
    assert b.btc_holdings == pytest.approx(0.0)
    assert b.account.realized_pnl == pytest.approx(-1.0)
    assert sell.equity_after == pytest.approx(4998.0)


@pytest.mark.asyncio
async def test_paper_buy_applies_slippage() -> None:
    b = broker(price=100.0, fee=FeeConfig(taker_bps=10, slippage_bps=50))
    res = await b.execute(make_buy(amount=1050.0, stop=95.0))
    assert res.executed is True
    assert res.fill.price == pytest.approx(100.5)          # BUY fills higher
    assert res.fill.quantity == pytest.approx(1050.0 / 100.5)


@pytest.mark.asyncio
async def test_refuses_short_sell_without_position() -> None:
    b = broker(price=100.0)
    res = await b.execute(make_buy(amount=1000.0, stop=95.0, side=Side.SELL,
                                   coid="coid-short"))
    assert res.executed is False
    assert b.btc_holdings == pytest.approx(0.0)
    assert b.usdt_balance == pytest.approx(5000.0)


@pytest.mark.asyncio
async def test_refuses_stale_data() -> None:
    b = broker(price=100.0, stale=True)
    res = await b.execute(make_buy(amount=1000.0, stop=95.0))
    assert res.executed is False
    assert b.btc_holdings == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_refuses_when_source_down() -> None:
    b = broker(price=100.0, fail=True)
    res = await b.execute(make_buy(amount=1000.0, stop=95.0))
    assert res.executed is False


@pytest.mark.asyncio
async def test_refuses_shariah_violation_futures() -> None:
    b = broker(price=100.0)
    sig = make_buy(amount=1000.0, stop=95.0, instrument=InstrumentType.FUTURES)
    res = await b.execute(sig)
    assert res.executed is False
    assert b.btc_holdings == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_refuses_trade_without_mandatory_stop_loss() -> None:
    b = broker(price=100.0)
    res = await b.execute(make_buy(amount=1000.0, stop=None))
    assert res.executed is False   # risk gate rejects a missing stop-loss


@pytest.mark.asyncio
async def test_refuses_trade_without_exact_user_amount() -> None:
    b = broker(price=100.0)
    # No amount (only quantity) — the user did not provide an exact size.
    sig = Signal(side=Side.BUY, price=100.0, quantity=0.01, stop_loss=95.0,
                 data_timestamp=utcnow(), client_order_id="coid-qty")
    res = await b.execute(sig)
    assert res.executed is False


@pytest.mark.asyncio
async def test_refuses_trade_over_position_cap() -> None:
    b = broker(price=100.0)   # max_position_size = 10000
    res = await b.execute(make_buy(amount=12000.0, stop=95.0))
    assert res.executed is False   # risk gate rejects size > max_position_size
    assert b.btc_holdings == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_stop_loss_enforced_on_paper_position() -> None:
    b = broker(price=100.0)
    res = await b.execute(make_buy(amount=1000.0, stop=95.0))
    assert res.executed is True
    assert b.btc_holdings == pytest.approx(10.0)
    # Price falls to/under the stop -> the position must be closed
    fired = b.close_all_for_stop_loss(94.0)
    assert fired is True
    assert b.btc_holdings == pytest.approx(0.0)
    # closed at the stop level 95, so loss is bounded by the stop distance (fees aside)
    assert b.account.realized_pnl < 0


@pytest.mark.asyncio
async def test_idempotent_client_order_id_prevents_duplicate_fill() -> None:
    b = broker(price=100.0)
    first = await b.execute(make_buy(amount=1000.0, stop=95.0, coid="same-id"))
    assert first.executed is True
    # Same clientOrderId resubmitted -> must NOT create a second fill.
    second = await b.execute(make_buy(amount=1000.0, stop=95.0, coid="same-id"))
    assert second.executed is False
    assert b.btc_holdings == pytest.approx(10.0)      # not doubled
    assert b.usdt_balance == pytest.approx(3999.0)    # not spent twice
