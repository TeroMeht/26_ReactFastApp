import subprocess
from pathlib import Path
from core.config import settings

import logging

logger = logging.getLogger(__name__)

class ScriptService:
    def __init__(self):
        self.script_dir = Path(settings.SCRIPT_DIR)
        self.target_script = settings.TARGET_SCRIPT

    def run_script(self) -> str:
        if not self.script_dir.exists():
            raise FileNotFoundError(f"Script directory not found: {self.script_dir}")

        script_path = self.script_dir / self.target_script
        if not script_path.exists():
            raise FileNotFoundError(f"Target script not found: {script_path}")

        logger.info(f"Starting script: {self.target_script} in {self.script_dir}")

        # --- Windows: start a new cmd window ---
        subprocess.Popen(
            ['cmd.exe', '/c', 'start', 'cmd.exe', '/k', 'python', str(script_path)],
            cwd=str(self.script_dir),
            shell=True
        )

        return f"{self.target_script} started successfully."