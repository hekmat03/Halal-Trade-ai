"""Backtest engine (Delivery 3) — forward OHLCV simulation with policy gates."""
from __future__ import annotations

from .base import Strategy
from .engine import BacktestConfig, BacktestEngine, BacktestResult
from .performance import ClosedTrade, PerformanceReport, build_report
from .strategies import HoldStrategy, make_hold, make_sma_cross

__all__ = [
    "Strategy",
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "PerformanceReport",
    "ClosedTrade",
    "build_report",
    "HoldStrategy",
    "make_hold",
    "make_sma_cross",
]
