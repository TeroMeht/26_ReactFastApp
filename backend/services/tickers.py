from pathlib import Path
from typing import Dict, Optional
from core.config import settings  # service pulls settings itself
import logging
logger = logging.getLogger(__name__)


class InputTickerService:
    """
    Service for managing input ticker files.
    Decoupled from FastAPI; reads settings internally.
    """

    def __init__(self):
        # Pull base path from settings internally
        self.base_path: Path = Path(settings.INPUT_TICKERS_PATH)

    # -------------------------
    # Public Methods
    # -------------------------

    def get_tickers(self, filename: Optional[str] = None) -> dict:
        """
        If filename is provided -> return single file.
        Otherwise -> return all .txt files.
        """
        self._ensure_directory_exists()

        if filename:
            return self._read_single_file(filename)

        return self._read_all_files()

    def save_tickers(self, filename: str, content: str) -> dict:
        """
        Save content to a file.
        """
        self._ensure_directory_exists()

        file_path = self.base_path / filename
        file_path.write_text(content, encoding="utf-8")

        return {
            "success": True,
            "filename": filename
        }

    # -------------------------
    # Internal Helpers
    # -------------------------

    def _ensure_directory_exists(self) -> None:
        if not self.base_path.exists() or not self.base_path.is_dir():
            raise FileNotFoundError(
                f"Base path not found or not a directory: {self.base_path}"
            )

    def _read_single_file(self, filename: str) -> dict:
        file_path = self.base_path / filename

        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = file_path.read_text(encoding="utf-8")

        return {
            "filename": filename,
            "content": content
        }

    def _read_all_files(self) -> dict:
        files: Dict[str, str] = {}

        for file in self.base_path.glob("*.txt"):
            files[file.name] = file.read_text(encoding="utf-8")

        return {"files": files}