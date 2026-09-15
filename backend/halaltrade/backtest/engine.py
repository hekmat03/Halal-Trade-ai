"""Deterministic backtest engine (Delivery 3).

Runs a strategy forward over historical OHLCV candles, generating a Signal on
each bar, applying the four-gate pipeline, and simulating fills with fees,
slippage, mandatory stop-loss and optional take-profit. All at Spot, 1x, no
shorting, exactly like the paper engine.

Determinism: given the same candles + config the same result is reproduced.
There is no randomness in the core path (the RNG seed only exists for
strategies that opt into it). No network is used — candles come from the
marketdata module or injected fixtures.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..config import Settings
from ..gates.base import Context, RiskAccount
from ..marketdata.models import Candle
from ..models import PipelineDecision, Side, Signal
from ..pipeline import Pipeline
from ..positions import TrailingConfig
from ..simulation import FeeConfig, SimAccount
from ..simulation.account import FillError, PositionError
from .base import Strategy
from .performance import ClosedTrade, PerformanceReport, build_report

logger = logging.getLogger(__name__)

__all__ = ["BacktestConfig", "BacktestResult", "BacktestEngine"]


@dataclass
class BacktestConfig:
    """Configuration for a single backtest run."""

    starting_equity: float = 5000.0
    trade_amount: float = 1000.0
    use_strategy_sizing: bool = False
    trailing: Optional[TrailingConfig] = None
    fee_config: FeeConfig = field(default_factory=FeeConfig)
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.05
    warmup: int = 0
    trade_direction: str = "long"
    symbol: str = "BTCUSDT"
    timeframe: str = "1m"
    seed: int = 42
    periods_per_year: float = 945


@dataclass
class BacktestResult:
    report: PerformanceReport
    account: SimAccount
    run_id: str
    strategy_name: str
    config: BacktestConfig


class BacktestEngine:
    """Forward-driving backtest runner that enforces every policy gate."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session=None,
    ) -> None:
        self.settings = settings or Settings(
            trading_mode="backtest",
            live_enabled=False,
        )
        self.session = session

    def run(
        self,
        strategy: Strategy,
        candles: list[Candle],
        config: Optional[BacktestConfig] = None,
        *,
        strategy_name: str | None = None,
    ) -> BacktestResult:
        cfg = config or BacktestConfig()
        candles = list(candles)
        trade_amount = float(cfg.trade_amount)

        account = SimAccount(
            starting_usdt=cfg.starting_equity,
            fee_config=cfg.fee_config,
        )

        closed_trades: list[ClosedTrade] = []
        equity_curve: list[float] = []
        run_id = str(uuid.uuid4())

        name = (
            strategy_name
            or getattr(strategy, "name", None)
            or getattr(strategy, "__name__", "strategy")
        )

        entry = None
        trailing_peak: Optional[float] = None
        trailing_stop_level: Optional[float] = None

        for i in range(len(candles)):
            candle = candles[i]

            if i < cfg.warmup:
                continue

            window = candles[: i + 1]
            price = candle.close

            # ----- 1. Intra-bar stop-loss / take-profit / trailing -----
            if account.has_position and account.stop_loss is not None:

                # Ratchet the trailing stop using this bar's HIGH before
                # checking any exit.
                if (
                    cfg.trailing is not None
                    and cfg.trailing.enabled
                    and cfg.trailing.distance > 0
                ):
                    entry_price = entry[1] if entry else price

                    activated = (
                        cfg.trailing.activation_profit <= 0
                        or candle.high
                        >= entry_price + cfg.trailing.activation_profit
                    )

                    if activated and (
                        trailing_peak is None
                        or candle.high > trailing_peak
                    ):
                        trailing_peak = candle.high
                        trailing_stop_level = (
                            trailing_peak - cfg.trailing.distance
                        )

                if candle.low <= account.stop_loss:
                    fill = account.close_position(
                        account.stop_loss,
                        timestamp=candle.timestamp,
                    )

                    closed_trades.append(
                        self._close_trade(
                            entry,
                            fill,
                            "STOP_LOSS",
                        )
                    )

                    entry = None
                    trailing_peak = trailing_stop_level = None

                elif (
                    account.take_profit is not None
                    and candle.high >= account.take_profit
                ):
                    fill = account.close_position(
                        account.take_profit,
                        timestamp=candle.timestamp,
                    )

                    closed_trades.append(
                        self._close_trade(
                            entry,
                            fill,
                            "TAKE_PROFIT",
                        )
                    )

                    entry = None
                    trailing_peak = trailing_stop_level = None

                elif (
                    cfg.trailing is not None
                    and cfg.trailing.enabled
                    and trailing_stop_level is not None
                    and candle.low <= trailing_stop_level
                ):
                    fill = account.close_position(
                        trailing_stop_level,
                        timestamp=candle.timestamp,
                    )

                    closed_trades.append(
                        self._close_trade(
                            entry,
                            fill,
                            "TRAILING_STOP",
                        )
                    )

                    entry = None
                    trailing_peak = trailing_stop_level = None

            # ----- 2. Mark-to-market equity -----
            equity_curve.append(account.equity(price))

            # ----- 3. Strategy recommendation -----
            signal = strategy(
                window,
                account.btc,
                account.equity(price),
            )

            if signal.side == Side.HOLD:
                continue

            # By default the engine supplies the exact amount.
            resolved_amount = trade_amount

            if (
                cfg.use_strategy_sizing
                and signal.side == Side.BUY
                and signal.amount is not None
                and signal.amount > 0
            ):
                resolved_amount = float(signal.amount)

            signal = signal.model_copy(
                update={
                    "amount": (
                        resolved_amount
                        if signal.side == Side.BUY
                        else signal.amount
                    ),
                    "price": price,
                    "symbol": cfg.symbol,
                    "data_timestamp": candle.timestamp,
                    "client_order_id": (
                        f"bt-{i}-{uuid.uuid4().hex[:8]}"
                    ),
                    "instrument_type": signal.instrument_type,
                }
            )

            if signal.side == Side.BUY and signal.stop_loss is None:
                signal = signal.model_copy(
                    update={
                        "stop_loss": price * (1 - cfg.stop_loss_pct)
                    }
                )

            if signal.side == Side.BUY and signal.proposed_exit is None:
                signal = signal.model_copy(
                    update={
                        "proposed_exit": price * (1 + cfg.take_profit_pct)
                    }
                )

            # ----- 4. Pipeline gates -----
            result = self._evaluate(
                signal,
                account,
                price,
                candle.timestamp,
            )

            if result.decision != PipelineDecision.TRADE:
                continue

            # ----- 5. Explicit simulated fill -----
            if signal.side == Side.BUY and not account.has_position:
                account.stop_loss = signal.stop_loss
                account.take_profit = signal.proposed_exit

                account.avg_entry_price = 0.0

                buy_fill = account.buy(
                    resolved_amount,
                    price,
                    client_order_id=signal.client_order_id,
                    timestamp=candle.timestamp,
                )

                entry = (
                    candle.timestamp,
                    price,
                    buy_fill.fee,
                )

                trailing_peak = trailing_stop_level = None

            elif signal.side == Side.SELL and account.has_position:
                fill = account.close_position(
                    price,
                    client_order_id=signal.client_order_id,
                    timestamp=candle.timestamp,
                )

                closed_trades.append(
                    self._close_trade(
                        entry,
                        fill,
                        "SIGNAL",
                    )
                )

                entry = None
                trailing_peak = trailing_stop_level = None

        # ----- 6. Flatten leftover position -----
        if account.has_position and candles:
            last = candles[-1]

            fill = account.close_position(
                last.close,
                timestamp=last.timestamp,
            )

            closed_trades.append(
                self._close_trade(
                    entry,
                    fill,
                    "FLAT_CLOSE",
                )
            )

            equity_curve.append(
                account.equity(last.close)
            )

        ending_equity = (
            equity_curve[-1]
            if equity_curve
            else account.usdt
        )

        report = build_report(
            starting_equity=cfg.starting_equity,
            ending_equity=ending_equity,
            closed_trades=closed_trades,
            equity_curve=equity_curve,
            fees_paid=account.fees_paid,
            periods_per_year=cfg.periods_per_year,
        )

        if self.session is not None:
            from ..db.recorder import record_backtest_run

            record_backtest_run(
                self.session,
                report,
                run_id=run_id,
                strategy=name,
                symbol=cfg.symbol,
                timeframe=cfg.timeframe,
                candles_count=len(candles),
            )

        return BacktestResult(
            report=report,
            account=account,
            run_id=run_id,
            strategy_name=name,
            config=cfg,
        )

    def _close_trade(
        self,
        entry,
        fill,
        reason: str,
    ) -> ClosedTrade:
        entry_price = entry[1] if entry else 0.0
        entry_fee = entry[2] if entry else 0.0

        return ClosedTrade(
            entry_time=entry[0] if entry else None,
            exit_time=fill.timestamp,
            entry_price=entry_price,
            exit_price=fill.price,
            quantity=fill.quantity,
            fees=fill.fee + entry_fee,
            pnl=(
                fill.quantity * (fill.price - entry_price)
                - fill.fee
                - entry_fee
            ),
            exit_reason=reason,
        )

    def _evaluate(
        self,
        signal: Signal,
        account: SimAccount,
        price: float,
        now,
    ) -> PipelineResult:
        """Build a fresh Context whose clock is the bar timestamp."""

        def clock() -> datetime:
            return now

        context = Context(
            settings=self.settings,
            account=RiskAccount(
                balance=account.usdt,
                current_position_value=account.btc * price,
                daily_pnl=account.realized_pnl,
                realized_pnl_today=account.realized_pnl,
                open_position_count=(
                    1 if account.has_position else 0
                ),
                base_holdings=account.btc,
            ),
            idempotency_registry=set(),
            order_status_confirmed=True,
            now=clock,
        )

        return Pipeline(
            self.settings,
            context,
        ).evaluate(signal)
