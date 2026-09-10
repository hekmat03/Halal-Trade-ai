"""Zakat calculator — optional, user-configurable (see master spec section 4)."""
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
    if portfolio_value_usdt < 0:
        raise ValueError(f"portfolio_value_usdt must be >= 0, got {portfolio_value_usdt}")
    if nisab_threshold_usdt < 0:
        raise ValueError(f"nisab_threshold_usdt must be >= 0, got {nisab_threshold_usdt}")
    if not (0.0 <= zakat_rate <= 1.0):
        raise ValueError(f"zakat_rate must be in [0, 1], got {zakat_rate}")

    due = portfolio_value_usdt >= nisab_threshold_usdt and nisab_threshold_usdt > 0
    amount = round(portfolio_value_usdt * zakat_rate, 2) if due else 0.0

    return ZakatResult(
        portfolio_value_usdt=portfolio_value_usdt,
        nisab_threshold_usdt=nisab_threshold_usdt,
        zakat_rate=zakat_rate,
        due=due,
        zakat_amount_usdt=amount,
    )