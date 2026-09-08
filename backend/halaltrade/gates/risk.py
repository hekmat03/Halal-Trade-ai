"""RISK GATE — hard, user-configurable caps.

Rules:
* The signal must carry an explicit, exact size. Missing amount/quantity → reject
  (NO TRADE). HOLD signals carry no size and are rejected here (that is the
  desired "do nothing" outcome).
* A mandatory stop-loss is required on every non-HOLD trade.
* Position size cap, exposure cap, loss-per-trade cap, daily-loss cap and
  drawdown cap are enforced as hard ceilings.
* A minimum account balance is required to trade.

All caps come from ``Settings`` (env configurable) so the owner controls them.
"""
from __future__ import annotations

from ..config import Settings
from ..models import RiskResult, Signal
from .base import Context, Gate, RiskAccount

__all__ = ["RiskGate"]


class RiskGate(Gate):
    name: str = "risk"

    def evaluate(self, signal: Signal, context: Context) -> RiskResult:
        reasons: list[str] = []
        failed: list[str] = []
        settings: Settings = context.settings

        # --- 1. Explicit size is mandatory (no trade without an exact size) ---
        if signal.side.value == "HOLD":
            failed.append(
                "REJECT: signal is HOLD (no explicit trade size) — no trade."
            )
            reasons.append(
                "REJECT: HOLD carries no size/amount; reflected as NO TRADE."
            )
            return RiskResult(gate_name=self.name, passed=False, reasons=reasons)

        notional = signal.notional()
        if notional is None or notional <= 0:
            failed.append(
                "REJECT: missing or non-positive order size (need explicit amount or "
                "valid quantity x price)."
            )
            reasons.append(
                "REJECT: missing amount/size — the user must provide an exact size."
            )
            return RiskResult(gate_name=self.name, passed=False, reasons=reasons)

        reasons.append(f"OK: explicit size present (notional={notional:.6f} USDT).")

        # --- 2. Mandatory stop-loss on every trade ---
        if signal.stop_loss is None:
            failed.append("REJECT: mandatory stop-loss missing on this trade.")
            reasons.append("REJECT: stop-loss is mandatory on every trade.")
        else:
            reasons.append(f"OK: stop-loss present ({signal.stop_loss}).")

        # --- 3. Caps (only evaluated if we already have a real trade) ---
        if signal.amount is not None and signal.amount > settings.max_position_size:
            failed.append(
                f"REJECT: notional {signal.amount} > max_position_size "
                f"{settings.max_position_size}."
            )
        if signal.quantity is not None and signal.price is not None:
            if signal.quantity * signal.price > settings.max_position_size:
                failed.append(
                    f"REJECT: order value {signal.quantity * signal.price} > "
                    f"max_position_size {settings.max_position_size}."
                )

        account = context.account or RiskAccount()
        equity = account.equity()
        if equity < settings.min_account_balance:
            failed.append(
                f"REJECT: account equity {equity:.2f} < min_account_balance "
                f"{settings.min_account_balance}."
            )
        else:
            reasons.append(f"OK: account equity {equity:.2f} >= minimum balance.")

        if signal.price is not None and signal.instrument_type is not None:
            # exposure cap on a BUY (increasing exposure)
            if signal.side.value == "BUY":
                exposure = account.current_position_value + notional
                if exposure > settings.max_exposure:
                    failed.append(
                        f"REJECT: resulting exposure {exposure:.2f} > max_exposure "
                        f"{settings.max_exposure}."
                    )
                else:
                    reasons.append(f"OK: resulting exposure {exposure:.2f} within cap.")

        # loss-per-trade based on stop distance
        if (
            signal.stop_loss is not None
            and signal.price is not None
            and signal.quantity is not None
        ):
            entry = signal.proposed_entry or signal.price
            risk_per_unit = abs(entry - signal.stop_loss)
            risk_usd = risk_per_unit * signal.quantity
            if risk_usd > settings.max_loss_per_trade:
                failed.append(
                    f"REJECT: stop-distance risk {risk_usd:.2f} USDT > "
                    f"max_loss_per_trade {settings.max_loss_per_trade}."
                )
            else:
                reasons.append(f"OK: stop-distance risk {risk_usd:.2f} within cap.")

        if account.daily_pnl < -settings.max_daily_loss:
            failed.append(
                f"REJECT: daily P&L {account.daily_pnl:.2f} exceeds max_daily_loss "
                f"{settings.max_daily_loss}."
            )

        if account.equity_peak and account.equity_peak > 0:
            dd = (account.equity_peak - equity) / account.equity_peak
            if dd > settings.max_drawdown:
                failed.append(
                    f"REJECT: drawdown {dd:.2%} > max_drawdown {settings.max_drawdown:.2%}."
                )

        reasons.extend(failed)
        return RiskResult(gate_name=self.name, passed=not failed, reasons=reasons)
