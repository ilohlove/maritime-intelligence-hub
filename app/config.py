import json
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
VERSION_FILE = RESOURCE_DIR / "version.json"
LATEST_FILE = RESOURCE_DIR / "latest.json"


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
