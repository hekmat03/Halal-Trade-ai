"""Paper trading engine — the default operating mode (Delivery 3).

A :class:`PaperBroker` simulates Spot BUY/SELL fills against *live* market data
from the existing marketdata sources. It never touches real money, never places
a live order, and never assumes a fill: every paper order is routed through the
four-gate pipeline and then an explicit fill/confirmation step before the
simulated account changes.

Hard constraints enforced here (mirroring the production rules):
* No leverage, no margin, no futures — Spot, 1x only (Shariah gate).
* No shorting — a SELL can only dispose of an owned position.
* Mandatory stop-loss on every open position.
* Stale-data protection — no trade on stale/unavailable data.
* The four-gate pipeline MUST pass before any paper fill.
* The user supplies the exact USDT amount; if none is given, no trade.
* Idempotent ``clientOrderId`` — a duplicate cannot create a second fill.

If any gate fails the result is ``executed=False`` (NO TRADE). Nothing is ever
fabricated: equity/P&L/fills come only from the simulation.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..config import Settings
from ..gates.base import Context, RiskAccount
from ..marketdata import DataUnavailableError, MarketDataSource, Ticker, is_stale
from ..models import PipelineResult, Side, Signal, utcnow
from ..pipeline import Pipeline
from ..simulation import FeeConfig, Fill, SimAccount
from ..simulation.account import FillError, PositionError, ShortError

logger = logging.getLogger(__name__)

__all__ = ["PaperBroker", "PaperResult"]


@dataclass
class PaperResult:
    """Outcome of a paper order attempt. ``executed`` is the single source of truth."""

    executed: bool
    reason: str
    signal: Signal
    pipeline: Optional[PipelineResult] = None
    fill: Optional[Fill] = None
    equity_after: Optional[float] = None
    realized_pnl: Optional[float] = None

    @property
    def order_id(self) -> str:
        return self.signal.client_order_id or ""


class PaperBroker:
    """Simulated Spot broker that enforces the full gate pipeline + fill rules."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        data_source: MarketDataSource,
        starting_usdt: float = 5000.0,
        fee_config: FeeConfig | None = None,
        session=None,
        pipeline: Optional[Pipeline] = None,
        now=None,
        notifier=None,
    ) -> None:
        self.settings = settings or Settings(trading_mode="paper", live_enabled=False)
        self.data_source = data_source
        self.fee_config = fee_config or FeeConfig()
        self.account = SimAccount(starting_usdt=starting_usdt, fee_config=self.fee_config)
        self.session = session
        self._idempotency: set[str] = set()
        self._now = now or utcnow
        self._pipeline = pipeline

        from ..orders import OrderManager
        self.orders = OrderManager(self.settings, session=None, now=self._now)

        from ..positions import PositionTracker
        self.positions = PositionTracker(
            self.account,
            self.settings,
            session=None,
            now=self._now,
        )

        self.notifier = notifier

    @property
    def usdt_balance(self) -> float:
        return self.account.usdt

    @property
    def btc_holdings(self) -> float:
        return self.account.btc

    def equity(self, price: float) -> float:
        return self.account.equity(price)

    def _build_context(self, signal: Signal, price: float) -> Context:
        return Context(
            settings=self.settings,
            account=RiskAccount(
                balance=self.account.usdt,
                current_position_value=self.account.btc * price,
                daily_pnl=self.account.realized_pnl,
                realized_pnl_today=self.account.realized_pnl,
                open_position_count=1 if self.account.has_position else 0,
                base_holdings=self.account.btc,
            ),
            idempotency_registry=self._idempotency,
            order_status_confirmed=True,
            now=self._now,
        )

    async def execute(self, signal: Signal) -> PaperResult:
        """Run a paper order: gates -> fresh data -> explicit fill."""

        if signal.amount is None or signal.amount <= 0:
            return PaperResult(
                executed=False,
                reason="NO TRADE: exact user USDT amount is required (manual size control).",
                signal=signal,
            )

        ticker = await self._fresh_ticker(signal.symbol)

        if ticker is None:
            return PaperResult(
                executed=False,
                reason="NO TRADE: market data unavailable or stale — no fill.",
                signal=signal,
            )

        eval_signal = signal.model_copy(
            update={
                "price": ticker.price,
                "data_timestamp": ticker.timestamp,
            }
        )

        pipeline = self._pipeline or Pipeline(
            self.settings,
            self._build_context(eval_signal, ticker.price),
        )

        result = pipeline.evaluate(eval_signal)

        if result.decision.value != "TRADE":
            await self._notify_rejection(result)

            return PaperResult(
                executed=False,
                reason="; ".join(result.reasons)
                or "NO TRADE: a gate rejected the order.",
                signal=eval_signal,
                pipeline=result,
            )

        from ..orders import OrderRequest
        from ..orders.manager import OrderStatus

        order_view = self.orders.submit(
            OrderRequest(
                side=eval_signal.side.value,
                symbol=eval_signal.symbol,
                order_type="MARKET",
                amount=eval_signal.amount,
                price=ticker.price,
                signal_id=eval_signal.client_order_id or "n/a",
                client_order_id=eval_signal.client_order_id,
            )
        )

        if order_view.status == OrderStatus.REJECTED.value:
            return PaperResult(
                executed=False,
                reason=f"NO TRADE: order validation refused — {order_view.reason}",
                signal=eval_signal,
                pipeline=result,
            )

        try:
            fill = self._simulate_fill(eval_signal, ticker)

        except (FillError, PositionError, ShortError) as exc:
            return PaperResult(
                executed=False,
                reason=f"NO TRADE: fill refused by simulation — {exc}",
                signal=eval_signal,
                pipeline=result,
            )

        self.orders.apply_fill(
            order_view.client_order_id,
            fill.quantity,
            fill.price,
            is_final=True,
            reason="FILLED: simulated fill confirmed by paper broker.",
        )

        confirmed = await self._confirm(
            signal=eval_signal,
            ticker=ticker,
            fill=fill,
            pipeline=result,
        )

        await self._notify_trade(eval_signal, fill)

        return confirmed

    async def _notify_rejection(self, result: PipelineResult) -> None:
        """Best-effort Telegram alert on a gate rejection. Never raises."""

        if self.notifier is None:
            return

        from ..notifications.telegram import AlertType, format_rejection_alert

        for gate_name in (
            "shariah",
            "prayer_time",
            "risk",
            "security",
            "execution",
        ):
            gate_result = result.gates.get(gate_name)

            if gate_result is not None and not gate_result.passed:
                is_shariah = gate_name in ("shariah", "prayer_time")

                alert_type = (
                    AlertType.SHARIAH_REJECTION
                    if is_shariah
                    else AlertType.RISK_REJECTION
                )

                text = format_rejection_alert(
                    gate_name,
                    gate_result.reasons,
                    is_shariah=is_shariah,
                )

                try:
                    await self.notifier.send(alert_type, text)
                except Exception:
                    logger.warning(
                        "Telegram rejection notification failed",
                        exc_info=True,
                    )

                return

    async def _notify_trade(self, signal: Signal, fill: Fill) -> None:
        """Best-effort Telegram alert on a confirmed fill. Never raises."""

        if self.notifier is None:
            return

        from ..notifications.telegram import AlertType, format_trade_alert

        text = format_trade_alert(
            side=(
                signal.side.value
                if hasattr(signal.side, "value")
                else str(signal.side)
            ),
            symbol=signal.symbol,
            quantity=fill.quantity,
            price=fill.price,
            stop_loss=signal.stop_loss,
            take_profit=signal.proposed_exit,
            reason=signal.reason or "",
        )

        try:
            await self.notifier.send(
                AlertType.TRADE_EXECUTED,
                text,
            )
        except Exception:
            logger.warning(
                "Telegram trade notification failed",
                exc_info=True,
            )

    async def _fresh_ticker(self, symbol: str) -> Optional[Ticker]:
        try:
            ticker = await self.data_source.get_ticker(symbol)

        except DataUnavailableError:
            return None

        if is_stale(
            ticker,
            self.settings.market_data_max_age_seconds,
            now=self._now(),
        ):
            return None

        return ticker

    def _simulate_fill(self, signal: Signal, ticker: Ticker) -> Fill:
        """Apply slippage + fees and move the simulated account."""

        ts = self._now()

        if signal.stop_loss is not None:
            self.account.stop_loss = signal.stop_loss

        if signal.proposed_exit is not None:
            self.account.take_profit = signal.proposed_exit

        if signal.side == Side.BUY:
            fill = self.account.buy(
                signal.amount,
                ticker.price,
                client_order_id=signal.client_order_id,
                timestamp=ts,
            )

            self.positions._arm_trailing(ticker.price)

            return fill

        if signal.side == Side.SELL:
            fill = self.account.sell_amount(
                signal.amount,
                ticker.price,
                client_order_id=signal.client_order_id,
                timestamp=ts,
            )

            self.positions._realized_today = (
                self.account.realized_pnl
                - self.positions._realized_base
            )

            return fill

        raise FillError(
            f"unsupported side for paper fill: {signal.side}"
        )

    async def _confirm(
        self,
        *,
        signal: Signal,
        ticker: Ticker,
        fill: Fill,
        pipeline: PipelineResult,
    ) -> PaperResult:
        """Persist the confirmed fill and report."""

        if self.session is not None:
            from ..db.recorder import (
                record_order_event,
                record_paper_trade,
                record_position,
            )

            record_paper_trade(
                self.session,
                signal,
                fill_price=fill.price,
                quantity=fill.quantity,
                notional=fill.notional,
                fee=fill.fee,
            )

            order_view = self.orders.get(
                signal.client_order_id or ""
            )

            if order_view is not None:
                record_order_event(
                    self.session,
                    signal_id=signal.client_order_id or "n/a",
                    client_order_id=order_view.client_order_id,
                    symbol=signal.symbol,
                    side=order_view.side,
                    order_type=order_view.order_type,
                    quantity=order_view.quantity,
                    price=order_view.price,
                    status="CONFIRMED",
                    lifecycle=order_view.status,
                    executed_qty=order_view.executed_qty,
                    cummulative_quote_qty=order_view.cummulative_quote_qty,
                    time_in_force=order_view.time_in_force,
                    post_only=order_view.post_only,
                )

            if fill.is_buy:
                record_position(
                    self.session,
                    mode="paper",
                    symbol=signal.symbol,
                    quantity=self.account.btc,
                    avg_entry_price=self.account.avg_entry_price,
                    stop_loss=self.account.stop_loss,
                    take_profit=self.account.take_profit,
                    status="OPEN",
                    client_order_id=signal.client_order_id,
                )

        price = ticker.price

        return PaperResult(
            executed=True,
            reason="FILLED (simulated): pipeline passed and fill confirmed.",
            signal=signal,
            pipeline=pipeline,
            fill=fill,
            equity_after=self.account.equity(price),
            realized_pnl=self.account.realized_pnl,
        )

    def close_all_for_stop_loss(
        self,
        price: float,
        *,
        client_order_id: str | None = None,
    ):
        """Close any open position whose stop-loss has been reached."""

        report = self.positions.on_price(price)

        if report.closed and report.reason == "STOP_LOSS":
            logger.info(
                "paper stop-loss fired at %s (client order %s)",
                price,
                client_order_id,
            )
            return True

        return False

    def monitor_price(self, price: float):
        """Run the full position monitor on a new price."""

        return self.positions.on_price(price)
