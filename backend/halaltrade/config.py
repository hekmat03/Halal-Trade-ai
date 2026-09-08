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
    # Live trading ships DISABLED by default. It requires explicit, deliberate
    # opt-in AND a valid, restricted credential set before the Security gate lets it run.
    live_enabled: bool = False
    # --- Secrets (environment only) ----------------------------------------------
    binance_api_key: str = ""
    binance_api_secret: str = ""
    # --- Security posture ---------------------------------------------------------
    # The system NEVER allows withdrawals. If this is ever set to False the
    # Security gate rejects and STOPS.
    api_key_restricted: bool = True
    # Credential/system health gate; False -> Security gate rejects and STOPS.
    system_healthy: bool = True
    # --- Risk Policy (hard user-configurable caps) --------------------------------
    max_position_size: float = 500.0      # max single-position notional (USDT)
    max_exposure: float = 2000.0          # max total exposure (USDT)
    max_loss_per_trade: float = 50.0      # max loss allowed on a single trade (USDT)
    max_daily_loss: float = 100.0         # max aggregate daily loss (USDT)
    max_drawdown: float = 0.10            # max allowed drawdown (fraction of peak)
    min_account_balance: float = 100.0    # below this the account cannot trade (USDT)
    # --- Execution validation -----------------------------------------------------
    min_notional: float = 5.0             # BTCUSDT Binance minimum order notional (USDT)
    max_data_age_seconds: float = 5.0     # reject any signal older than this
    # --- Market data (Delivery 2) -------------------------------------------------
    # Public endpoints — overridable via env (HALAL_*). Public market data needs
    # NO API key; both Binance and CoinGecko public endpoints are keyless.
    market_data_max_age_seconds: float = 5.0   # refuse data older than this
    binance_rest_base: str = "https://api.binance.com"
    binance_ws_base: str = "wss://stream.binance.com:9443"
    # --- Persistence --------------------------------------------------------------
    database_url: str = "sqlite:///halaltrade.db"  # SQLite for dev; Postgres later
    # ------------------------------------------------------------------------------
    def requires_binance_credentials(self) -> bool:
        """Whether this mode needs real, valid Binance credentials to trade."""
        return self.trading_mode == "live" and self.live_enabled

    def secrets_present(self) -> bool:
        return bool(self.binance_api_key and self.binance_api_secret)
