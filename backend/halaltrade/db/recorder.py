"""Optional audit logging for the pipeline.

The pipeline yields a full ``PipelineResult``; this module can persist it to the
database (``audit_log``, ``signals``, ``shariah_checks``, ``risk_events``) so the
"logs remember" principle is honored even in the library-only MVP. Wired into
FastAPI in a later delivery.
"""
from __future__ import annotations

import json

from datetime import datetime

from sqlalchemy.orm import Session

from ..models import PipelineResult, Signal


def _json_default(o):
    """JSON-serialize datetimes (used by the backtest report detail column)."""
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


from .models import (
    AuditLog,
    BacktestRun,
    Order,
    Position,
    RiskEvent,
    ShariahCheck,
    SignalRecord,
    Trade,
    utc_str,
)


def _now() -> str:
    return utc_str()


def record_order_event(
    session: Session,
    *,
    signal_id: str,
    client_order_id: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float | None,
    price: float | None,
    status: str,
    lifecycle: str,
    executed_qty: float = 0.0,
    cummulative_quote_qty: float = 0.0,
    time_in_force: str | None = None,
    post_only: bool | None = None,
    limit_price: float | None = None,
) -> None:
    """Persist one order lifecycle event (create/update/cancel/fill) for audit.

    Rows are keyed by ``client_order_id`` (unique): the first event inserts the
    row, later events update the lifecycle + executed totals in place — so the
    ``orders`` table always reflects the latest CONFIRMED state of each order.
    """
    row = session.query(Order).filter_by(client_order_id=client_order_id).first()
    if row is None:
        session.add(
            Order(
                timestamp=_now(),
                signal_id=signal_id,
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=status,
                lifecycle=lifecycle,
                executed_qty=executed_qty,
                cummulative_quote_qty=cummulative_quote_qty,
                time_in_force=time_in_force,
                post_only=post_only,
                limit_price=limit_price,
            )
        )
    else:
        row.timestamp = _now()
        row.quantity = quantity
        row.price = price
        row.status = status
        row.lifecycle = lifecycle
        row.executed_qty = executed_qty
        row.cummulative_quote_qty = cummulative_quote_qty
        if time_in_force is not None:
            row.time_in_force = time_in_force
        if post_only is not None:
            row.post_only = post_only
        if limit_price is not None:
            row.limit_price = limit_price
    session.commit()


def record_paper_trade(
    session: Session,
    signal: Signal,
    *,
    fill_price: float,
    quantity: float,
    notional: float,
    fee: float,
) -> None:
    """Persist one confirmed paper/backtest fill (order + trade records).

    The order lifecycle status is always ``"CONFIRMED"`` — a simulated fill is
    only written after the engine has gone through the explicit fill step; it is
    never assumed up-front.
    """
    session.add(
        Order(
            timestamp=_now(),
            signal_id=signal.client_order_id or "n/a",
            client_order_id=signal.client_order_id or "",
            symbol=signal.symbol,
            side=signal.side.value,
            order_type="MARKET",
            quantity=quantity,
            price=fill_price,
            status="CONFIRMED",
        )
    )
    session.add(
        Trade(
            timestamp=_now(),
            signal_id=signal.client_order_id or "n/a",
            client_order_id=signal.client_order_id,
            symbol=signal.symbol,
            side=signal.side.value,
            quantity=quantity,
            price=fill_price,
            notional=notional,
            status="CONFIRMED",
        )
    )
    session.commit()


def record_position(
    session: Session,
    *,
    mode: str,
    symbol: str,
    quantity: float,
    avg_entry_price: float,
    stop_loss: float | None,
    take_profit: float | None,
    status: str,
    run_id: str | None = None,
    client_order_id: str | None = None,
    realized_pnl: float | None = None,
    close_price: float | None = None,
    close_reason: str | None = None,
) -> None:
    session.add(
        Position(
            timestamp=_now(),
            symbol=symbol,
            mode=mode,
            run_id=run_id,
            client_order_id=client_order_id,
            quantity=quantity,
            avg_entry_price=avg_entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status=status,
            realized_pnl=realized_pnl,
            close_price=close_price,
            close_reason=close_reason,
        )
    )
    session.commit()


def record_backtest_run(
    session: Session,
    report,
    *,
    run_id: str,
    strategy: str,
    symbol: str,
    timeframe: str,
    candles_count: int,
) -> None:
    """Persist a completed backtest run and its Performance report."""
    import json

    session.add(
        BacktestRun(
            run_id=run_id,
            timestamp=_now(),
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            candles_count=candles_count,
            starting_equity=report.starting_equity,
            ending_equity=report.ending_equity,
            total_return=report.total_return,
            trade_count=report.trade_count,
            win_rate=report.win_rate,
            profit_factor=report.profit_factor,
            max_drawdown=report.max_drawdown,
            sharpe=report.sharpe,
            pnl=report.pnl,
            detail=json.dumps(report.model_dump(), default=_json_default),
        )
    )
    session.commit()


def record_pipeline(session: Session, signal: Signal, result: PipelineResult) -> None:
    """Persist one pipeline evaluation. Idempotent per signal_id."""
    if session.query(SignalRecord).filter_by(signal_id=result.signal_id).first():
        return

    session.add(
        SignalRecord(
            signal_id=result.signal_id,
            side=signal.side.value,
            symbol=signal.symbol,
            instrument_type=signal.instrument_type.value,
            price=signal.price,
            quantity=signal.quantity,
            amount=signal.amount,
            confidence=signal.confidence,
            reason=signal.reason,
            stop_loss=signal.stop_loss,
            decision=result.decision.value,
        )
    )

    for name, gate_result in result.gates.items():
        # Every rejection is recorded, per policy.
        session.add(
            AuditLog(
                timestamp=utc_str(),
                event_type="GATE",
                signal_id=result.signal_id,
                gate=name,
                passed=gate_result.passed,
                detail=json.dumps(gate_result.reasons),
            )
        )
        if name == "shariah":
            session.add(
                ShariahCheck(
                    signal_id=result.signal_id,
                    passed=gate_result.passed,
                    instrument_type=signal.instrument_type.value,
                    leverage=signal.leverage,
                    reasons=json.dumps(gate_result.reasons),
                )
            )
        elif name == "risk":
            session.add(
                RiskEvent(
                    signal_id=result.signal_id,
                    passed=gate_result.passed,
                    reasons=json.dumps(gate_result.reasons),
                )
            )

    session.commit()