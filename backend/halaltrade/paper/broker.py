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
    ) -> None:
        self.settings = settings or Settings(trading_mode="paper", live_enabled=False)
        self.data_source = data_source
        self.fee_config = fee_config or FeeConfig()
        self.account = SimAccount(starting_usdt=starting_usdt, fee_config=self.fee_config)
        self.session = session
        # A persistent idempotency registry shared across all orders on this broker,
        # so a resubmitted clientOrderId cannot create a second fill.
        self._idempotency: set[str] = set()
        self._now = now or utcnow
        self._pipeline = pipeline

    # -- small public state helpers ------------------------------------------
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
            ),
            idempotency_registry=self._idempotency,
            order_status_confirmed=True,  # the paper broker itself confirms fills
            now=self._now,
        )

    async def execute(self, signal: Signal) -> PaperResult:
        """Run a paper order: gates -> fresh data -> explicit fill.

        The exact user amount is mandatory (manual size control). This returns a
        structured result and never raises for a policy rejection.
        """
        # 1. Manual size rule: the user must supply the exact USDT amount.
        if signal.amount is None or signal.amount <= 0:
            return PaperResult(
                executed=False,
                reason="NO TRADE: exact user USDT amount is required (manual size control).",
                signal=signal,
            )

        # 2. Fresh market data (never trade on stale/unavailable data).
        ticker = await self._fresh_ticker(signal.symbol)
        if ticker is None:
            return PaperResult(
                executed=False,
                reason="NO TRADE: market data unavailable or stale — no fill.",
                signal=signal,
            )

        # Stamp the signal with the observed price/freshness so the gates see it.
        eval_signal = signal.model_copy(
            update={"price": ticker.price, "data_timestamp": ticker.timestamp}
        )

        # 3. Enforce the four-gate pipeline before any paper fill.
        pipeline = self._pipeline or Pipeline(self.settings, self._build_context(eval_signal, ticker.price))
        result = pipeline.evaluate(eval_signal)
        if result.decision.value != "TRADE":
            return PaperResult(
                executed=False,
                reason="; ".join(result.reasons) or "NO TRADE: a gate rejected the order.",
                signal=eval_signal,
                pipeline=result,
            )

        # 4. Explicit fill/confirmation step (never assumed). The no-shorting /
        #    fill-validity rules are enforced here, inside the simulation.
        try:
            fill = self._simulate_fill(eval_signal, ticker)
        except (FillError, PositionError, ShortError) as exc:
            return PaperResult(
                executed=False,
                reason=f"NO TRADE: fill refused by simulation — {exc}",
                signal=eval_signal,
                pipeline=result,
            )

        return await self._confirm(signal=eval_signal, ticker=ticker, fill=fill, pipeline=result)

    async def _fresh_ticker(self, symbol: str) -> Optional[Ticker]:
        try:
            ticker = await self.data_source.get_ticker(symbol)
        except DataUnavailableError:
            return None
        if is_stale(ticker, self.settings.market_data_max_age_seconds, now=self._now()):
            return None
        return ticker

    def _simulate_fill(self, signal: Signal, ticker: Ticker) -> Fill:
        """Apply slippage + fees and move the simulated account. Spot, 1x, no short."""
        ts = self._now()
        # Set the mandatory stop-loss / optional take-profit on the position.
        if signal.stop_loss is not None:
            self.account.stop_loss = signal.stop_loss
        if signal.proposed_exit is not None:
            self.account.take_profit = signal.proposed_exit

        if signal.side == Side.BUY:
            return self.account.buy(
                signal.amount, ticker.price,
                client_order_id=signal.client_order_id, timestamp=ts,
            )
        if signal.side == Side.SELL:
            return self.account.sell_amount(
                signal.amount, ticker.price,
                client_order_id=signal.client_order_id, timestamp=ts,
            )
        raise FillError(f"unsupported side for paper fill: {signal.side}")  # HOLD never reaches here

    async def _confirm(
        self,
        *,
        signal: Signal,
        ticker: Ticker,
        fill: Fill,
        pipeline: PipelineResult,
    ) -> PaperResult:
        """Persist the confirmed fill (if persistence is configured) and report."""
        if self.session is not None:
            from ..db.recorder import record_paper_trade, record_position
            record_paper_trade(
                self.session, signal,
                fill_price=fill.price, quantity=fill.quantity,
                notional=fill.notional, fee=fill.fee,
            )
            if fill.is_buy:
                record_position(
                    self.session, mode="paper",
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

    def close_all_for_stop_loss(self, price: float, *, client_order_id: str | None = None):
        """Close any open position whose stop-loss has been reached.

        This is the pump the caller drives with each new price observation; call
        after refreshing market data to enforce the mandatory stop-loss. Returns
        True when the stop fired and closed the position.
        """
        if self.account.hit_stop_loss(price, client_order_id=client_order_id):
            logger.info("paper stop-loss fired at %s (client order %s)", price, client_order_id)
            return True
        return False
