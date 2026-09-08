"""A single simulated cash (Spot, 1x, no leverage) BTC/USDT account.

This is the exact thing both the paper engine and the backtest engine trade,
so the P&L and equity math is identical by construction.

Rules that are hard here and can never be violated:
* Spot only, 1x leverage — there is no margin/borrow concept at all.
* No shorting: a SELL can only ever dispose of an owned position
  (``ShortError`` if there is nothing to sell / quantity exceeds holdings).
* The user/strategy supplies the exact amount; this account only executes it.
* Fees and slippage are always costs — they are applied to every fill.

All values are pure numbers computed from arguments; nothing is fabricated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .fees import FeeConfig

__all__ = [
    "Fill",
    "FillError",
    "PositionError",
    "ShortError",
    "SimAccount",
]


class FillError(Exception):
    """Base for fill-level errors (shorting, insufficient funds, no position)."""


class ShortError(FillError):
    """Raised when a SELL would create or exceed a short position."""


class PositionError(FillError):
    """Raised when an operation requires an open position and there is none."""


@dataclass
class Fill:
    """One confirmed simulated fill. Prices/qty/fee are final, never assumed."""

    side: str                     # "BUY" | "SELL"
    price: float                  # fill price (after slippage)
    quantity: float               # base-asset (BTC) quantity
    notional: float               # quote notional traded (= qty * price)
    fee: float                    # fee paid on this fill
    timestamp: Optional[datetime] = None
    # Client-side idempotency key that produced this fill, if any.
    client_order_id: Optional[str] = None

    @property
    def is_buy(self) -> bool:
        return self.side == "BUY"


@dataclass
class SimAccount:
    """Simulated BTC/USDT spot account."""

    starting_usdt: float
    fee_config: FeeConfig = field(default_factory=FeeConfig)

    # --- live state ---------------------------------------------------------
    usdt: float = 0.0
    btc: float = 0.0
    avg_entry_price: float = 0.0        # weighted average entry of the open position
    stop_loss: Optional[float] = None   # mandatory on the open position
    take_profit: Optional[float] = None # optional exit target
    realized_pnl: float = 0.0
    fees_paid: float = 0.0

    def __post_init__(self) -> None:
        self.usdt = float(self.starting_usdt)

    # -- position helpers ----------------------------------------------------
    @property
    def has_position(self) -> bool:
        return self.btc > 1e-12

    @property
    def position_size(self) -> float:
        """Current open position in base asset (BTC)."""
        return self.btc

    # -- equity / P&L --------------------------------------------------------
    def equity(self, price: float) -> float:
        """Mark-to-market equity at a given reference price."""
        return self.usdt + self.btc * price

    def unrealized_pnl(self, price: float) -> float:
        """Unrealized P&L on the open position at *price*."""
        if not self.has_position:
            return 0.0
        return self.btc * (price - self.avg_entry_price)

    # -- fills ---------------------------------------------------------------
    def buy(self, amount_usdt: float, price: float, *, client_order_id: str | None = None,
            timestamp: datetime | None = None) -> Fill:
        """Deploy *amount_usdt* of quote into BTC at *price* (1x spot).

        The user's exact amount is the notional deployed. The fill price is
        worsened by slippage, and the taker fee is charged on top, so the USDT
        balance falls by ``amount_usdt + fee``.
        """
        if amount_usdt is None or amount_usdt <= 0:
            raise FillError(f"BUY amount must be positive, got {amount_usdt!r}")
        fill_price = self.fee_config.buy_price(price)
        quantity = amount_usdt / fill_price
        fee = amount_usdt * self.fee_config.taker_rate()

        new_btc = self.btc + quantity
        total_cost = self.btc * self.avg_entry_price + quantity * fill_price
        self.avg_entry_price = total_cost / new_btc
        self.btc = new_btc
        # The caller decides the stop/target before the fill; we never clear them
        # on a scale-up (they remain the protection of the combined position).
        self.usdt -= (amount_usdt + fee)
        self.fees_paid += fee
        return Fill(
            side="BUY",
            price=fill_price,
            quantity=quantity,
            notional=amount_usdt,
            fee=fee,
            timestamp=timestamp,
            client_order_id=client_order_id,
        )

    def sell(self, quantity: float, price: float, *, client_order_id: str | None = None,
             timestamp: datetime | None = None) -> Fill:
        """Sell *quantity* BTC at *price*. Never shorts.

        ``quantity`` must be > 0 and <= the owned position; otherwise
        ``ShortError`` (a spot sale can only dispose of what is actually owned).
        Realized P&L = proceeds(less slippage & fee) - average cost of sold BTC.
        """
        if quantity is None or quantity <= 0:
            raise FillError(f"SELL quantity must be positive, got {quantity!r}")
        if quantity > self.btc + 1e-12:
            raise ShortError(
                f"cannot SELL {quantity:.8f} BTC with only {self.btc:.8f} owned "
                "(no shorting allowed)"
            )
        fill_price = self.fee_config.sell_price(price)
        notional = quantity * fill_price
        fee = notional * self.fee_config.taker_rate()
        net = notional - fee
        cost_basis = quantity * self.avg_entry_price

        self.realized_pnl += (notional - cost_basis - fee)
        self.btc -= quantity
        self.usdt += net
        self.fees_paid += fee

        if not self.has_position:
            self.avg_entry_price = 0.0
            self.stop_loss = None
            self.take_profit = None
        return Fill(
            side="SELL",
            price=fill_price,
            quantity=quantity,
            notional=notional,
            fee=fee,
            timestamp=timestamp,
            client_order_id=client_order_id,
        )

    def sell_amount(self, amount_usdt: float, price: float, *,
                    client_order_id: str | None = None,
                    timestamp: datetime | None = None) -> Fill:
        """Sell a notional *amount_usdt* worth of BTC, capped at the position.

        Raises ``PositionError`` if there is no open position.
        """
        if not self.has_position:
            raise PositionError("cannot SELL: no open position to sell (no shorting)")
        fill_price = self.fee_config.sell_price(price)
        quantity = amount_usdt / fill_price
        if quantity > self.btc:
            quantity = self.btc
        return self.sell(quantity, price, client_order_id=client_order_id, timestamp=timestamp)

    def close_position(self, price: float, *, client_order_id: str | None = None,
                       timestamp: datetime | None = None) -> Fill:
        """Sell the entire open position at *price* (used by stop/TP exits)."""
        if not self.has_position:
            raise PositionError("cannot close: no open position")
        return self.sell(self.btc, price, client_order_id=client_order_id, timestamp=timestamp)

    def hit_stop_loss(self, price: float, *, client_order_id: str | None = None,
                      timestamp: datetime | None = None) -> bool:
        """Close the position if *price* has reached the mandatory stop-loss.

        Returns True when the stop fired and closed the position; False if there
        is no position or the stop has not been reached.

        A long position is a losing trade when price falls to or below
        ``stop_loss``. Use the stop level (not the raw market print) as the fill
        so the model reflects the resting stop order.
        """
        if not self.has_position or self.stop_loss is None:
            return False
        if price <= self.stop_loss:
            self.sell(self.btc, self.stop_loss, client_order_id=client_order_id,
                      timestamp=timestamp)
            return True
        return False

    def hit_take_profit(self, price: float, *, client_order_id: str | None = None,
                        timestamp: datetime | None = None) -> bool:
        """Close the position if *price* has reached the optional take-profit."""
        if not self.has_position or self.take_profit is None:
            return False
        if price >= self.take_profit:
            self.sell(self.btc, self.take_profit, client_order_id=client_order_id,
                      timestamp=timestamp)
            return True
        return False
