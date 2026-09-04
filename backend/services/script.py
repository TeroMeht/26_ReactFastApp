import subprocess
import sys
from pathlib import Path
from core.config import settings
import psutil
import logging

logger = logging.getLogger(__name__)





def _resolve_python_for(script_path: Path) -> str:
    """Return the python.exe that should run ``script_path``.

    Walk up from the script's directory looking for a ``.venv`` folder
    that contains ``Scripts/python.exe`` (Windows) or ``bin/python``
    (POSIX). If we find one, use it — that project's dependencies live
    there. Otherwise fall back to the interpreter running this backend
    (the backend's own .venv).
    """
    subpaths = (
        Path("Scripts") / "python.exe",   # Windows
        Path("bin") / "python",           # POSIX
    )

    current = script_path.resolve().parent
    for _ in range(5):  # script dir + up to 4 ancestors
        for sub in subpaths:
            candidate = current / ".venv" / sub
            if candidate.exists():
                return str(candidate)
        if current.parent == current:
            break
        current = current.parent

    return sys.executable


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

    # Pick the interpreter to run the target script with.
    #
    # Preference order:
    #   1. A .venv sitting next to the script (or in any parent up to 4
    #      levels above it) — that project owns its own dependencies,
    #      including editable installs of its own packages (e.g. the
    #      22_WatchlistStreamer project has data_sources/ registered in
    #      its own pyproject.toml). Using its venv makes those imports
    #      resolve.
    #   2. Fall back to THIS process's interpreter (sys.executable), i.e.
    #      the backend's own .venv, for scripts that don't ship a venv.
    python_exe = _resolve_python_for(script_path)
    logger.info("Starting script: %s (via %s)", script_path, python_exe)

    # Start in a new cmd window
    subprocess.Popen(
        [
            "cmd.exe",
            "/c",
            "start",
            "cmd.exe",
            "/k",
            python_exe,
            str(script_path),
        ],
        cwd=str(script_path.parent),
        shell=True,
    )

    return f"{script_path.name} started successfully."
