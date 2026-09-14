"""Telegram alerts (master spec section 21 — NOTIFICATIONS AND ALERTS).

Design mirrors the same fail-safe pattern as the Mistral client
(``research/mistral_client.py``): sending a notification can NEVER crash or
block the trading loop. If Telegram is unreachable, misconfigured, or the
bot token is missing, this logs a warning and moves on — a failed alert is
never allowed to become a failed trade.

Per spec: "User can enable/disable each alert type" — every alert type has
its own settings toggle, checked before any network call is attempted.

Testability: message-formatting functions (``format_*``) are pure, take
plain data, and are unit-tested without any network access. The actual send
(``TelegramNotifier.send``) needs httpx + a real bot token and chat ID —
that part must be exercised in a real environment, not here.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)

__all__ = [
    "AlertType",
    "TelegramNotifier",
    "format_trade_alert",
    "format_rejection_alert",
    "format_system_alert",
    "format_daily_summary",
]

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class AlertType(str, Enum):
    TRADE_EXECUTED = "trade_executed"
    STRATEGY_CHANGE = "strategy_change"
    RISK_REJECTION = "risk_rejection"
    SHARIAH_REJECTION = "shariah_rejection"
    SYSTEM_ERROR = "system_error"
    API_FAILURE = "api_failure"
    EMERGENCY_STOP = "emergency_stop"
    DAILY_PNL = "daily_pnl"
    NEW_OPPORTUNITY = "new_opportunity"


def format_trade_alert(
    side: str,
    symbol: str,
    quantity: float,
    price: float,
    *,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    reason: str = "",
) -> str:
    """Format a trade-execution alert. Pure function, no network."""
    lines = [f"\U0001F4CA Trade Executed: {side} {symbol}", f"Quantity: {quantity:.8f}", f"Price: ${price:,.2f}"]
    if stop_loss is not None:
        lines.append(f"Stop-loss: ${stop_loss:,.2f}")
    if take_profit is not None:
        lines.append(f"Take-profit: ${take_profit:,.2f}")
    if reason:
        lines.append(f"Reason: {reason}")
    return "\n".join(lines)


def format_rejection_alert(gate_name: str, reasons: list[str], *, is_shariah: bool = False) -> str:
    """Format a gate-rejection alert (risk or Shariah). Pure function."""
    icon = "\U0001F54C" if is_shariah else "\u26D4"
    label = "Shariah Rejection" if is_shariah else "Risk Rejection"
    lines = [f"{icon} {label} — {gate_name}"]
    for reason in reasons[:5]:
        lines.append(f"  • {reason}")
    if len(reasons) > 5:
        lines.append(f"  ... and {len(reasons) - 5} more")
    return "\n".join(lines)


def format_system_alert(event_type: str, message: str) -> str:
    """Format a system-level alert (error, API failure, emergency stop). Pure function."""
    icon = "\U0001F6A8" if event_type == AlertType.EMERGENCY_STOP.value else "\u26A0\uFE0F"
    return f"{icon} {event_type.replace('_', ' ').title()}\n{message}"


def format_daily_summary(
    *,
    date: str,
    starting_equity: float,
    ending_equity: float,
    trades: int,
    wins: int,
    losses: int,
    realized_pnl: float,
) -> str:
    """Format the daily P&L summary report. Pure function."""
    pct = (ending_equity - starting_equity) / starting_equity * 100 if starting_equity > 0 else 0.0
    return (
        f"\U0001F4C5 Daily Summary — {date}\n"
        f"Equity: ${starting_equity:,.2f} -> ${ending_equity:,.2f} ({pct:+.2f}%)\n"
        f"Trades: {trades} (wins: {wins}, losses: {losses})\n"
        f"Realized P&L: ${realized_pnl:+,.2f}"
    )


_ALERT_SETTING_MAP: dict[AlertType, str] = {
    AlertType.TRADE_EXECUTED: "telegram_alert_trade_execution",
    AlertType.STRATEGY_CHANGE: "telegram_alert_strategy_change",
    AlertType.RISK_REJECTION: "telegram_alert_risk_rejection",
    AlertType.SHARIAH_REJECTION: "telegram_alert_shariah_rejection",
    AlertType.SYSTEM_ERROR: "telegram_alert_system_error",
    AlertType.API_FAILURE: "telegram_alert_api_failure",
    AlertType.EMERGENCY_STOP: "telegram_alert_emergency_stop",
    AlertType.DAILY_PNL: "telegram_alert_daily_pnl",
    AlertType.NEW_OPPORTUNITY: "telegram_alert_new_opportunity",
}


class TelegramNotifier:
    """Sends alerts to a single Telegram chat via the Bot API.

    Every failure mode (Telegram disabled, missing token/chat_id, network
    error, non-2xx response) resolves to a logged warning and a returned
    False — NEVER an exception propagating into the trading loop.
    """

    def __init__(self, settings: Optional[Settings] = None, *, client: Optional[httpx.AsyncClient] = None) -> None:
        self.settings = settings or Settings()
        self._client = client

    def _is_enabled(self, alert_type: AlertType) -> bool:
        if not getattr(self.settings, "telegram_enabled", False):
            return False
        setting_name = _ALERT_SETTING_MAP.get(alert_type)
        if setting_name is None:
            return False
        return bool(getattr(self.settings, setting_name, False))

    async def send(self, alert_type: AlertType, text: str) -> bool:
        """Send one alert. Returns True on confirmed delivery, False otherwise."""
        if not self._is_enabled(alert_type):
            logger.debug("Telegram alert %s skipped (disabled in settings).", alert_type.value)
            return False

        token = getattr(self.settings, "telegram_bot_token", "")
        chat_id = getattr(self.settings, "telegram_chat_id", "")
        if not token or not chat_id:
            logger.warning("Telegram alert %s skipped: bot token or chat_id not configured.", alert_type.value)
            return False

        url = TELEGRAM_API_URL.format(token=token)
        payload = {"chat_id": chat_id, "text": text}

        try:
            if self._client is not None:
                response = await self._client.post(url, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(url, json=payload, timeout=10.0)
            response.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram alert %s failed to send: %s", alert_type.value, exc)
            return False
