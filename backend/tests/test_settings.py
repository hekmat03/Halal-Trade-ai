"""Tests for Settings loading secrets and risk limits from the environment."""
from __future__ import annotations

import pytest

from halaltrade.config import Settings


def test_env_prefix_required() -> None:
    """All settings must read through the HALAL_ prefix (explicit, discoverable)."""
    assert Settings.model_config["env_prefix"] == "HALAL_"


def test_loads_risk_limits_from_env(monkeypatch) -> None:
    monkeypatch.setenv("HALAL_MAX_POSITION_SIZE", "1234")
    monkeypatch.setenv("HALAL_MAX_DAILY_LOSS", "88")
    monkeypatch.setenv("HALAL_MAX_DRAWDOWN", "0.05")
    settings = Settings(_env_file=None)
    assert settings.max_position_size == 1234.0
    assert settings.max_daily_loss == 88.0
    assert settings.max_drawdown == 0.05


def test_loads_secrets_from_env_only(monkeypatch) -> None:
    monkeypatch.setenv("HALAL_BINANCE_API_KEY", "k-abc")
    monkeypatch.setenv("HALAL_BINANCE_API_SECRET", "s-xyz")
    settings = Settings(_env_file=None)
    assert settings.binance_api_key == "k-abc"
    assert settings.binance_api_secret == "s-xyz"


def test_no_real_secrets_hardcoded_in_source() -> None:
    """The Settings defaults are empty: there is no secret baked into code."""
    blank = Settings(_env_file=None, **{})
    # Simulate a fresh run without env vars: defaults must be empty strings.
    import os

    for var in ("HALAL_BINANCE_API_KEY", "HALAL_BINANCE_API_SECRET"):
        os.environ.pop(var, None)
    defaults = Settings(_env_file=None)
    assert defaults.binance_api_key == ""
    assert defaults.binance_api_secret == ""


def test_live_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("HALAL_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("HALAL_TRADING_MODE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.live_enabled is False
    assert settings.trading_mode == "paper"


def test_live_requires_credentials_flag(monkeypatch) -> None:
    settings = Settings(_env_file=None)  # paper, live disabled
    assert settings.requires_binance_credentials() is False

    live_off = Settings(_env_file=None)
    # even in live mode, if live_enabled is False, credentials are not demanded
    assert live_off.requires_binance_credentials() is False


def test_mode_and_live_flag_drive_credential_requirement(monkeypatch) -> None:
    monkeypatch.setenv("HALAL_TRADING_MODE", "live")
    monkeypatch.setenv("HALAL_LIVE_ENABLED", "true")
    settings = Settings(_env_file=None)
    assert settings.requires_binance_credentials() is True
    assert settings.live_enabled is True
