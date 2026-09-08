"""Shared simulation primitives for the paper and backtest engines.

Both engines simulate the same thing — a Shariah-compliant, Spot-only,
BTC/USDT, 1x-leverage cash account — so the fill, fee, slippage and P&L math
lives here once and is reused. Nothing in this module touches the network or
invents data: every value derives from the caller-provided price and the
configured fee/slippage parameters.
"""
from __future__ import annotations

from .account import Fill, FillError, PositionError, ShortError, SimAccount
from .fees import FeeConfig

__all__ = [
    "FeeConfig",
    "SimAccount",
    "Fill",
    "FillError",
    "PositionError",
    "ShortError",
]
