import json
import os
import shutil
import sys
from pathlib import Path


def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_resource_dir():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return get_app_dir()


ROOT_DIR = get_app_dir()
RESOURCE_DIR = get_resource_dir()
if getattr(sys, "frozen", False):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    os.environ.setdefault("TCL_LIBRARY", str(RESOURCE_DIR / "_tcl_data"))
    os.environ.setdefault("TK_LIBRARY", str(RESOURCE_DIR / "_tk_data"))
VERSION_FILE = RESOURCE_DIR / "version.json"
LATEST_FILE = RESOURCE_DIR / "latest.json"


def ensure_runtime_seed(filename):
    """Return a usable mutable data file, seeding it beside a frozen app when possible."""
    runtime_path = ROOT_DIR / filename
    if runtime_path.exists():
        return runtime_path

    resource_path = RESOURCE_DIR / filename
    if not resource_path.exists() or resource_path == runtime_path:
        return runtime_path

    try:
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resource_path, runtime_path)
        return runtime_path
    except OSError:
        # Read-only install locations can still consume the bundled seed.
        return resource_path


def validate_runtime_seeds(filenames=("NEWS_SOURCE_MASTER.csv", "BACKUP_FEED_MASTER.csv")):
    paths = {filename: ensure_runtime_seed(filename) for filename in filenames}
    missing = [filename for filename, path in paths.items() if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required runtime seed files: " + ", ".join(missing))
    return paths


def load_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def load_version():
    return load_json(VERSION_FILE)


def load_latest():
    return load_json(LATEST_FILE)


def get_latest_json_url(metadata=None):
    data = metadata if metadata is not None else load_version()
    return data.get("latest_json_url", "")
