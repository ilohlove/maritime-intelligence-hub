import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.retention import cleanup_runtime_artifacts, cleanup_update_artifacts


class RetentionTests(unittest.TestCase):
    def test_cleanup_output_removes_old_visual_runs_and_preserves_latest_brief(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            briefs = root / "output" / "briefs"
            old_run = root / "output" / "visual_briefs" / "combined" / "20260601_080000"
            new_run = root / "output" / "visual_briefs" / "combined" / "20260629_080000"
            briefs.mkdir(parents=True)
            old_run.mkdir(parents=True)
            new_run.mkdir(parents=True)

            old_brief = briefs / "2026-06-01_morning.md"
            latest_brief = briefs / "latest_brief.md"
            gitkeep = briefs / ".gitkeep"
            old_brief.write_text("old", encoding="utf-8")
            latest_brief.write_text("latest", encoding="utf-8")
            gitkeep.write_text("", encoding="utf-8")
            (old_run / "card_01.png").write_bytes(b"old")
            (new_run / "card_01.png").write_bytes(b"new")

            _touch_old(old_brief, days=3)
            _touch_old(latest_brief, days=3)
            _touch_old(gitkeep, days=3)
            _touch_old(old_run, days=3)
            _touch_old(old_run / "card_01.png", days=3)
            _touch_old(new_run, days=1)
            _touch_old(new_run / "card_01.png", days=1)

            cleanup_runtime_artifacts(root_dir=root, output_retention_days=2, temp_retention_days=2)

            self.assertFalse(old_brief.exists())
            self.assertFalse(old_run.exists())
            self.assertTrue(latest_brief.exists())
            self.assertTrue(gitkeep.exists())
            self.assertTrue(new_run.exists())

    def test_cleanup_temp_removes_old_cache_and_keeps_new_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = root / "temp" / "visual_assets"
            cache.mkdir(parents=True)
            old_file = cache / "old.png"
            new_file = cache / "new.png"
            gitkeep = root / "temp" / ".gitkeep"
            old_file.write_bytes(b"old")
            new_file.write_bytes(b"new")
            gitkeep.write_text("", encoding="utf-8")
            _touch_old(old_file, days=3)
            _touch_old(new_file, days=1)
            _touch_old(gitkeep, days=3)

            cleanup_runtime_artifacts(root_dir=root, output_retention_days=2, temp_retention_days=2)

            self.assertFalse(old_file.exists())
            self.assertTrue(new_file.exists())
            self.assertTrue(gitkeep.exists())

    def test_cleanup_update_artifacts_removes_only_app_temp_and_backup_exe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            temp = root / "temp"
            backup = root / "backup"
            other = root / "other"
            temp.mkdir()
            backup.mkdir()
            other.mkdir()
            temp_exe = temp / "BV-App.exe"
            temp_download = temp / "BV-App.exe.download"
            backup_exe = backup / "BV-App.backup.exe"
            other_exe = other / "BV-App.exe"
            unrelated_backup = backup / "other.backup.exe"
            for path in [temp_exe, temp_download, backup_exe, other_exe, unrelated_backup]:
                path.write_bytes(b"exe")

            removed = cleanup_update_artifacts(root_dir=root, exe_name="BV-App.exe")

            self.assertEqual(len(removed), 3)
            self.assertFalse(temp_exe.exists())
            self.assertFalse(temp_download.exists())
            self.assertFalse(backup_exe.exists())
            self.assertTrue(other_exe.exists())
            self.assertTrue(unrelated_backup.exists())


def _touch_old(path, days):
    timestamp = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp))


if __name__ == "__main__":
    unittest.main()
