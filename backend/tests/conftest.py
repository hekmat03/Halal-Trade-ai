"""Shared fixtures for the HalalTrade AI test suite."""
from __future__ import annotations

from datetime import timedelta

import pytest

from halaltrade.config import Settings
from halaltrade.gates.base import Context, RiskAccount
from halaltrade.models import Signal, InstrumentType, Side, utcnow


@pytest.fixture
def settings() -> Settings:
    """A Settings object with deterministic, non-secret defaults for tests."""
    return Settings(
        trading_mode="paper",
        live_enabled=False,
        api_key_restricted=True,
        system_healthy=True,
        max_position_size=500.0,
        max_exposure=2000.0,
        max_loss_per_trade=50.0,
        max_daily_loss=100.0,
        max_drawdown=0.10,
        min_account_balance=100.0,
        min_notional=5.0,
        max_data_age_seconds=5.0,
    )


@pytest.fixture
def context(settings: Settings) -> Context:
    return Context(
        settings=settings,
        account=RiskAccount(balance=1000.0, current_position_value=0.0),
        order_status_confirmed=True,
    )


def make_signal(
    *,
    side: Side = Side.BUY,
    instrument_type: InstrumentType = InstrumentType.SPOT,
    leverage: float = 1.0,
    price: float = 40000.0,
    quantity: float = 0.01,
    amount: float | None = None,
    stop_loss: float | None = 39000.0,
    data_timestamp=None,
    client_order_id: str | None = "coid-1",
    **kwargs,
) -> Signal:
    return Signal(
        side=side,
        instrument_type=instrument_type,
        leverage=leverage,
        price=price,
        quantity=quantity,
        amount=amount,
        stop_loss=stop_loss,
        data_timestamp=data_timestamp if data_timestamp is not None else utcnow(),
        client_order_id=client_order_id,
        **kwargs,
    )


@pytest.fixture
def fresh_signal():
    return make_signal(data_timestamp=utcnow())


@pytest.fixture
def stale_signal():
    return make_signal(data_timestamp=utcnow() - timedelta(seconds=60))
