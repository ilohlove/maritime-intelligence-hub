import subprocess
from pathlib import Path

from app.config import ROOT_DIR, load_version
from app.logger import logger


def launch_updater(new_exe_path, app_exe_path=None):
    metadata = load_version()
    updater_path = ROOT_DIR / metadata.get("updater_name", "BV-Updater.exe")
    target_path = Path(app_exe_path) if app_exe_path else ROOT_DIR / metadata.get("exe_name", "BV-App.exe")

    command = [
        str(updater_path),
        "--target",
        str(target_path),
        "--new",
        str(new_exe_path),
    ]

    logger.info("Launching updater: %s", command)
    subprocess.Popen(command, close_fds=True)
