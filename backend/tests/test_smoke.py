"""Smoke + database schema tests.

Smoke: prove the pipeline imports and runs end-to-end.
DB: prove the eight required tables are created and portable.
"""
from __future__ import annotations

import halaltrade  # smoke import of the package
from halaltrade.config import Settings
from halaltrade.db import make_engine, create_all, Base
from halaltrade.gates.base import Context, RiskAccount
from halaltrade.models import InstrumentType, PipelineDecision, Side
from halaltrade.pipeline import Pipeline

from conftest import make_signal


def test_smoke_pipeline_import_and_run() -> None:
    context = Context(settings=Settings(), order_status_confirmed=True)
    context.account = RiskAccount(balance=1000.0)
    pipeline = Pipeline(context=context)
    signal = make_signal(
        side=Side.BUY,
        instrument_type=InstrumentType.SPOT,
        quantity=0.01,
        price=50000.0,
        amount=None,
        stop_loss=49000.0,
    )
    result = pipeline.evaluate(signal)
    # A decision is always returned — never None, never fabricated.
    assert result.decision in (PipelineDecision.TRADE, PipelineDecision.NO_TRADE)
    assert len(result.gates) >= 1


def test_db_creates_all_required_tables() -> None:
    engine = make_engine("sqlite:///:memory:")
    create_all(engine)
    from sqlalchemy import inspect

    tables = set(inspect(engine).get_table_names())
    required = {
        "audit_log",
        "signals",
        "shariah_checks",
        "risk_events",
        "trades",
        "orders",
        "emergency_events",
        "system_events",
    }
    assert required.issubset(tables), f"missing tables: {required - tables}"


def test_db_engine_default_is_sqlite() -> None:
    assert Settings(_env_file=None).database_url.startswith("sqlite:///")
