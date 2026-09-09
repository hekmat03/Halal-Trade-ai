"""RISK GATE — hard, user-configurable caps.

Rules:
* The signal must carry an explicit, exact size. Missing amount/quantity → reject
  (NO TRADE). HOLD signals carry no size and are rejected here (that is the
  desired "do nothing" outcome).
* A mandatory stop-loss is required on every non-HOLD trade.
* Position size cap, exposure cap, loss-per-trade cap, daily-loss cap and
  drawdown cap are enforced as hard ceilings.
* A minimum account balance is required to trade.
* Max concurrent open positions: a BUY that opens a NEW position while the
  position cap (``max_open_positions``, default 1) is already reached is
  rejected. The caller feeds live position state via ``RiskAccount``.
* Never-short invariant: a SELL may only dispose of owned base asset
  (``RiskAccount.base_holdings``); selling more than held is rejected.
* Daily-loss limit consults realized P&L for the day (``realized_pnl_today``,
  falling back to ``daily_pnl`` when unset).

All caps come from ``Settings`` (env configurable) so the owner controls them.
Every failed check carries an explicit ``REJECT: ...`` reason for the audit log.
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

        account = context.account or RiskAccount()

        # --- 2. Mandatory stop-loss on every trade ---
        if signal.stop_loss is None:
            failed.append("REJECT: mandatory stop-loss missing on this trade.")
            reasons.append("REJECT: stop-loss is mandatory on every trade.")
        else:
            reasons.append(f"OK: stop-loss present ({signal.stop_loss}).")

        # --- 2b. Never-short invariant (SELL can only dispose of owned BTC) ---
        # The gate cannot create an order quantity from an amount-only SELL, so it
        # enforces the invariant where it can be stated exactly: an explicit
        # quantity must not exceed what is owned. (The simulation/fill layer
        # enforces the same rule on computed quantities as the final backstop.)
        if signal.side.value == "SELL" and signal.quantity is not None:
            if signal.price is not None and signal.price <= 0:
                failed.append("REJECT: non-positive SELL price — size is unverifiable.")
            elif signal.quantity > account.base_holdings + 1e-12:
                failed.append(
                    f"REJECT: SELL {signal.quantity:.8f} BTC exceeds owned holdings "
                    f"{account.base_holdings:.8f} (no shorting allowed)."
                )
            else:
                reasons.append(
                    f"OK: SELL {signal.quantity:.8f} <= owned {account.base_holdings:.8f}."
                )

        # --- 2c. Max concurrent open positions (a new BUY opens a position) ---
        if signal.side.value == "BUY":
            if account.open_position_count >= settings.max_open_positions:
                failed.append(
                    f"REJECT: already holding {account.open_position_count} open "
                    f"position(s) >= max_open_positions {settings.max_open_positions} "
                    "(single-asset bot holds at most one position)."
                )
            else:
                reasons.append(
                    f"OK: open positions {account.open_position_count} < "
                    f"max {settings.max_open_positions}."
                )

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

        # --- Daily-loss limit (consults realized P&L for the day) ---
        day_pnl = (
            account.realized_pnl_today
            if account.realized_pnl_today is not None
            else account.daily_pnl
        )
        if day_pnl < -settings.max_daily_loss:
            failed.append(
                f"REJECT: realized daily P&L {day_pnl:.2f} exceeds max_daily_loss "
                f"{settings.max_daily_loss}."
            )
        else:
            reasons.append(f"OK: daily P&L {day_pnl:.2f} within loss cap.")

        if account.equity_peak and account.equity_peak > 0:
            dd = (account.equity_peak - equity) / account.equity_peak
            if dd > settings.max_drawdown:
                failed.append(
                    f"REJECT: drawdown {dd:.2%} > max_drawdown {settings.max_drawdown:.2%}."
                )

        reasons.extend(failed)
        return RiskResult(gate_name=self.name, passed=not failed, reasons=reasons)
