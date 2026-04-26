from typing import List
from pydantic_settings import BaseSettings
from pydantic import field_validator
import os
from pathlib import Path

ENV_REPO = Path("C:/codebase/env-repo")


class Settings(BaseSettings):
    
    DATABASE_URL: str


    API_PREFIX: str = "/api"
    DEBUG: bool = False
    ALLOWED_ORIGINS: str


    # --- IBKR settings ---
    IB_HOST: str
    IB_PORT: int
    IB_CLIENT_ID: int

    # --- Folder Paths ---
    INPUT_TICKERS_PATH: Path
    SCRIPT_DIR: Path
    TARGET_SCRIPT: str

    # --- Strategy parameters ---
    RISK: int
    MAX_ENTRY_FREQUENCY_MINUTES: int

    # Block entry for time period
    BLOCK_START_HOUR:int
    BLOCK_START_MINUTE:int
    BLOCK_END_HOUR:int
    BLOCK_END_MINUTE:int


    # Daily loss limit
    MAX_DAILY_LOSS: int

    # --- Exit triggers ---
    EXIT_TRIGGERS: set[str]


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