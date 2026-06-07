from packaging.version import InvalidVersion, Version


def is_newer_version(current_version, latest_version):
    try:
        return Version(latest_version) > Version(current_version)
    except InvalidVersion:
        return False
