import requests

from app.logger import logger
from app.version_checker import is_newer_version


def check_update(current_version, latest_json_url):
    try:
        response = requests.get(latest_json_url, timeout=10)
        response.raise_for_status()
        latest_data = response.json()

        latest_version = latest_data.get("version")
        if not latest_version:
            logger.warning("Remote latest.json has no version")
            return None

        if not is_newer_version(current_version, latest_version):
            return None

        if not latest_data.get("download_url"):
            logger.warning("Update %s skipped: missing download_url", latest_version)
            return None

        return latest_data
    except Exception as exc:
        logger.warning("Update check failed: %s", exc)
        return None
