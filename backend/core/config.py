from typing import List, Optional
from pathlib import Path
from pydantic import field_validator
from pydantic import model_validator
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

    # --- Extended-hours (pre-market) protective stop ---
    # Global default for whether new bracket orders should use a
    # conditional-LMT protective leg (fires from pre-market prints) instead
    # of a native STP (RTH-only trigger on US stocks). Per-Order override
    # via Order.pre_market_stop still wins.
    EXTENDED_HOURS_STOP_ENABLED: bool
    # Absolute-dollar offset worse-than-trigger to use as the limit price
    # when the conditional-LMT protective leg fires. e.g. 0.10 -> long stop
    # at 95 transmits a SELL LMT @ 94.90 once price reaches 95.
    STOP_LIMIT_OFFSET: float


    @field_validator("TARGET_SCRIPT_PATH")
    @classmethod
    def validate_target_script_path(cls, v: Path) -> Path:
        if not v.exists():
            raise ValueError(f"TARGET_SCRIPT_PATH does not exist: {v}")

        if not v.is_file():
            raise ValueError(f"TARGET_SCRIPT_PATH is not a file: {v}")

        return v.resolve()


    @field_validator("ALLOWED_ORIGINS")
    def parse_allowed_origins(cls, v: str) -> List[str]:
        return v.split(",") if v else []

    class Config:
        ENV_REPO = Path("C:/codebase/env-repo")
        env_file = ENV_REPO / "26_ReactFastApp.env" # centralized project configs
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()