import argparse
import shutil
import time
from pathlib import Path


def wait_for_unlock(path, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(path, "a+b"):
                return True
        except OSError:
            time.sleep(1)
    return False


def replace_executable(target_path, new_path):
    target_path = Path(target_path)
    new_path = Path(new_path)
    backup_dir = target_path.parent / "backup"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"{target_path.stem}.backup{target_path.suffix}"

    if backup_path.exists():
        backup_path.unlink()

    try:
        if target_path.exists():
            shutil.copy2(target_path, backup_path)

        shutil.copy2(new_path, target_path)
    except Exception:
        if backup_path.exists():
            shutil.copy2(backup_path, target_path)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--new", required=True)
    args = parser.parse_args()

    target_path = Path(args.target)
    new_path = Path(args.new)

    if not wait_for_unlock(target_path):
        raise RuntimeError(f"Target is still running or locked: {target_path}")

    replace_executable(target_path, new_path)


if __name__ == "__main__":
    main()
