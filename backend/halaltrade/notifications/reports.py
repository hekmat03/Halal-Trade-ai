"""Daily / weekly paper-trading performance reports (Delivery 8).

Two halves, both deliberately boring and testable:

* :class:`PerformanceTracker` — a pure in-memory accumulator. The paper run
  feeds it the equity after every fill/poll and every confirmed simulated fill.
  It computes period P&L, trade counts, win rate, profit factor and max
  drawdown. No network, no DB, no clock of its own.
* ``format_*`` functions — pure string builders mirroring
  :mod:`halaltrade.notifications.telegram` (pure, no network, unit-testable).

Honesty rules (matching the rest of the repo):
* Every number comes from the simulation. Nothing is estimated or invented.
* A period with no closed trades reports a profit factor of ``0.0`` and a win
  rate of ``0.0`` — never a flattering placeholder.
* Reports are **simulated paper results**; they are not a profit guarantee and
  they never imply live trading is enabled.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from ..models import utcnow

__all__ = [
    "FillRecord",
    "PaperStats",
    "PerformanceTracker",
    "compute_stats",
    "format_report",
    "format_daily_report",
    "format_weekly_report",
    "finite_profit_factor",
    "max_drawdown_pct",
]

#: Profit factor is unbounded above when there are no losing trades. Evidence
#: records and report lines need a finite, JSON-safe number, so it is capped
#: here (clearly marked in the output as "capped" when it happens).
PROFIT_FACTOR_CAP = 1_000_000.0


def finite_profit_factor(gross_profit: float, gross_loss: float) -> float:
    """``gross_profit / gross_loss``, capped and never NaN.

    ``0.0`` when nothing has closed; the cap when there are no losses at all.
    """
    if gross_loss > 0:
        return gross_profit / gross_loss
    if gross_profit > 0:
        return PROFIT_FACTOR_CAP
    return 0.0


def max_drawdown_pct(equity_curve: Iterable[float]) -> float:
    """Peak-to-trough drawdown of an equity curve, as a positive percentage."""
    peak: Optional[float] = None
    worst = 0.0
    for equity in equity_curve:
        if equity is None:
            continue
        if peak is None or equity > peak:
            peak = equity
            continue
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            if dd > worst:
                worst = dd
    return worst


@dataclass(frozen=True)
class FillRecord:
    """One confirmed *simulated* fill, plus the realized P&L it produced."""

    timestamp: datetime
    side: str
    quantity: float
    price: float
    notional: float
    fee: float
    realized_pnl: float = 0.0  # realized delta attributable to this fill


@dataclass(frozen=True)
class PaperStats:
    """Every number a paper report shows. Computed, never estimated."""

    label: str
    period_end: datetime
    starting_equity: float
    ending_equity: float
    trades: int = 0
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    profit_factor: float = 0.0
    period_max_drawdown_pct: float = 0.0
    open_position_qty: float = 0.0
    period_start: Optional[datetime] = None
    symbol: str = "BTCUSDT"
    stage: str = ""
    paper_days: float = 0.0
    data_source: str = ""

    @property
    def pnl(self) -> float:
        """Period P&L in quote currency (equity change over the period)."""
        return self.ending_equity - self.starting_equity

    @property
    def pnl_pct(self) -> float:
        if self.starting_equity <= 0:
            return 0.0
        return self.pnl / self.starting_equity * 100.0

    @property
    def win_rate_pct(self) -> float:
        closed = self.wins + self.losses
        if closed <= 0:
            return 0.0
        return self.wins / closed * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat(),
            "starting_equity": self.starting_equity,
            "ending_equity": self.ending_equity,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "trades": self.trades,
            "closed_trades": self.closed_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate_pct": self.win_rate_pct,
            "realized_pnl": self.realized_pnl,
            "fees_paid": self.fees_paid,
            "profit_factor": self.profit_factor,
            "period_max_drawdown_pct": self.period_max_drawdown_pct,
            "open_position_qty": self.open_position_qty,
            "symbol": self.symbol,
            "stage": self.stage,
            "paper_days": self.paper_days,
            "data_source": self.data_source,
        }


def _window_start(ts: datetime, period_start: Optional[datetime]) -> bool:
    if period_start is None:
        return True
    return ts >= period_start


def compute_stats(
    *,
    label: str,
    fills: Iterable[FillRecord],
    equity_curve: Iterable[tuple[datetime, float]] = (),
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
    fallback_starting_equity: float = 0.0,
    ending_equity: Optional[float] = None,
    open_position_qty: float = 0.0,
    symbol: str = "BTCUSDT",
    stage: str = "",
    paper_days: float = 0.0,
    data_source: str = "",
) -> PaperStats:
    """Aggregate one reporting period from fills + an equity curve.

    ``starting_equity`` is the first observed equity at/after ``period_start``
    (falling back to ``fallback_starting_equity`` when the period has no
    observations yet); ``ending_equity`` is the last observation, or the caller
    supplied value.
    """
    period_end = period_end or utcnow()
    period_fills = [f for f in fills if _window_start(f.timestamp, period_start)]

    curve = [(ts, eq) for (ts, eq) in equity_curve if _window_start(ts, period_start)]
    if curve:
        starting = curve[0][1]
        ending = curve[-1][1] if ending_equity is None else ending_equity
        values = [eq for _, eq in curve]
        if ending_equity is not None:
            values = values + [ending_equity]
    else:
        starting = fallback_starting_equity
        ending = fallback_starting_equity if ending_equity is None else ending_equity
        values = [ending]

    realized = [f.realized_pnl for f in period_fills if abs(f.realized_pnl) > 1e-12]
    wins = sum(1 for pnl in realized if pnl > 0)
    losses = sum(1 for pnl in realized if pnl < 0)
    gross_profit = sum(pnl for pnl in realized if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in realized if pnl < 0))

    return PaperStats(
        label=label,
        period_start=period_start,
        period_end=period_end,
        starting_equity=starting,
        ending_equity=ending,
        trades=len(period_fills),
        closed_trades=len(realized),
        wins=wins,
        losses=losses,
        realized_pnl=sum(realized),
        fees_paid=sum(f.fee for f in period_fills),
        profit_factor=finite_profit_factor(gross_profit, gross_loss),
        period_max_drawdown_pct=max_drawdown_pct(values),
        open_position_qty=open_position_qty,
        symbol=symbol,
        stage=stage,
        paper_days=paper_days,
        data_source=data_source,
    )


class PerformanceTracker:
    """Accumulates simulated fills + an equity curve for report periods.

    The paper run calls :meth:`record_equity` on every poll and
    :meth:`record_fill` on every confirmed simulated fill. Nothing here talks to
    the network or the database.
    """

    def __init__(
        self,
        starting_equity: float,
        *,
        symbol: str = "BTCUSDT",
        max_points: int = 20_000,
        max_fills: int = 20_000,
    ) -> None:
        self.starting_equity = float(starting_equity)
        self.symbol = symbol
        self._equity: deque[tuple[datetime, float]] = deque(maxlen=max_points)
        self._fills: deque[FillRecord] = deque(maxlen=max_fills)
        self._realized_total = 0.0

    # -- recording -----------------------------------------------------------
    def record_equity(self, equity: float, ts: Optional[datetime] = None) -> None:
        self._equity.append((ts or utcnow(), float(equity)))

    def record_fill(
        self,
        fill: Any,
        *,
        realized_pnl_total: float,
        ts: Optional[datetime] = None,
    ) -> FillRecord:
        """Record a confirmed simulated fill (a ``simulation.Fill``).

        The realized P&L *delta* is taken from the account's running realized
        total, so a BUY records ``0.0`` and a closing SELL records only what it
        actually realized.
        """
        delta = float(realized_pnl_total) - self._realized_total
        self._realized_total = float(realized_pnl_total)
        record = FillRecord(
            timestamp=ts or getattr(fill, "timestamp", None) or utcnow(),
            side=str(getattr(fill, "side", "")),
            quantity=float(getattr(fill, "quantity", 0.0)),
            price=float(getattr(fill, "price", 0.0)),
            notional=float(getattr(fill, "notional", 0.0)),
            fee=float(getattr(fill, "fee", 0.0)),
            realized_pnl=delta,
        )
        self._fills.append(record)
        return record

    def record_exit(
        self,
        *,
        realized_pnl: float,
        price: float,
        ts: Optional[datetime] = None,
        fee: float = 0.0,
        quantity: float = 0.0,
        reason: str = "",
    ) -> FillRecord:
        """Record a close that was performed by the position monitor.

        Used when the monitor (stop-loss / take-profit) closed the position and
        exposed no ``Fill`` object. The realized delta and price are real; the
        quantity/fee default to the values the monitor did not report rather
        than being invented.
        """
        record = FillRecord(
            timestamp=ts or utcnow(),
            side="SELL",
            quantity=float(quantity),
            price=float(price),
            notional=float(quantity) * float(price),
            fee=float(fee),
            realized_pnl=float(realized_pnl),
        )
        self._fills.append(record)
        return record

    # -- reads ---------------------------------------------------------------
    @property
    def fills(self) -> list[FillRecord]:
        return list(self._fills)

    @property
    def equity_curve(self) -> list[tuple[datetime, float]]:
        return list(self._equity)

    def last_equity(self, default: Optional[float] = None) -> float:
        if self._equity:
            return self._equity[-1][1]
        return self.starting_equity if default is None else default

    def equity_at(self, ts: Optional[datetime]) -> float:
        """Last observed equity at or before ``ts`` (starting equity otherwise)."""
        if ts is None or not self._equity:
            return self._equity[0][1] if self._equity else self.starting_equity
        equity = self.starting_equity
        for point_ts, value in self._equity:
            if point_ts <= ts:
                equity = value
            else:
                break
        return equity

    def stats(
        self,
        *,
        label: str = "DAILY",
        period_start: Optional[datetime] = None,
        ending_equity: Optional[float] = None,
        open_position_qty: float = 0.0,
        stage: str = "",
        paper_days: float = 0.0,
        data_source: str = "",
    ) -> PaperStats:
        """Compute stats for the period starting at ``period_start``."""
        return compute_stats(
            label=label,
            fills=self._fills,
            equity_curve=self._equity,
            period_start=period_start,
            ending_equity=ending_equity,
            fallback_starting_equity=self.starting_equity,
            open_position_qty=open_position_qty,
            symbol=self.symbol,
            stage=stage,
            paper_days=paper_days,
            data_source=data_source,
        )


# ---------------------------------------------------------------------------
# Pure formatters (no network, no clock) — same style as telegram.py
# ---------------------------------------------------------------------------


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_profit_factor(value: float) -> str:
    if value >= PROFIT_FACTOR_CAP:
        return f">= {PROFIT_FACTOR_CAP:,.0f} (no losing trades yet)"
    return f"{value:.2f}"


def format_report(stats: PaperStats) -> str:
    """Format one daily/weekly paper report. Pure function."""
    icon = "\U0001F4C5" if stats.label.upper().startswith("D") else "\U0001F5D3"
    title = stats.label.title()
    lines = [
        f"{icon} {title} Paper Report — {stats.period_end:%Y-%m-%d %H:%M} UTC",
        f"{stats.symbol} — simulated money (no live order path)",
        f"Equity: {_fmt_money(stats.starting_equity)} -> {_fmt_money(stats.ending_equity)} "
        f"({stats.pnl_pct:+.2f}%)",
        f"Period P&L: {stats.pnl:+,.2f} USDT",
        f"Trades: {stats.trades} (closed: {stats.closed_trades}; "
        f"wins: {stats.wins}, losses: {stats.losses}, win rate: {stats.win_rate_pct:.1f}%)",
        f"Realized P&L: {stats.realized_pnl:+,.2f} USDT",
        f"Profit factor: {_fmt_profit_factor(stats.profit_factor)}",
        f"Max drawdown (period): -{stats.period_max_drawdown_pct:.2f}%",
        f"Fees paid: {_fmt_money(stats.fees_paid)}",
        f"Open position: {stats.open_position_qty:.8f} BTC",
    ]
    if stats.stage:
        stage_line = f"Stage: {stats.stage}"
        if stats.paper_days > 0:
            stage_line += f" — paper day {stats.paper_days:.1f} of 30"
        lines.append(stage_line)
    if stats.data_source:
        lines.append(f"Market data: {stats.data_source} (freshness checked per bar)")
    lines.append("Simulated paper results — not a profit guarantee.")
    return "\n".join(lines)


def format_daily_report(stats: PaperStats) -> str:
    """Format the daily paper report (label forced to DAILY). Pure function."""
    return format_report(replace(stats, label="DAILY"))


def format_weekly_report(stats: PaperStats) -> str:
    """Format the weekly paper report (label forced to WEEKLY). Pure function."""
    return format_report(replace(stats, label="WEEKLY"))
