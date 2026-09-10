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
    max_open_positions: int = 1           # max concurrent open positions (single-asset bot)
    qty_step: float = 0.00001             # BTCUSDT minimum quantity step (LOT_SIZE)
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
    # --- Prayer-time pause (optional, user-configurable; see gates/prayer_time.py) -
    pause_during_prayer_times: bool = False
    # Comma-separated "HH:MM-HH:MM" UTC windows, e.g. "04:30-04:50,12:15-12:35".
    # The system does NOT calculate prayer times itself — the user supplies the
    # exact UTC windows for their location/method/madhhab.
    prayer_windows_utc: str = ""
    # --- Zakat (optional, user-configurable; see zakat.py) -------------------------
    zakat_enabled: bool = False
    # Nisab threshold in USDT below which no Zakat is due. The user is
    # responsible for setting this to the correct current nisab value (e.g.
    # based on gold/silver price) — this system does not calculate nisab.
    zakat_nisab_usdt: float = 0.0
    zakat_rate: float = 0.025  # standard 2.5%
    # --- AI research layer (optional; ANALYSIS/RECOMMENDATION ONLY — see
    #     research/mistral_client.py. The LLM can never execute a trade.) -----
    mistral_api_key: str = ""
    mistral_model: str = "mistral-small"
    mistral_temperature: float = 0.2
    mistral_max_tokens: int = 1024
    mistral_timeout_seconds: float = 10.0
    mistral_rate_limit_per_minute: int = 10
    # ------------------------------------------------------------------------------
    def requires_binance_credentials(self) -> bool:
        """Whether this mode needs real, valid Binance credentials to trade."""
        return self.trading_mode == "live" and self.live_enabled

    def secrets_present(self) -> bool:
        return bool(self.binance_api_key and self.binance_api_secret)
