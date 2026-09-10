from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class ZakatResult:
    portfolio_value_usdt: float
    nisab_threshold_usdt: float
    zakat_rate: float
    due: bool
    zakat_amount_usdt: float

def calculate_zakat(portfolio_value_usdt: float, nisab_threshold_usdt: float, zakat_rate: float = 0.025) -> ZakatResult:
    due = portfolio_value_usdt >= nisab_threshold_usdt and nisab_threshold_usdt > 0
    amount = round(portfolio_value_usdt * zakat_rate, 2) if due else 0.0
    return ZakatResult(portfolio_value_usdt, nisab_threshold_usdt, zakat_rate, due, amount)