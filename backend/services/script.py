import subprocess
from pathlib import Path
from core.config import settings

import logging

logger = logging.getLogger(__name__)

# psutil is used to discover and stop a previously-launched instance of the
# target script. It's a soft dependency: if it isn't installed we just skip
# the cleanup step and behave the same way the original implementation did.
try:
    import psutil
except ImportError:  # pragma: no cover - optional dep
    psutil = None
    logger.warning(
        "psutil is not installed; ScriptService cannot stop a previous "
        "script instance before starting a new one. "
        "Install psutil to enable that cleanup."
    )


class ScriptService:
    def __init__(self):
        self.script_dir = Path(settings.SCRIPT_DIR)
        self.target_script = settings.TARGET_SCRIPT

    def _stop_existing(self, script_path: Path) -> int:
        """Stop any python process already running ``script_path``.

        We launch the script via ``cmd.exe /c start cmd.exe /k python <script>``,
        which leaves a process tree of:

            cmd.exe (the visible terminal kept alive by /k)
              python.exe (the script itself)

        We find python.exe by matching the *absolute* script path against the
        process's command line so we don't disturb unrelated python processes
        (e.g. this very FastAPI server).  We then terminate both the python
        process and the cmd.exe shell that owns its window — otherwise the
        terminal lingers on screen after the script dies.

        Returns the number of script instances stopped.  Always returns 0 if
        psutil isn't available.
        """
        if psutil is None:
            return 0

        target = str(script_path).lower()
        stopped = 0

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if "python" not in name:
                    continue
                cmdline = proc.info.get("cmdline") or []
                if target not in " ".join(cmdline).lower():
                    continue

                # Walk up the parent chain collecting cmd.exe ancestors so we
                # also close the terminal window that wraps this python child.
                # We stop at the first non-cmd ancestor to avoid taking down
                # this server (which is the cmd's own grandparent).
                victims = [proc]
                try:
                    for ancestor in proc.parents():
                        if (ancestor.name() or "").lower() == "cmd.exe":
                            victims.append(ancestor)
                        else:
                            break
                except psutil.Error:
                    pass

                pids = [p.pid for p in victims]
                logger.info(
                    "Stopping previous %s instance: pids=%s",
                    self.target_script,
                    pids,
                )

                for victim in victims:
                    try:
                        victim.terminate()
                    except psutil.NoSuchProcess:
                        continue

                _gone, alive = psutil.wait_procs(victims, timeout=3)
                for victim in alive:
                    logger.warning(
                        "pid=%s did not exit after terminate(); killing",
                        victim.pid,
                    )
                    try:
                        victim.kill()
                    except psutil.NoSuchProcess:
                        pass

                stopped += 1

            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                # Process disappeared mid-iteration or we lack rights — log
                # and move on so a single problem PID doesn't abort the sweep.
                logger.debug(
                    "Skipping pid=%s while looking for previous %s: %s",
                    getattr(proc, "pid", "?"),
                    self.target_script,
                    exc,
                )
                continue

        return stopped

    def run_script(self) -> str:
        if not self.script_dir.exists():
            raise FileNotFoundError(f"Script directory not found: {self.script_dir}")

        script_path = self.script_dir / self.target_script
        if not script_path.exists():
            raise FileNotFoundError(f"Target script not found: {script_path}")

        # Stop any prior instance first so we never end up with two scripts
        # (and two terminal windows) running at the same time.
        stopped = self._stop_existing(script_path)
        if stopped:
            logger.info(
                "Stopped %d previous instance(s) of %s before restart",
                stopped,
                self.target_script,
            )
        else:
            logger.debug("No previous instance of %s found", self.target_script)

        logger.info(f"Starting script: {self.target_script} in {self.script_dir}")

        # --- Windows: start a new cmd window ---
        subprocess.Popen(
            ['cmd.exe', '/c', 'start', 'cmd.exe', '/k', 'python', str(script_path)],
            cwd=str(self.script_dir),
            shell=True
        )

        return f"{self.target_script} started successfully."
