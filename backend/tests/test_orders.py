"""Tests for the order manager (Delivery 5) — lifecycle, idempotency, fills.

Fully offline: the OrderManager never touches the network. Submits validate
against Settings only; fills are confirmed by the caller (paper broker in
production, direct apply_fill calls here).
"""
from __future__ import annotations

import pytest

from halaltrade.config import Settings
from halaltrade.orders import (
    OrderManager,
    OrderRequest,
    new_client_order_id,
)
from halaltrade.orders.validation import OrderValidationError, validate_order


def settings() -> Settings:
    return Settings(
        trading_mode="paper",
        live_enabled=False,
        min_notional=5.0,
        qty_step=0.00001,
    )


def market_buy(**over) -> OrderRequest:
    # Default notional 0.1 * 100 = 10 USDT (above the 5 USDT minimum).
    kw = dict(side="BUY", symbol="BTCUSDT", order_type="MARKET",
              quantity=0.1, price=100.0)
    kw.update(over)
    return OrderRequest(**kw)


# --------------------------------------------------------------------------------------
# clientOrderId uniqueness
# --------------------------------------------------------------------------------------

def test_client_order_ids_are_unique() -> None:
    ids = {new_client_order_id() for _ in range(200)}
    assert len(ids) == 200


def test_submit_assigns_id_when_missing() -> None:
    mgr = OrderManager(settings())
    view = mgr.submit(market_buy(client_order_id=None))
    assert view.client_order_id
    assert view.status == "NEW"


# --------------------------------------------------------------------------------------
# Idempotent retry never duplicates
# --------------------------------------------------------------------------------------

def test_idempotent_retry_returns_original_result() -> None:
    mgr = OrderManager(settings())
    first = mgr.submit(market_buy(client_order_id="retry-1"))
    assert first.status == "NEW"
    mgr.apply_fill("retry-1", 0.1, 100.0)
    # Retry of the submitted order returns the ORIGINAL result, not a duplicate.
    second = mgr.submit(market_buy(client_order_id="retry-1"))
    assert second.status == "FILLED"
    assert second.executed_qty == pytest.approx(0.1)
    assert len(mgr._orders) == 1  # exactly one tracked order, never duplicated


def test_retry_of_rejected_order_returns_rejection() -> None:
    mgr = OrderManager(settings())
    bad = market_buy(client_order_id="bad-1", quantity=0.0000001, price=100.0)
    first = mgr.submit(bad)
    assert first.status == "REJECTED"
    second = mgr.submit(bad)
    assert second.status == "REJECTED"
    assert len(mgr._orders) == 0  # rejected orders are never tracked


# --------------------------------------------------------------------------------------
# Lifecycle: NEW -> PARTIALLY_FILLED -> FILLED
# --------------------------------------------------------------------------------------

def test_partial_fills_accumulate_to_filled() -> None:
    mgr = OrderManager(settings())
    mgr.submit(market_buy(client_order_id="pf-1", quantity=0.2, price=100.0))
    v1 = mgr.apply_fill("pf-1", 0.08, 100.0)
    assert v1.status == "PARTIALLY_FILLED"
    assert v1.is_complete is False
    assert v1.executed_qty == pytest.approx(0.08)
    assert v1.cummulative_quote_qty == pytest.approx(8.0)
    v2 = mgr.apply_fill("pf-1", 0.12, 101.0)
    assert v2.status == "FILLED"
    assert v2.is_complete is True
    assert v2.executed_qty == pytest.approx(0.2)
    assert v2.cummulative_quote_qty == pytest.approx(0.08 * 100.0 + 0.12 * 101.0)


def test_order_complete_only_at_filled() -> None:
    mgr = OrderManager(settings())
    mgr.submit(market_buy(client_order_id="pf-2", quantity=0.2, price=100.0))
    partial = mgr.apply_fill("pf-2", 0.199999, 100.0)
    # A hair below full (beyond dust tolerance) is still partial, not complete.
    assert partial.status == "PARTIALLY_FILLED"
    assert partial.is_complete is False


def test_terminal_fill_is_stable() -> None:
    mgr = OrderManager(settings())
    mgr.submit(market_buy(client_order_id="t-1", quantity=0.1, price=100.0))
    mgr.apply_fill("t-1", 0.1, 100.0)
    again = mgr.apply_fill("t-1", 0.1, 100.0)  # terminal: no change
    assert again.status == "FILLED"
    assert again.executed_qty == pytest.approx(0.1)


def test_fill_unknown_order_raises() -> None:
    mgr = OrderManager(settings())
    with pytest.raises(KeyError):
        mgr.apply_fill("nope", 0.01, 100.0)


# --------------------------------------------------------------------------------------
# Cancel by clientOrderId (idempotent)
# --------------------------------------------------------------------------------------

def test_cancel_open_order() -> None:
    mgr = OrderManager(settings())
    mgr.submit(market_buy(client_order_id="c-1"))
    view = mgr.cancel("c-1")
    assert view.status == "CANCELED"
    assert view.is_complete is False


