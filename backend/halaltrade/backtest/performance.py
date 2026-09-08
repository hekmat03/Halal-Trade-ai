"""Performance report for a backtest run.

Every metric is computed solely from simulated data (the account's fills and the
mark-to-market equity curve). Nothing is invented: if there are no closed trades
win rate / profit factor are reported as 0 (DATA UNAVAILABLE-style), never
fabricated.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

__all__ = ["PerformanceReport", "ClosedTrade"]


class ClosedTrade(BaseModel):
    """One completed round-trip (BUY then SELL) in the simulation."""

    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    entry_price: float
    exit_price: float
    quantity: float
    fees: float
    pnl: float
    exit_reason: str = "SIGNAL"   # SIGNAL | STOP_LOSS | TAKE_PROFIT | FLAT_CLOSE


class PerformanceReport(BaseModel):
    """Aggregate metrics computed from a completed run."""

    starting_equity: float
    ending_equity: float
    total_return: float                 # (end - start) / start
    pnl: float                          # total $ P&L
    trade_count: int                    # completed round-trips
    win_rate: float                     # wins / closed trades (0 if none)
    profit_factor: float                # gross_profit / |gross_loss|
    max_drawdown: float                 # peak-to-trough on equity curve (fraction)
    max_drawdown_usd: float
    sharpe: float                       # approx, annualized from per-candle returns
    gross_profit: float
    gross_loss: float
    wins: int
    losses: int
    fees_paid: float
    closed_trades: list[ClosedTrade] = Field(default_factory=list)
    equity_curve: list[float] = Field(default_factory=list)


def _compute_drawdown(curve: list[float]) -> tuple[float, float]:
    peak = -math.inf
    max_dd = 0.0
    max_dd_usd = 0.0
    for eq in curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
                max_dd_usd = peak - eq
    return max_dd, max_dd_usd


def _compute_sharpe(curve: list[float], periods_per_year: float = 945) -> float:
    """Approximate annualized Sharpe from per-period log returns.

    Default ``periods_per_year`` = 945 (365 days x ~2 for the 12h-style candles
    we primarily model; callers can override). A flat curve yields 0.0.
    """
    if len(curve) < 2:
        return 0.0
    returns: list[float] = []
    prev = curve[0]
    for eq in curve[1:]:
        if prev > 0:
            returns.append(math.log(eq / prev))
        prev = eq
    if len(returns) < 2:
        return 0.0
    std = statistics.pstdev(returns)
    if std == 0:
        return 0.0
    mean = statistics.fmean(returns)
    return (mean / std) * math.sqrt(periods_per_year)


def build_report(
    *,
    starting_equity: float,
    ending_equity: float,
    closed_trades: list[ClosedTrade],
    equity_curve: list[float],
    fees_paid: float,
    periods_per_year: float = 945,
) -> PerformanceReport:
    wins = [t for t in closed_trades if t.pnl > 0]
    losses = [t for t in closed_trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = sum(t.pnl for t in losses)  # negative or 0
    trade_count = len(closed_trades)
    win_rate = (len(wins) / trade_count) if trade_count else 0.0
    if gross_loss < 0:
        profit_factor = gross_profit / abs(gross_loss)
    else:
        profit_factor = gross_profit if gross_profit > 0 else 0.0
    max_dd, max_dd_usd = _compute_drawdown(equity_curve)
    sharpe = _compute_sharpe(equity_curve, periods_per_year=periods_per_year)
    return PerformanceReport(
        starting_equity=starting_equity,
        ending_equity=ending_equity,
        total_return=(ending_equity - starting_equity) / starting_equity if starting_equity else 0.0,
        pnl=ending_equity - starting_equity,
        trade_count=trade_count,
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown=max_dd,
        max_drawdown_usd=max_dd_usd,
        sharpe=sharpe,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        wins=len(wins),
        losses=len(losses),
        fees_paid=fees_paid,
        closed_trades=closed_trades,
        equity_curve=equity_curve,
    )
