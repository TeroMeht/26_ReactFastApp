from pathlib import Path
from pydantic_settings import BaseSettings


class RiskManagerSettings(BaseSettings):

    # --- Strategy parameters ---
    RISK: int

    MAX_ADD_FREQUENCY_MINUTES: int
    MAX_ENTRY_FREQUENCY_MINUTES: int
    MAX_ATTEMPTS_PER_SYMBOL_PER_DAY: int
    MAX_TOTAL_ENTRIES_PER_DAY: int  # Hard cap on total entries across all tickers in one day.

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


    class Config:
        ENV_REPO = Path("C:/codebase/env-repo")
        env_file = ENV_REPO / "26_risk_manager.env" # centralized project configs
        env_file_encoding = "utf-8"
        case_sensitive = True
        frozen = True  # immutable after load: no mid-session mutation


risk_settings = RiskManagerSettings()
