"""Tests for the FastAPI backend service (Delivery 4).

Fully offline: an injected fake market-data source and a temp SQLite DB mean no
network and no real money. Verifies the HTTP contract — health/status/audit/
backtest, and especially /trade (gate pass/reject) and /kill (halt-until-unset).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from halaltrade.api import create_app
from halaltrade.config import Settings
from halaltrade.marketdata import DataUnavailableError, MarketDataSource, Ticker
from halaltrade.db import make_engine, create_session


def make_settings() -> Settings:
    return Settings(
        trading_mode="paper",
        live_enabled=False,
        api_key_restricted=True,
        system_healthy=True,
        max_position_size=10000.0,
        max_exposure=20000.0,
        max_loss_per_trade=500.0,
        max_daily_loss=1000.0,
        max_drawdown=0.10,
        min_account_balance=100.0,
        min_notional=5.0,
        max_data_age_seconds=5.0,
    )


class FakeSource(MarketDataSource):
    """Deterministic offline price source."""

    def __init__(self, price=100.0, *, stale=False, fail=False) -> None:
        from halaltrade.models import utcnow

        super().__init__(now=utcnow)
        self.price = price
        self._stale = stale
        self._fail = fail

    async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker:
        if self._fail:
            raise DataUnavailableError("down", source="fake")
        from halaltrade.models import utcnow

        ts = utcnow()
        if self._stale:
            ts = ts - timedelta(seconds=60)
        return Ticker(symbol=symbol, price=self.price, timestamp=ts, source="fake")

    async def get_candles(self, symbol="BTCUSDT", timeframe="1m", limit=100):
        return []


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'api_test.db'}")
    sf = create_session(engine)
    return sf


def make_client(db, *, price=100.0, stale=False, fail=False, settings=None):
    app = create_app(
        settings=settings or make_settings(),
        data_source=FakeSource(price=price, stale=stale, fail=fail),
        session_factory=db,
    )
    return TestClient(app)


def buy_payload(**overrides):
    payload = {
        "side": "BUY",
        "amount": 300.0,
        "stop_loss": 90.0,
        "instrument_type": "SPOT",
        "leverage": 1.0,
        "symbol": "BTCUSDT",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------- health/status


def test_health_reports_paper_mode_and_live_disabled(db):
    with make_client(db) as c:
        r = c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["trading_mode"] == "paper"
        assert body["live_enabled"] is False
        assert body["status"] == "ok"
        assert body["data_source"] == "fake"
        assert body["last_price"] == 100.0
        assert body["data_fresh"] is True
        assert body["kill_switch"] is False


def test_health_reports_data_unavailable_when_source_down(db):
    with make_client(db, fail=True) as c:
        body = c.get("/health").json()
        assert body["data_source"] == "DATA UNAVAILABLE"
        assert body["last_price"] is None


def test_status_shows_paper_balance_and_no_positions(db):
    with make_client(db) as c:
        body = c.get("/status").json()
        assert body["operating_mode"] == "PAPER"
        assert body["live_enabled"] is False
        assert body["paper"]["starting_balance"] == 5000.0
        assert body["paper"]["usdt_balance"] == 5000.0
        assert body["positions"] == []


# ----------------------------------------------------------------------------------- trade


def test_trade_executes_on_paper_broker_when_gates_pass(db):
    with make_client(db) as c:
        r = c.post("/trade", json=buy_payload())
        assert r.status_code == 200
        body = r.json()
        assert body["executed"] is True
        assert body["decision"] == "TRADE"
        assert body["gate_results"]["shariah"]["passed"] is True
        assert body["gate_results"]["risk"]["passed"] is True
        assert body["fill"]["side"] == "BUY"
        assert body["equity_after"] is not None
        # Paper balance should now be reduced by the buy notional.
        status = c.get("/status").json()
        assert status["paper"]["usdt_balance"] < 5000.0
        assert len(status["positions"]) == 1
        assert status["positions"][0]["quantity"] > 0


def test_trade_rejected_for_non_spot_instrument(db):
    with make_client(db) as c:
        body = c.post(
            "/trade", json=buy_payload(instrument_type="FUTURES")
        ).json()
        assert body["executed"] is False
        assert body["decision"] == "NO_TRADE"
        assert body["gate_results"]["shariah"]["passed"] is False


def test_trade_rejected_for_missing_amount(db):
    with make_client(db) as c:
        payload = buy_payload()
        payload.pop("amount")
        r = c.post("/trade", json=payload)
        # Missing exact size is a validation failure -> no trade.
        assert r.status_code == 422


def test_trade_rejected_without_stop_loss(db):
    with make_client(db) as c:
        body = c.post(
            "/trade", json=buy_payload(stop_loss=None)
        ).json()
        assert body["executed"] is False
        assert body["decision"] == "NO_TRADE"
        assert body["gate_results"]["risk"]["passed"] is False


def test_trade_rejected_when_kill_switch_on(db):
    with make_client(db) as c:
        assert c.post("/kill", json={"enabled": True}).status_code == 200
        body = c.post("/trade", json=buy_payload()).json()
        assert body["executed"] is False
        assert body["decision"] == "NO_TRADE"
        assert body["kill_switch_on"] is True
        assert "kill switch" in body["reason"].lower()


def test_kill_stops_trading_until_unset(db):
    with make_client(db) as c:
        # Engage -> refuse.
        c.post("/kill", json={"enabled": True})
        assert c.post("/trade", json=buy_payload()).json()["executed"] is False
        # Release -> allowed again.
        c.post("/kill", json={"enabled": False})
        assert c.post("/trade", json=buy_payload()).json()["executed"] is True


def test_kill_switch_persists_across_restart(db):
    # Engage on one app instance with the same DB.
    with make_client(db) as c:
        c.post("/kill", json={"enabled": True})
    # A fresh app instance reads the persisted kill switch and refuses trades.
    with make_client(db) as c2:
        assert c2.get("/status").json()["kill_switch"] is True
        assert c2.post("/trade", json=buy_payload()).json()["executed"] is False
        c2.post("/kill", json={"enabled": False})
    with make_client(db) as c3:
        assert c3.get("/status").json()["kill_switch"] is False
        assert c3.post("/trade", json=buy_payload()).json()["executed"] is True


def test_trade_guard_against_stale_data(db):
    with make_client(db, stale=True) as c:
        body = c.post("/trade", json=buy_payload()).json()
        assert body["executed"] is False
        assert "stale" in body["reason"].lower() or "unavailable" in body["reason"].lower()


# ----------------------------------------------------------------------------------- audit


def test_audit_records_gate_and_trade_events(db):
    with make_client(db) as c:
        c.post("/trade", json=buy_payload())  # passes -> TRADE_ORDER audit
        c.post("/trade", json=buy_payload(instrument_type="FUTURES"))  # rejected
        rows = c.get("/audit", params={"limit": 50}).json()
        types = {r["event_type"] for r in rows}
        assert "TRADE_ORDER" in types
        assert "GATE" in types
        # A rejected futures signal must appear as a shariah gate failure.
        gate_events = [r for r in rows if r["event_type"] == "GATE"]
        assert any(r["gate"] == "shariah" and r["passed"] is False for r in gate_events)


def test_audit_is_empty_on_fresh_db(db):
    with make_client(db) as c:
        assert c.get("/audit").json() == []


# -------------------------------------------------------------------------------- backtest


def test_backtest_returns_simulation_report(db):
    with make_client(db) as c:
        body = c.get("/backtest").json()
        assert body["simulation"] is True
        assert body["mode"] == "backtest"
        assert "report" in body
        assert body["report"]["starting_equity"] == 5000.0
        assert body["report"]["trade_count"] >= 0
        assert body["candles_used"] == 240
