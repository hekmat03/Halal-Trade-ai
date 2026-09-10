"""Application settings.
Everything here is configurable through environment variables (prefix ``HALAL_``)
or an optional ``.env`` file. **Secrets and API keys come from the environment
ONLY** — they are never hardcoded in code, never committed, and never appear in
any source file. See ``.env.example`` for the full list of required variables.
None of these values are trade secrets in the source; real credentials are injected
at runtime via the environment.
"""
from __future__ import annotations
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HALAL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Runtime / mode ----------------------------------------------------------
    trading_mode: Literal["backtest", "paper", "live"] = "paper"

    live_enabled: bool = False

    # --- Secrets (environment only) ----------------------------------------------
    binance_api_key: str = ""
    binance_api_secret: str = ""

    # --- Security posture ---------------------------------------------------------
    api_key_restricted: bool = True
    system_healthy: bool = True

    # --- Risk Policy --------------------------------------------------------------
    max_position_size: float = 500.0
    max_exposure: float = 2000.0
    max_loss_per_trade: float = 50.0
    max_daily_loss: float = 100.0
    max_drawdown: float = 0.10
    min_account_balance: float = 100.0
    max_open_positions: int = 1
    qty_step: float = 0.00001

    # --- Execution validation -----------------------------------------------------
    min_notional: float = 5.0
    max_data_age_seconds: float = 5.0

    # --- Market data --------------------------------------------------------------
    market_data_max_age_seconds: float = 5.0
    binance_rest_base: str = "https://api.binance.com"
    binance_ws_base: str = "wss://stream.binance.com:9443"

    # --- Persistence --------------------------------------------------------------
    database_url: str = "sqlite:///halaltrade.db"

    # --- Prayer-time pause --------------------------------------------------------
    pause_during_prayer_times: bool = False
    prayer_windows_utc: str = ""

    # --- Zakat --------------------------------------------------------------------
    zakat_enabled: bool = False
    zakat_nisab_usdt: float = 0.0
    zakat_rate: float = 0.025

    # ------------------------------------------------------------------------------
    def requires_binance_credentials(self) -> bool:
        """Whether this mode needs real, valid Binance credentials to trade."""
        return self.trading_mode == "live" and self.live_enabled

    def secrets_present(self) -> bool:
        return bool(self.binance_api_key and self.binance_api_secret)