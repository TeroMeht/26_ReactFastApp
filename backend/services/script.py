import subprocess
from pathlib import Path
from core.config import settings
import psutil
import logging

logger = logging.getLogger(__name__)





def _stop_existing(script_path: Path) -> int:
    """Stop any python process already running ``script_path``."""

    target = str(script_path.resolve()).lower()
    stopped = 0

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "python" not in name:
                continue

            cmdline = proc.info.get("cmdline") or []
            if target not in " ".join(cmdline).lower():
                continue

            # Collect cmd.exe ancestors so the terminal window closes too
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
                script_path.name,
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
            logger.debug(
                "Skipping pid=%s while looking for previous %s: %s",
                getattr(proc, "pid", "?"),
                script_path.name,
                exc,
            )
            continue

    return stopped



def run_script() -> str:
    script_path = settings.TARGET_SCRIPT_PATH

    stopped = _stop_existing(script_path)

    if stopped:
        logger.info(
            "Stopped %d previous instance(s) of %s before restart",
            stopped,
            script_path.name,
        )
    else:
        logger.debug("No previous instance of %s found", script_path.name)

    logger.info("Starting script: %s", script_path)

    # Start in a new cmd window
    subprocess.Popen(
        [
            "cmd.exe",
            "/c",
            "start",
            "cmd.exe",
            "/k",
            "python",
            str(script_path),
        ],
        cwd=str(script_path.parent),
        shell=True,
    )

    return f"{script_path.name} started successfully."
