"""Tests for halaltrade.notifications.telegram formatting functions.

These test ONLY the pure, network-free message formatting. The actual send
(TelegramNotifier.send) requires httpx + a real bot token and is NOT covered
here — see the module docstring.
"""
from __future__ import annotations

import unittest

from halaltrade.notifications.telegram import (
    format_daily_summary,
    format_rejection_alert,
    format_system_alert,
    format_trade_alert,
)


class TestFormatTradeAlert(unittest.TestCase):
    def test_basic_buy_alert(self) -> None:
        msg = format_trade_alert("BUY", "BTCUSDT", 0.01, 65000.0)
        self.assertIn("BUY", msg)
        self.assertIn("BTCUSDT", msg)
        self.assertIn("65,000.00", msg)

    def test_includes_stop_loss_and_take_profit_when_given(self) -> None:
        msg = format_trade_alert("BUY", "BTCUSDT", 0.01, 65000.0, stop_loss=63000.0, take_profit=68000.0)
        self.assertIn("Stop-loss", msg)
        self.assertIn("63,000.00", msg)
        self.assertIn("Take-profit", msg)
        self.assertIn("68,000.00", msg)

    def test_omits_stop_loss_when_not_given(self) -> None:
        msg = format_trade_alert("SELL", "BTCUSDT", 0.01, 65000.0)
        self.assertNotIn("Stop-loss", msg)

    def test_includes_reason_when_given(self) -> None:
        msg = format_trade_alert("BUY", "BTCUSDT", 0.01, 65000.0, reason="EMA cross")
        self.assertIn("EMA cross", msg)


class TestFormatRejectionAlert(unittest.TestCase):
    def test_risk_rejection_labeled_correctly(self) -> None:
        msg = format_rejection_alert("RiskGate", ["max drawdown exceeded"])
        self.assertIn("Risk Rejection", msg)
        self.assertIn("max drawdown exceeded", msg)

    def test_shariah_rejection_labeled_correctly(self) -> None:
        msg = format_rejection_alert("ShariahGate", ["margin trading not allowed"], is_shariah=True)
        self.assertIn("Shariah Rejection", msg)

    def test_caps_reason_list_to_avoid_flooding(self) -> None:
        reasons = [f"reason {i}" for i in range(10)]
        msg = format_rejection_alert("RiskGate", reasons)
        self.assertIn("and 5 more", msg)
        self.assertIn("reason 0", msg)
        self.assertIn("reason 4", msg)
        self.assertNotIn("reason 5", msg)

    def test_empty_reasons_does_not_crash(self) -> None:
        msg = format_rejection_alert("RiskGate", [])
        self.assertIn("Risk Rejection", msg)


class TestFormatSystemAlert(unittest.TestCase):
    def test_basic_system_error(self) -> None:
        msg = format_system_alert("system_error", "Database connection lost")
        self.assertIn("System Error", msg)
        self.assertIn("Database connection lost", msg)

    def test_emergency_stop_uses_distinct_icon(self) -> None:
        msg = format_system_alert("emergency_stop", "Kill switch activated by user")
        self.assertIn("\U0001F6A8", msg)
        self.assertIn("Kill switch activated by user", msg)


class TestFormatDailySummary(unittest.TestCase):
    def test_positive_day(self) -> None:
        msg = format_daily_summary(
            date="2026-09-14", starting_equity=5000.0, ending_equity=5100.0,
            trades=5, wins=3, losses=2, realized_pnl=100.0,
        )
        self.assertIn("5,000.00", msg)
        self.assertIn("5,100.00", msg)
        self.assertIn("+2.00%", msg)
        self.assertIn("wins: 3", msg)
        self.assertIn("losses: 2", msg)
        self.assertIn("+100.00", msg)

    def test_negative_day(self) -> None:
        msg = format_daily_summary(
            date="2026-09-14", starting_equity=5000.0, ending_equity=4900.0,
            trades=3, wins=1, losses=2, realized_pnl=-100.0,
        )
        self.assertIn("-2.00%", msg)
        self.assertIn("-100.00", msg)

    def test_zero_starting_equity_does_not_divide_by_zero(self) -> None:
        msg = format_daily_summary(
            date="2026-09-14", starting_equity=0.0, ending_equity=0.0,
            trades=0, wins=0, losses=0, realized_pnl=0.0,
        )
        self.assertIn("0.00%", msg)


if __name__ == "__main__":
    unittest.main()
