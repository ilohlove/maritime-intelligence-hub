import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import ROOT_DIR
from app.logger import logger


OUTPUT_RETENTION_DAYS = 2
TEMP_RETENTION_DAYS = 2

VISUAL_RUN_DIR_PATTERN = re.compile(r"^\d{8}_\d{6}$")
PRESERVED_OUTPUT_NAMES = {"latest_brief.md", "latest_brief.json", ".gitkeep"}


def cleanup_runtime_artifacts(
    root_dir=ROOT_DIR,
    output_retention_days=OUTPUT_RETENTION_DAYS,
    temp_retention_days=TEMP_RETENTION_DAYS,
):
    root = Path(root_dir).resolve()
    removed = {
        "output_files": 0,
        "output_dirs": 0,
        "temp_files": 0,
        "temp_dirs": 0,
    }
    removed.update(_cleanup_output(root, output_retention_days))
    removed.update(_cleanup_temp(root, temp_retention_days))
    logger.info("Runtime cleanup finished: %s", removed)
    return removed


def cleanup_update_artifacts(root_dir=ROOT_DIR, exe_name=None):
    root = Path(root_dir).resolve()
    exe_name = exe_name or _load_exe_name()
    exe_path = Path(exe_name)
    exe_filename = exe_path.name
    backup_filename = f"{exe_path.stem}.backup{exe_path.suffix}"

    candidates = [
        root / "temp" / exe_filename,
        root / "temp" / f"{exe_filename}.download",
        root / "backup" / backup_filename,
    ]

    removed = []
    for path in candidates:
        if _remove_path(path, root):
            removed.append(str(path))

    if removed:
        logger.info("Update cleanup removed: %s", removed)
    return removed


def _cleanup_output(root, retention_days):
    output_root = root / "output"
    cutoff = _cutoff(retention_days)
    counts = {"output_files": 0, "output_dirs": 0}

    briefs_dir = output_root / "briefs"
    if briefs_dir.exists():
        for path in briefs_dir.iterdir():
            if path.name in PRESERVED_OUTPUT_NAMES:
                continue
            if path.is_file() and _is_older_than(path, cutoff) and _remove_path(path, root):
                counts["output_files"] += 1

    visual_root = output_root / "visual_briefs"
    if visual_root.exists():
        for path in visual_root.rglob("*"):
            if not path.is_dir() or not VISUAL_RUN_DIR_PATTERN.match(path.name):
                continue
            if _is_older_than(path, cutoff) and _remove_path(path, root):
                counts["output_dirs"] += 1

    if output_root.exists():
        for path in output_root.iterdir():
            if path.name in {"briefs", "visual_briefs", ".gitkeep"}:
                continue
            if path.is_file() and _is_older_than(path, cutoff) and _remove_path(path, root):
                counts["output_files"] += 1

    return counts


def _cleanup_temp(root, retention_days):
    temp_root = root / "temp"
    cutoff = _cutoff(retention_days)
    counts = {"temp_files": 0, "temp_dirs": 0}
    if not temp_root.exists():
        return counts

    for path in sorted(temp_root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.name == ".gitkeep":
            continue
        if path.is_file() and _is_older_than(path, cutoff) and _remove_path(path, root):
            counts["temp_files"] += 1
        elif path.is_dir() and _is_empty_dir(path) and _remove_path(path, root):
            counts["temp_dirs"] += 1

    return counts


def _load_exe_name():
    try:
        from app.config import load_version

        metadata = load_version()
        return metadata.get("exe_name", "BV-App.exe")
    except Exception as exc:
        logger.warning("Could not load exe name for update cleanup: %s", exc)
        return "BV-App.exe"


def _cutoff(days):
    return datetime.now(timezone.utc) - timedelta(days=max(0, int(days or 0)))


def _is_older_than(path, cutoff):
    try:
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return False
    return modified_at < cutoff


def _is_empty_dir(path):
    try:
        return path.is_dir() and not any(path.iterdir())
    except OSError:
        return False


def _remove_path(path, root):
    path = Path(path).resolve()
    if not _is_inside(path, root):
        logger.warning("Cleanup skipped path outside app root: %s", path)
        return False
    if path == root or not path.exists():
        return False
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except OSError as exc:
        logger.warning("Cleanup could not remove %s: %s", path, exc)
        return False


def _is_inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
