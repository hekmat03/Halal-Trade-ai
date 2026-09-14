"""Live broker wiring — connects the safety pipeline to Binance Spot.

This module is the ONLY place where the application may request a real-money
order. It enforces:

* LIVE mode must be explicitly enabled.
* BTC/USDT Spot only.
* BUY/SELL only — no leverage, margin, futures, shorting, or borrowing.
* Exact user-supplied USDT amount is required.
* The four-gate pipeline must pass before execution.
* Every order receives a unique clientOrderId for idempotency.
* Binance order status is explicitly confirmed before success is recorded.
* A confirmed fill is written to the audit/DB layer when a session exists.

IMPORTANT: ``BinanceLiveClient`` itself defaults to Binance TESTNET. This broker
does not override that safety default unless the caller explicitly supplies a
live client configured for ``BINANCE_LIVE_URL``.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from ..config import Settings
from ..gates.base import Context, RiskAccount
from ..models import PipelineResult, Side, Signal, utcnow
from ..pipeline import Pipeline
from .binance_client import (
    BinanceLiveClient,
    BinanceOrderError,
    OrderResult,
)

logger = logging.getLogger(__name__)

__all__ = ["LiveBroker", "LiveResult"]


@dataclass
class LiveResult:
    """The complete outcome of one live-order attempt."""

    executed: bool
    reason: str
    signal: Signal
    pipeline: Optional[PipelineResult] = None
    order: Optional[OrderResult] = None

    @property
    def order_id(self) -> str:
        return self.signal.client_order_id or ""


class LiveBroker:
    """Safety-gated Binance Spot broker.

    The broker does NOT create a live client itself. The caller must inject one.
    This makes it impossible for a random code path to silently construct a
    real-money client.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: BinanceLiveClient,
        session=None,
        pipeline: Optional[Pipeline] = None,
        now=None,
        notifier=None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.session = session
        self._pipeline = pipeline
        self._now = now or utcnow
        self.notifier = notifier

        # Local idempotency registry. A persistent DB-backed implementation
        # should be used by the production application if available.
        self._idempotency: set[str] = set()

    def _build_context(
        self,
        signal: Signal,
        price: float,
    ) -> Context:
        return Context(
            settings=self.settings,
            account=RiskAccount(
                balance=0.0,
                current_position_value=0.0,
                daily_pnl=0.0,
                realized_pnl_today=0.0,
                open_position_count=0,
                base_holdings=0.0,
            ),
            idempotency_registry=self._idempotency,
            order_status_confirmed=False,
            now=self._now,
        )

    async def execute(self, signal: Signal) -> LiveResult:
        """Evaluate all safety gates, then place and confirm a live Spot order."""

        # ------------------------------------------------------------------
        # HARD LIVE-MODE SAFETY CHECK
        # ------------------------------------------------------------------
        if self.settings.trading_mode != "live":
            return LiveResult(
                executed=False,
                reason=(
                    "NO TRADE: live broker called while trading_mode "
                    f"is {self.settings.trading_mode!r}."
                ),
                signal=signal,
            )

        if not self.settings.live_enabled:
            return LiveResult(
                executed=False,
                reason=(
                    "NO TRADE: live trading is disabled. "
                    "Explicit live_enabled=True is required."
                ),
                signal=signal,
            )

        # ------------------------------------------------------------------
        # HARD ASSET / SIDE RESTRICTIONS
        # ------------------------------------------------------------------
        if signal.symbol != "BTCUSDT":
            return LiveResult(
                executed=False,
                reason=(
                    "NO TRADE: only BTCUSDT Spot is permitted by the "
                    "live trading specification."
                ),
                signal=signal,
            )

        if signal.side not in (Side.BUY, Side.SELL):
            return LiveResult(
                executed=False,
                reason=(
                    "NO TRADE: only Spot BUY/SELL orders are permitted."
                ),
                signal=signal,
            )

        # ------------------------------------------------------------------
        # EXACT USER AMOUNT — NEVER INVENT POSITION SIZE
        # ------------------------------------------------------------------
        if signal.amount is None or signal.amount <= 0:
            return LiveResult(
                executed=False,
                reason=(
                    "NO TRADE: exact user USDT amount is required. "
                    "The live broker will never invent a position size."
                ),
                signal=signal,
            )

        # ------------------------------------------------------------------
        # CLIENT ORDER ID / IDEMPOTENCY
        # ------------------------------------------------------------------
        client_order_id = (
            signal.client_order_id
            or f"HALAL-{uuid.uuid4().hex[:20]}"
        )

        if client_order_id in self._idempotency:
            return LiveResult(
                executed=False,
                reason=(
                    "NO TRADE: duplicate clientOrderId detected. "
                    "Idempotency protection prevented a second order."
                ),
                signal=signal.model_copy(
                    update={
                        "client_order_id": client_order_id,
                    }
                ),
            )

        eval_signal = signal.model_copy(
            update={
                "client_order_id": client_order_id,
            }
        )

        # Reserve the ID BEFORE making the network request. If the request
        # times out, retry/reconciliation must use the same ID rather than
        # creating a second order.
        self._idempotency.add(client_order_id)

        # ------------------------------------------------------------------
        # MARKET DATA
        # ------------------------------------------------------------------
        try:
            ticker = await self._get_ticker(eval_signal.symbol)
        except Exception as exc:
            return LiveResult(
                executed=False,
                reason=f"NO TRADE: market-data check failed — {exc}",
                signal=eval_signal,
            )

        if ticker is None:
            return LiveResult(
                executed=False,
                reason=(
                    "NO TRADE: current BTCUSDT market data is unavailable "
                    "or stale."
                ),
                signal=eval_signal,
            )

        eval_signal = eval_signal.model_copy(
            update={
                "price": ticker.price,
                "data_timestamp": ticker.timestamp,
            }
        )

        # ------------------------------------------------------------------
        # FOUR-GATE PIPELINE
        # ------------------------------------------------------------------
        pipeline = self._pipeline or Pipeline(
            self.settings,
            self._build_context(
                eval_signal,
                ticker.price,
            ),
        )

        result = pipeline.evaluate(eval_signal)

        if result.decision.value != "TRADE":
            await self._notify_rejection(result)

            return LiveResult(
                executed=False,
                reason=(
                    "; ".join(result.reasons)
                    or "NO TRADE: safety pipeline rejected the signal."
                ),
                signal=eval_signal,
                pipeline=result,
            )

        # ------------------------------------------------------------------
        # LIVE ORDER — THE ONLY REAL-MONEY ACTION IN THIS MODULE
        # ------------------------------------------------------------------
        try:
            order = await self.client.place_and_confirm(
                symbol=eval_signal.symbol,
                side=eval_signal.side.value,
                quantity=eval_signal.amount,
                client_order_id=client_order_id,
            )

        except BinanceOrderError as exc:
            logger.error(
                "Live order failed or could not be confirmed: %s",
                exc,
            )

            return LiveResult(
                executed=False,
                reason=(
                    "NO TRADE / UNKNOWN OUTCOME: Binance order was not "
                    f"confirmed — {exc}"
                ),
                signal=eval_signal,
                pipeline=result,
            )

        # ------------------------------------------------------------------
        # EXPLICIT TERMINAL-STATUS CHECK
        # ------------------------------------------------------------------
        if order.status.value != "FILLED":
            return LiveResult(
                executed=False,
                reason=(
                    "NO TRADE RECORDED: Binance returned terminal status "
                    f"{order.status.value}, not FILLED."
                ),
                signal=eval_signal,
                pipeline=result,
                order=order,
            )

        # ------------------------------------------------------------------
        # PERSIST ONLY AFTER CONFIRMATION
        # ------------------------------------------------------------------
        await self._record_confirmed_order(
            signal=eval_signal,
            order=order,
        )

        await self._notify_trade(
            signal=eval_signal,
            order=order,
        )

        return LiveResult(
            executed=True,
            reason=(
                "FILLED: Binance Spot order was explicitly confirmed "
                "with terminal status FILLED."
            ),
            signal=eval_signal,
            pipeline=result,
            order=order,
        )

    async def _get_ticker(self, symbol: str):
        """Get a fresh ticker using the existing market-data layer."""

        from ..marketdata import DataUnavailableError, is_stale

        try:
            ticker = await self.client_get_ticker(symbol)
        except DataUnavailableError:
            return None

        if is_stale(
            ticker,
            self.settings.market_data_max_age_seconds,
            now=self._now(),
        ):
            return None

        return ticker

    async def client_get_ticker(self, symbol: str):
        """Compatibility hook for the application's market-data provider.

        ``BinanceLiveClient`` is deliberately focused on signed/private
        endpoints. A production application should replace this method with
        the existing public market-data source.
        """

        source = getattr(self, "data_source", None)

        if source is None:
            raise RuntimeError(
                "LiveBroker requires a public market-data source for "
                "fresh-price validation."
            )

        return await source.get_ticker(symbol)

    async def _record_confirmed_order(
        self,
        *,
        signal: Signal,
        order: OrderResult,
    ) -> None:
        """Write the confirmed execution to the audit/DB layer."""

        if self.session is None:
            logger.warning(
                "Confirmed live order %s has no DB session; "
                "execution cannot be persisted locally.",
                order.client_order_id,
            )
            return

        from ..db.recorder import record_order_event

        record_order_event(
            self.session,
            signal_id=signal.client_order_id or "n/a",
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            order_type="MARKET",
            quantity=signal.amount,
            price=(
                order.cummulative_quote_qty / order.executed_qty
                if order.executed_qty > 0
                else signal.price
            ),
            status=order.status.value,
            lifecycle=order.status.value,
            executed_qty=order.executed_qty,
            cummulative_quote_qty=order.cummulative_quote_qty,
            time_in_force=None,
            post_only=False,
        )

    async def _notify_rejection(
        self,
        result: PipelineResult,
    ) -> None:
        """Best-effort Telegram alert on a rejected live signal."""

        if self.notifier is None:
            return

        from ..notifications.telegram import (
            AlertType,
            format_rejection_alert,
        )

        for gate_name in (
            "shariah",
            "prayer_time",
            "risk",
            "security",
            "execution",
        ):
            gate_result = result.gates.get(gate_name)

            if gate_result is None or gate_result.passed:
                continue

            is_shariah = gate_name in (
                "shariah",
                "prayer_time",
            )

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
                await self.notifier.send(
                    alert_type,
                    text,
                )
            except Exception:
                logger.warning(
                    "Telegram rejection notification failed",
                    exc_info=True,
                )

            return

    async def _notify_trade(
        self,
        *,
        signal: Signal,
        order: OrderResult,
    ) -> None:
        """Best-effort Telegram alert after a confirmed fill."""

        if self.notifier is None:
            return

        from ..notifications.telegram import (
            AlertType,
            format_trade_alert,
        )

        price = (
            order.cummulative_quote_qty / order.executed_qty
            if order.executed_qty > 0
            else signal.price
        )

        text = format_trade_alert(
            side=order.side,
            symbol=order.symbol,
            quantity=order.executed_qty,
            price=price,
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
                "Telegram live-trade notification failed",
                exc_info=True,
          )
