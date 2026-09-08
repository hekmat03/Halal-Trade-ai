"""Fee and slippage configuration shared by paper and backtest engines.

Slippage is expressed in basis points (bps): 1 bps = 0.01%. A positive
slippage budget makes every fill worse than the quoted price (BUY fills at a
higher price, SELL fills at a lower price), modelling the real cost of crossing
the spread.

The taker fee mirrors Binance's spot taker fee (``taker_bps=10`` => 0.1%).
``bnb_discount_pct`` applies the optional BNB-holdings discount as a straight
percentage reduction of the fee.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeConfig:
    """Fee + slippage parameters used to compute realistic simulated fills."""

    # Taker fee in basis points (e.g. 10 => 0.10%).
    taker_bps: float = 10.0
    # Maker fee in basis points (unused for the immediate market fills we model,
    # kept for realism / future limit orders).
    maker_bps: float = 10.0
    # Slippage budget in basis points applied against the quoted price.
    slippage_bps: float = 0.0
    # Optional BNB discount as a percentage of the fee (0 = no discount).
    bnb_discount_pct: float = 0.0

    def taker_rate(self) -> float:
        """Effective taker fee as a fraction (fees are always a cost)."""
        rate = self.taker_bps / 10000.0
        if self.bnb_discount_pct > 0:
            rate *= 1.0 - self.bnb_discount_pct / 100.0
        return rate

    def slippage_fraction(self) -> float:
        """Slippage as a fraction (e.g. 5 bps => 0.0005)."""
        return self.slippage_bps / 10000.0

    def buy_price(self, quote: float) -> float:
        """Fill price for a BUY: quoted price worsened by slippage."""
        return quote * (1.0 + self.slippage_fraction())

    def sell_price(self, quote: float) -> float:
        """Fill price for a SELL: quoted price worsened by slippage."""
        return quote * (1.0 - self.slippage_fraction())
