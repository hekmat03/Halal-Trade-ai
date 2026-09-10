"""Tests for PrayerTimeGate and parse_prayer_windows.

Requires the full halaltrade package (pydantic etc.) to import, like every
other test in this suite — run with the project's real environment.
"""
from __future__ import annotations

from datetime import time

import pytest

from halaltrade.gates.base import Context
from halaltrade.gates.prayer_time import PrayerTimeGate, parse_prayer_windows
from halaltrade.models import Side

from conftest import make_signal


def test_parse_empty_string_returns_no_windows() -> None:
    assert parse_prayer_windows("") == []
    assert parse_prayer_windows("   ") == []


def test_parse_single_window() -> None:
    windows = parse_prayer_windows("04:30-04:50")
    assert windows == [(time(4, 30), time(4, 50))]


def test_parse_multiple_windows() -> None:
    windows = parse_prayer_windows("04:30-04:50,12:15-12:35")
    assert windows == [(time(4, 30), time(4, 50)), (time(12, 15), time(12, 35))]


def test_parse_malformed_raises() -> None:
    with pytest.raises(ValueError):
        parse_prayer_windows("not-a-window")
    with pytest.raises(ValueError):
        parse_prayer_windows("04:30")


def test_gate_is_noop_when_disabled(context) -> None:
    context.settings.pause_during_prayer_times = False
    signal = make_signal(side=Side.BUY)
    result = PrayerTimeGate().evaluate(signal, context)
    assert result.passed is True


def test_gate_blocks_inside_window(context) -> None:
    context.settings.pause_during_prayer_times = True
    context.settings.prayer_windows_utc = "12:00-12:30"
    context.now = lambda: __import__("datetime").datetime(2026, 9, 9, 12, 15)
    signal = make_signal(side=Side.BUY)
    result = PrayerTimeGate().evaluate(signal, context)
    assert result.passed is False


def test_gate_allows_outside_window(context) -> None:
    context.settings.pause_during_prayer_times = True
    context.settings.prayer_windows_utc = "12:00-12:30"
    context.now = lambda: __import__("datetime").datetime(2026, 9, 9, 15, 0)
    signal = make_signal(side=Side.BUY)
    result = PrayerTimeGate().evaluate(signal, context)
    assert result.passed is True


def test_gate_always_allows_hold(context) -> None:
    context.settings.pause_during_prayer_times = True
    context.settings.prayer_windows_utc = "00:00-23:59"
    signal = make_signal(side=Side.HOLD)
    result = PrayerTimeGate().evaluate(signal, context)
    assert result.passed is True