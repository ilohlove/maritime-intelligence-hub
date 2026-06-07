import requests
from pathlib import Path

from app.logger import logger
from app.version_checker import is_newer_version


def get_update_status(current_version, latest_json_url):
    try:
        response = requests.get(latest_json_url, timeout=10)
        response.raise_for_status()
        latest_data = response.json()

        latest_version = latest_data.get("version")
        if not latest_version:
            message = "Remote latest.json has no version"
            logger.warning(message)
            return {"status": "invalid", "message": message}

        if not is_newer_version(current_version, latest_version):
            return {
                "status": "current",
                "message": f"Current version {current_version} is up to date",
                "latest": latest_data,
            }

        if not latest_data.get("download_url"):
            message = f"Update {latest_version} skipped: missing download_url"
            logger.warning(message)
            return {"status": "missing_download", "message": message, "latest": latest_data}

        return {
            "status": "available",
            "message": f"Update available: {latest_version}",
            "latest": latest_data,
        }
    except Exception as exc:
        message = f"Update check failed: {exc}"
        logger.warning(message)
        return {"status": "error", "message": message}


def check_update(current_version, latest_json_url):
    result = get_update_status(current_version, latest_json_url)
    if result.get("status") == "available":
        return result.get("latest")
    return None


def download_update(download_url, destination_dir, filename):
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(exist_ok=True)
    destination_path = destination_dir / filename
    temporary_path = destination_path.with_suffix(f"{destination_path.suffix}.download")

    try:
        with requests.get(download_url, stream=True, timeout=30) as response:
            response.raise_for_status()
            with open(temporary_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)

        temporary_path.replace(destination_path)
        logger.info("Downloaded update to %s", destination_path)
        return destination_path
    except Exception as exc:
        if temporary_path.exists():
            temporary_path.unlink()
        logger.warning("Update download failed: %s", exc)
        raise
