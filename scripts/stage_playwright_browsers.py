import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REQUIRED_PREFIXES = (
    "chromium-",
    "chromium_headless_shell-",
    "ffmpeg-",
    "winldd-",
)


def playwright_install_locations():
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    locations = []
    for line in result.stdout.splitlines():
        marker = "Install location:"
        if marker not in line:
            continue
        path = Path(line.split(marker, 1)[1].strip())
        if path.name.startswith(REQUIRED_PREFIXES):
            locations.append(path)
    return locations


def stage_browsers(output_dir):
    locations = playwright_install_locations()
    missing_prefixes = [
        prefix
        for prefix in REQUIRED_PREFIXES
        if not any(path.name.startswith(prefix) and path.is_dir() for path in locations)
    ]
    if missing_prefixes:
        raise FileNotFoundError(
            "Missing installed Playwright components: " + ", ".join(missing_prefixes)
        )

    target = Path(output_dir)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for source in locations:
        if source.is_dir() and source.name.startswith(REQUIRED_PREFIXES):
            shutil.copytree(source, target / source.name)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    target = stage_browsers(args.output)
    print(f"Staged Playwright browsers: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
