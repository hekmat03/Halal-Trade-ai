"""Zakat calculator — optional, user-configurable (see master spec section 4).

This module does ONE narrow thing: given a portfolio value and a nisab
threshold, compute the Zakat due at the standard rate. It does NOT:
* determine the correct nisab value (that depends on current gold/silver
  prices and school of thought — the user/scholar sets this)
* determine whether a full lunar (Hijri) year (hawl) has passed on the
  holdings — that is a record-keeping/calendar concern for the caller
* make any independent religious ruling — same posture as the Shariah gate:
  the system enforces user-defined rules, it does not invent them

The system does NOT make independent religious rulings. The user is
responsible for determining Zakat requirements and may consult a qualified
scholar, exactly as stated in the master spec for the Shariah policy system.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ZakatResult", "calculate_zakat"]


@dataclass(frozen=True)
class ZakatResult:
    portfolio_value_usdt: float
    nisab_threshold_usdt: float
    zakat_rate: float
    due: bool
    zakat_amount_usdt: float


def calculate_zakat(
    portfolio_value_usdt: float,
    nisab_threshold_usdt: float,
    zakat_rate: float = 0.025,
) -> ZakatResult:
    """Compute Zakat due on a portfolio value, if it meets the nisab threshold.

    Zakat is due on the FULL portfolio value once it meets/exceeds nisab, not
    only on the amount above nisab — this mirrors standard Zakat-on-wealth
    calculation (unlike a marginal tax bracket).

    Raises ValueError on negative inputs — a negative portfolio value or
    threshold is a caller bug, not a valid "zero Zakat" case.
    """
    if portfolio_value_usdt < 0:
        raise ValueError(
            f"portfolio_value_usdt must be >= 0, got {portfolio_value_usdt}"
        )

    if nisab_threshold_usdt < 0:
        raise ValueError(
            f"nisab_threshold_usdt must be >= 0, got {nisab_threshold_usdt}"
        )

    if not (0.0 <= zakat_rate <= 1.0):
        raise ValueError(
            f"zakat_rate must be in [0, 1], got {zakat_rate}"
        )

    due = (
        portfolio_value_usdt >= nisab_threshold_usdt
        and nisab_threshold_usdt > 0
    )

    amount = (
        round(portfolio_value_usdt * zakat_rate, 2)
        if due
        else 0.0
    )

    return ZakatResult(
        portfolio_value_usdt=portfolio_value_usdt,
        nisab_threshold_usdt=nisab_threshold_usdt,
        zakat_rate=zakat_rate,
        due=due,
        zakat_amount_usdt=amount,
    )