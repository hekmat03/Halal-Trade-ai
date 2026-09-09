"""Position management package (Delivery 5) — paper-first.

``halaltrade.positions`` tracks the open spot position from confirmed fills:

* Quantity (BTC), average entry, realized + unrealized P&L.
* Mandatory stop-loss monitoring — ``on_price`` triggers a flatten when the
  stop is hit (paper execution through the simulated account).
* Optional take-profit and optional trailing stop (ratchets up under a long).
* Dust handling: a residual below the tradeable step is flattened in one final
  close instead of emitting repeated failed orders.

The tracker is long-only, 1x, spot — it can never represent a short position.
It feeds live state into the Risk gate via ``RiskAccount`` (exposure counts the
open position; ``open_position_count`` enforces the single-position cap;
``base_holdings`` enforces the never-short SELL check).
"""
from __future__ import annotations

from .tracker import PositionTracker, PositionView, TrailingConfig

__all__ = ["PositionTracker", "PositionView", "TrailingConfig"]
