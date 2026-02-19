from typing import List
from pydantic_settings import BaseSettings
from pydantic import field_validator
import os
from pathlib import Path

ENV_REPO = Path("C:/Projects/env_repo")


class Settings(BaseSettings):
    API_PREFIX: str = "/api"
    DEBUG: bool = False

    DATABASE_URL: str = None

    ALLOWED_ORIGINS: str

    OPENAI_API_KEY: str

    # --- IBKR settings ---
    IB_HOST: str
    IB_PORT: int
    IB_CLIENT_ID: int

    # --- Folder Paths ---
    INPUT_TICKERS_PATH: Path

    SCRIPT_DIR: str  # folder where scripts live
    TARGET_SCRIPT: str  # script filename to run

    # --- Strategy parameters ---
    RISK: int  # Default risk per trade in dollars
    MAX_ENTRY_FREQUENCY_MINUTES: int

    # --- Alpaca API Config ---
    ALPACA_API_KEY: str
    ALPACA_API_SECRET: str
    ALPACA_BASE_URL: str


    def __init__(self, **values):
        super().__init__(**values)
        if not self.DEBUG:
            db_user = os.getenv("DB_USER")
            db_password = os.getenv("DB_PASSWORD")
            db_host = os.getenv("DB_HOST")
            db_port = os.getenv("DB_PORT")
            db_name = os.getenv("DB_NAME")
            self.DATABASE_URL = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    @field_validator("ALLOWED_ORIGINS")
    def parse_allowed_origins(cls, v: str) -> List[str]:
        return v.split(",") if v else []
    
    # Creates the input tickers folder if it doesn't exist and resolves the path
    @field_validator("INPUT_TICKERS_PATH")
    def validate_path(cls, v: Path) -> Path:
        # Expand things like ~/data
        v = v.expanduser().resolve()

        # Create directory if it doesn't exist
        v.mkdir(parents=True, exist_ok=True)

        return v
    
    class Config:
        env_file = ENV_REPO / "26_ReactFastApp.env" # centralized project configs
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()