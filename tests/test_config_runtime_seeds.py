import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import ensure_runtime_seed, validate_runtime_seeds


class RuntimeSeedTests(unittest.TestCase):
    def test_bundled_seed_is_copied_to_runtime_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "runtime"
            resources = Path(temp_dir) / "resources"
            root.mkdir()
            resources.mkdir()
            (resources / "MASTER.csv").write_text("ID,Name\n1,Source\n", encoding="utf-8")
            with patch("app.config.ROOT_DIR", root), patch("app.config.RESOURCE_DIR", resources):
                path = ensure_runtime_seed("MASTER.csv")

            self.assertEqual(path, root / "MASTER.csv")
            self.assertEqual(path.read_text(encoding="utf-8"), "ID,Name\n1,Source\n")

    def test_validation_fails_fast_when_seed_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "runtime"
            resources = Path(temp_dir) / "resources"
            root.mkdir()
            resources.mkdir()
            with patch("app.config.ROOT_DIR", root), patch("app.config.RESOURCE_DIR", resources):
                with self.assertRaisesRegex(FileNotFoundError, "MISSING.csv"):
                    validate_runtime_seeds(("MISSING.csv",))


if __name__ == "__main__":
    unittest.main()
