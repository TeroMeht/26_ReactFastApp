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
    SCRIPT_DIR: Path
    TARGET_SCRIPT: str


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