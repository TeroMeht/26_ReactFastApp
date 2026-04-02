from pathlib import Path
from typing import Dict, Optional
import aiofiles
from core.config import settings  # service pulls settings itself
import logging
logger = logging.getLogger(__name__)




BASE_PATH = Path(settings.INPUT_TICKERS_PATH)  # Assuming your path comes from settings

async def ensure_directory_exists():
    BASE_PATH.mkdir(parents=True, exist_ok=True)

async def get_tickers(filename: Optional[str] = None) -> dict:
    """
    Read tickers:
    - If filename is provided, return that file's content.
    - Otherwise, return all .txt files in the directory.
    """
    await ensure_directory_exists()

    if filename:
        file_path = BASE_PATH / filename
        if not file_path.exists():
            return {"error": "File not found", "filename": filename}
        return {"filename": filename, "content": file_path.read_text(encoding="utf-8")}

    # Read all .txt files
    files_content = {}
    for file in BASE_PATH.glob("*.txt"):
        files_content[file.name] = file.read_text(encoding="utf-8")
    return files_content

async def save_tickers(filename: str, content: str) -> dict:
    """
    Save content to a file in the tickers path.
    """
    await ensure_directory_exists()
    file_path = BASE_PATH / filename
    async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
        await f.write(content)
    logger.info(f"Saved ticker file: {filename}")
    return {"success": True, "filename": filename}



async def add_tickers1(filename: str, content: str) -> dict:
    """
    Append a ticker to a file, ensuring comma separation.
    """
    await ensure_directory_exists()
    file_path = BASE_PATH / filename

    # Read existing content to check if file has anything
    existing = ""
    if file_path.exists():
        async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
            existing = (await f.read()).strip()

    # Build new content — comma separated, no duplicates
    existing_symbols = [s.strip() for s in existing.split(",") if s.strip()]
    if content.strip() in existing_symbols:
        return {"success": True, "filename": filename, "duplicate": True}

    new_content = ",".join(existing_symbols + [content.strip()])

    async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
        await f.write(new_content)

    logger.info(f"Added ticker {content.strip()} to file: {filename}")
    return {"success": True, "filename": filename}