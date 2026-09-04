from typing import List, Optional
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


    # --- Script Path ---
    TARGET_SCRIPT_PATH: Path



    # --- Alpaca API Config ---
    ALPACA_API_KEY: str
    ALPACA_API_SECRET: str
    ALPACA_BASE_URL: str

    # --- Anthropic (news summarization for daily premarket summary) ---
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str

    # --- Timezone ---
    TIMEZONE:str

    # --- Telegram (notifications for automatic exits) ---
    # Optional: absence disables notifications; send helper no-ops with a warning.
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str


    STOP_LIMIT_OFFSET: float



    @field_validator("ALLOWED_ORIGINS")
    def parse_allowed_origins(cls, v: str) -> List[str]:
        return v.split(",") if v else []

    class Config:
        ENV_REPO = Path("C:/codebase/env-repo")
        env_file = ENV_REPO / "26_ReactFastApp.env" # centralized project configs
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()