def test_cancel_is_idempotent_on_terminal_order() -> None:
    mgr = OrderManager(settings())
    mgr.submit(market_buy(client_order_id="c-2"))
    mgr.cancel("c-2")
    again = mgr.cancel("c-2")  # already terminal: returns current view as-is
    assert again.status == "CANCELED"


def test_cancel_unknown_order_raises() -> None:
    mgr = OrderManager(settings())
    with pytest.raises(KeyError):
        mgr.cancel("ghost")


def test_cancel_after_partial_fill_keeps_executed() -> None:
    mgr = OrderManager(settings())
    mgr.submit(market_buy(client_order_id="c-3", quantity=0.2, price=100.0))
    mgr.apply_fill("c-3", 0.08, 100.0)
    view = mgr.cancel("c-3")
    assert view.status == "CANCELED"
    assert view.executed_qty == pytest.approx(0.08)


# --------------------------------------------------------------------------------------
# Limit orders: TIF + POST_ONLY
# --------------------------------------------------------------------------------------

def test_limit_gtc_rests_as_new() -> None:
    mgr = OrderManager(settings())
    view = mgr.submit(OrderRequest(side="BUY", order_type="LIMIT",
                                   quantity=0.1, price=99.0,
                                   time_in_force="GTC", client_order_id="lim-1"))
    assert view.status == "NEW"


def test_post_only_crossing_limit_rejected() -> None:
    mgr = OrderManager(settings())
    # BUY limit 101 >= market 100 would take immediately -> maker-only violated.
    view = mgr.submit(OrderRequest(side="BUY", order_type="LIMIT",
                                   quantity=0.1, price=101.0,
                                   time_in_force="GTC", post_only=True,
                                   client_order_id="po-1"),
                      market_price=100.0)
    assert view.status == "REJECTED"
    assert "POST_ONLY" in view.reason


def test_post_only_non_crossing_rests() -> None:
    mgr = OrderManager(settings())
    # BUY limit 99 < market 100 rests on the book (maker).
    view = mgr.submit(OrderRequest(side="BUY", order_type="LIMIT",
                                   quantity=0.1, price=99.0,
                                   time_in_force="GTC", post_only=True,
                                   client_order_id="po-2"),
                      market_price=100.0)
    assert view.status == "NEW"


def test_bad_tif_rejected() -> None:
    mgr = OrderManager(settings())
    view = mgr.submit(OrderRequest(side="BUY", order_type="LIMIT",
                                   quantity=0.1, price=99.0,
                                   time_in_force="DAY",  # type: ignore[arg-type]
                                   client_order_id="tif-1"))
    assert view.status == "REJECTED"


def test_expire_resting_limit() -> None:
    mgr = OrderManager(settings())
    mgr.submit(OrderRequest(side="BUY", order_type="LIMIT",
                            quantity=0.1, price=99.0,
                            time_in_force="IOC", client_order_id="ioc-1"))
    view = mgr.expire("ioc-1", reason="IOC window passed unfilled")
    assert view.status == "EXPIRED"


# --------------------------------------------------------------------------------------
# Validation: min notional + step/size
# --------------------------------------------------------------------------------------

def test_below_min_notional_rejected() -> None:
    mgr = OrderManager(settings())
    view = mgr.submit(market_buy(client_order_id="mn-1", quantity=0.00001, price=100.0))
    assert view.status == "REJECTED"
    assert "MIN_NOTIONAL" in view.reason or "minimum" in view.reason


def test_non_step_quantity_rejected() -> None:
    with pytest.raises(OrderValidationError):
        validate_order(side="BUY", order_type="MARKET", quantity=0.100000007,
                       price=100.0, settings=settings())


def test_validate_order_ok() -> None:
    v = validate_order(side="BUY", order_type="MARKET", quantity=0.1,
                       price=100.0, settings=settings())
    assert v.notional == pytest.approx(10.0)  # qty * price
    assert v.quantity == pytest.approx(0.1)


def test_validate_rejects_hold_side() -> None:
    with pytest.raises(OrderValidationError):
        validate_order(side="HOLD", order_type="MARKET", quantity=0.1,
                       price=100.0, settings=settings())


# --------------------------------------------------------------------------------------
# DB audit of order events
# --------------------------------------------------------------------------------------

def test_order_events_recorded_in_db(tmp_path) -> None:
    from halaltrade.db import create_session, make_engine
    from halaltrade.db.models import Order as OrderRow

    engine = make_engine(f"sqlite:///{tmp_path / 'orders_test.db'}")
    sf = create_session(engine)
    session = sf()
    try:
        mgr = OrderManager(settings(), session=session)
        mgr.submit(market_buy(client_order_id="db-1", quantity=0.1, price=100.0))
        mgr.apply_fill("db-1", 0.04, 100.0)
        mgr.apply_fill("db-1", 0.06, 100.0)
        # One row per clientOrderId, always the latest CONFIRMED state.
        rows = session.query(OrderRow).filter_by(client_order_id="db-1").all()
        assert len(rows) == 1
        assert rows[0].lifecycle == "FILLED"
        assert rows[0].executed_qty == pytest.approx(0.1)
    finally:
        session.close()
