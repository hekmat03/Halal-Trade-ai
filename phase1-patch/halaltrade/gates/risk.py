"""RISK GATE — hard, user-configurable caps.

Rules:
* Explicit trade size is required.
* HOLD signals are rejected here as NO TRADE.
* Stop-loss is mandatory.
* Position size, exposure, loss-per-trade, daily-loss and drawdown
  limits are enforced.
* Minimum account balance is required.
* Maximum concurrent positions are enforced.
* SELL cannot exceed owned base asset.
* Daily-loss uses realized P&L when available.
"""

from __future__ import annotations

from ..config import Settings
from ..models import RiskResult, Signal
from .base import Context, Gate, RiskAccount

__all__ = ["RiskGate"]


class RiskGate(Gate):

    name: str = "risk"

    def evaluate(
        self,
        signal: Signal,
        context: Context
    ) -> RiskResult:

        reasons: list[str] = []
        failed: list[str] = []

        settings: Settings = context.settings

        # --- 1. Explicit size is mandatory --------------------

        if signal.side.value == "HOLD":

            failed.append(
                "REJECT: signal is HOLD "
                "(no explicit trade size) — no trade."
            )

            reasons.append(
                "REJECT: HOLD carries no size/amount; "
                "reflected as NO TRADE."
            )

            return RiskResult(
                gate_name=self.name,
                passed=False,
                reasons=reasons
            )

        notional = signal.notional()

        if notional is None or notional <= 0:

            failed.append(
                "REJECT: missing or non-positive order size "
                "(need explicit amount or valid quantity x price)."
            )

            reasons.append(
                "REJECT: missing amount/size — "
                "the user must provide an exact size."
            )

            return RiskResult(
                gate_name=self.name,
                passed=False,
                reasons=reasons
            )

        reasons.append(
            f"OK: explicit size present "
            f"(notional={notional:.6f} USDT)."
        )

        account = context.account or RiskAccount()

        # --- 1b. Drawdown lockout -------------------------------

        if account.trading_locked:

            reason = (
                account.trading_locked_reason
                or "max_drawdown breach"
            )

            failed.append(
                f"REJECT: account is drawdown-locked "
                f"({reason}) — no trade until an operator "
                "explicitly unlocks it."
            )

            reasons.append(
                "REJECT: trading_locked=True "
                "(drawdown lockout active)."
            )

            return RiskResult(
                gate_name=self.name,
                passed=False,
                reasons=reasons
            )

        # --- 2. Mandatory stop-loss -----------------------------

        if signal.stop_loss is None:

            failed.append(
                "REJECT: mandatory stop-loss "
                "missing on this trade."
            )

            reasons.append(
                "REJECT: stop-loss is mandatory "
                "on every trade."
            )

        else:

            reasons.append(
                f"OK: stop-loss present ({signal.stop_loss})."
            )

        # --- 2b. Never-short invariant --------------------------

        if signal.side.value == "SELL":

            if signal.quantity is not None:

                if (
                    signal.price is not None
                    and signal.price <= 0
                ):
                    failed.append(
                        "REJECT: non-positive SELL price — "
                        "size is unverifiable."
                    )

                elif (
                    signal.quantity
                    > account.base_holdings + 1e-12
                ):
                    failed.append(
                        f"REJECT: SELL "
                        f"{signal.quantity:.8f} BTC exceeds "
                        f"owned holdings "
                        f"{account.base_holdings:.8f} "
                        "(no shorting allowed)."
                    )

                else:
                    reasons.append(
                        f"OK: SELL "
                        f"{signal.quantity:.8f} <= owned "
                        f"{account.base_holdings:.8f}."
                    )

        # --- 2c. Maximum concurrent positions -------------------

        if signal.side.value == "BUY":

            if (
                account.open_position_count
                >= settings.max_open_positions
            ):

                failed.append(
                    f"REJECT: already holding "
                    f"{account.open_position_count} "
                    f"open position(s) >= "
                    f"max_open_positions "
                    f"{settings.max_open_positions} "
                    "(single-asset bot holds at most "
                    "one position)."
                )

            else:

                reasons.append(
                    f"OK: open positions "
                    f"{account.open_position_count} < "
                    f"max "
                    f"{settings.max_open_positions}."
                )

        # --- 3. Position size cap -------------------------------

        if (
            signal.amount is not None
            and signal.amount > settings.max_position_size
        ):

            failed.append(
                f"REJECT: notional {signal.amount} > "
                f"max_position_size "
                f"{settings.max_position_size}."
            )

        if (
            signal.quantity is not None
            and signal.price is not None
        ):

            order_value = (
                signal.quantity * signal.price
            )

            if order_value > settings.max_position_size:

                failed.append(
                    f"REJECT: order value "
                    f"{order_value} > "
                    f"max_position_size "
                    f"{settings.max_position_size}."
                )

        # --- Account balance ------------------------------------

        equity = account.equity()

        if equity < settings.min_account_balance:

            failed.append(
                f"REJECT: account equity "
                f"{equity:.2f} < min_account_balance "
                f"{settings.min_account_balance}."
            )

        else:

            reasons.append(
                f"OK: account equity {equity:.2f} "
                f">= minimum balance."
            )

        # --- Exposure cap ---------------------------------------

        if (
            signal.price is not None
            and signal.instrument_type is not None
        ):

            if signal.side.value == "BUY":

                exposure = (
                    account.current_position_value
                    + notional
                )

                if exposure > settings.max_exposure:

                    failed.append(
                        f"REJECT: resulting exposure "
                        f"{exposure:.2f} > max_exposure "
                        f"{settings.max_exposure}."
                    )

                else:

                    reasons.append(
                        f"OK: resulting exposure "
                        f"{exposure:.2f} within cap."
                    )

        # --- Loss per trade -------------------------------------

        if (
            signal.stop_loss is not None
            and signal.price is not None
            and signal.quantity is not None
        ):

            entry = (
                signal.proposed_entry
                or signal.price
            )

            risk_per_unit = abs(
                entry - signal.stop_loss
            )

            risk_usd = (
                risk_per_unit * signal.quantity
            )

            if risk_usd > settings.max_loss_per_trade:

                failed.append(
                    f"REJECT: stop-distance risk "
                    f"{risk_usd:.2f} USDT > "
                    f"max_loss_per_trade "
                    f"{settings.max_loss_per_trade}."
                )

            else:

                reasons.append(
                    f"OK: stop-distance risk "
                    f"{risk_usd:.2f} within cap."
                )

        # --- Daily loss limit -----------------------------------

        day_pnl = (
            account.realized_pnl_today
            if account.realized_pnl_today is not None
            else account.daily_pnl
        )

        if day_pnl < -settings.max_daily_loss:

            failed.append(
                f"REJECT: realized daily P&L "
                f"{day_pnl:.2f} exceeds max_daily_loss "
                f"{settings.max_daily_loss}."
            )

        else:

            reasons.append(
                f"OK: daily P&L {day_pnl:.2f} "
                "within loss cap."
            )

        # --- Drawdown -------------------------------------------

        if (
            account.equity_peak
            and account.equity_peak > 0
        ):

            dd = (
                (account.equity_peak - equity)
                / account.equity_peak
            )

            if dd > settings.max_drawdown:

                failed.append(
                    f"REJECT: drawdown {dd:.2%} > "
                    f"max_drawdown "
                    f"{settings.max_drawdown:.2%}."
                )

        reasons.extend(failed)

        return RiskResult(
            gate_name=self.name,
            passed=not failed,
            reasons=reasons
        )