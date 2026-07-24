from typing import List
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    
    DATABASE_URL: str

    # --- Interactive Brokers ---
    IB_HOST: str
    IB_PORT: int
    IB_CLIENT_ID: int

    API_PREFIX: str
    ALLOWED_ORIGINS: str


    # --- Folder Paths ---
    # INPUT_TICKERS_PATH removed: the watchlist now lives in the `watchlist` /
    # `watchlist_strategies` tables (livestreaming DB), written by the new
    # /api/watchlist endpoints. The legacy ticker .txt flow is gone.
    SCRIPT_DIR: Path
    TARGET_SCRIPT: str

    # --- Strategy parameters ---
    RISK: int

    MAX_ADD_FREQUENCY_MINUTES: int
    MAX_ENTRY_FREQUENCY_MINUTES: int
    MAX_ATTEMPTS_PER_SYMBOL_PER_DAY: int
    MAX_TOTAL_ENTRIES_PER_DAY: int # Hard cap on total entries across all tickers in one day.

    # No entries allowed before this time of day (Helsinki).
    FIRST_ENTRY_HOUR: int
    FIRST_ENTRY_MINUTE: int


    # Daily loss limit
    MAX_DAILY_LOSS: int

    # --- Consecutive-loss escalating lockout ---
    CONSECUTIVE_LOSS_TIER1_COUNT: int
    CONSECUTIVE_LOSS_TIER1_MINUTES: int
    CONSECUTIVE_LOSS_TIER2_COUNT: int
    CONSECUTIVE_LOSS_TIER2_MINUTES: int


    # --- Alpaca API Config ---
    ALPACA_API_KEY: str
    ALPACA_API_SECRET: str
    ALPACA_BASE_URL: str

    # --- Anthropic (news summarization for daily premarket summary) ---
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str


    @field_validator("ALLOWED_ORIGINS")
    def parse_allowed_origins(cls, v: str) -> List[str]:
        return v.split(",") if v else []

    class Config:
        ENV_REPO = Path("C:/codebase/env-repo")
        env_file = ENV_REPO / "26_ReactFastApp.env" # centralized project configs
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()