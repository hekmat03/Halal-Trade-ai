"""Delivery 4 — FastAPI backend service exposing the HalalTrade engine.

The API is PAPER-only. Live trading stays DISABLED. Every endpoint wires to the
existing engine modules (pipeline, gates, paper broker, backtest engine,
marketdata, DB recorder) — nothing here re-implements policy. Endpoints:
    GET  /health      — system health (mode, live flag, data freshness)
    GET  /status      — operational state (paper equity, positions, kill switch)
    POST /trade       — submit a SIGNAL for full gate evaluation + paper fill
    POST /kill        — set/unset the emergency kill switch (persisted)
    GET  /audit       — recent audit log entries (from the DB)
    GET  /backtest    — run the existing backtest engine (simulation only)
"""
from __future__ import annotations

from .main import app, create_app

__all__ = ["app", "create_app"]
