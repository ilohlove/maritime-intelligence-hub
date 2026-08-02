import json
import tempfile
import unittest
from pathlib import Path

from app.services.runtime_settings import (
    CANONICAL_SCAN_TIMES,
    CANONICAL_TIMEZONE,
    DEFAULT_FACEBOOK_INTRO_TEXT,
    PREVIOUS_DEFAULT_FACEBOOK_INTRO_TEXT,
    load_runtime_settings,
    save_runtime_settings,
)


class RuntimeOrchestrationSettingsTests(unittest.TestCase):
    def test_missing_file_uses_canonical_schedule_and_orchestration_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = load_runtime_settings(Path(temp_dir) / "missing.json")

        self.assertEqual(settings["scan"]["times"], list(CANONICAL_SCAN_TIMES))
        self.assertEqual(settings["scan"]["timezone"], CANONICAL_TIMEZONE)
        self.assertEqual(settings["scan"]["timezone_offset"], "+7")
        self.assertEqual(
            settings["orchestration"],
            {
                "lane_policy": "primary_then_backup",
                "primary_timeout_minutes": 30,
                "poll_interval_seconds": 60,
                "catch_up_window_minutes": 120,
                "lease_seconds": 300,
                "heartbeat_seconds": 30,
            },
        )

    def test_legacy_default_schedule_and_caption_are_migrated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime_settings.json"
            path.write_text(
                json.dumps(
                    {
                        "scan": {"times": ["07:30", "19:30"], "timezone_offset": "-5"},
                        "publish": {"facebook_intro_text": PREVIOUS_DEFAULT_FACEBOOK_INTRO_TEXT},
                    }
                ),
                encoding="utf-8",
            )

            settings = load_runtime_settings(path)

        self.assertEqual(settings["scan"]["times"], ["07:15", "19:15"])
        self.assertEqual(settings["scan"]["timezone"], "Asia/Bangkok")
        self.assertEqual(settings["scan"]["timezone_offset"], "+7")
        self.assertEqual(settings["publish"]["facebook_intro_text"], DEFAULT_FACEBOOK_INTRO_TEXT)
        self.assertIn("07:15", DEFAULT_FACEBOOK_INTRO_TEXT)
        self.assertIn("19:15", DEFAULT_FACEBOOK_INTRO_TEXT)
        self.assertNotIn("07:30", DEFAULT_FACEBOOK_INTRO_TEXT)

    def test_custom_valid_schedule_is_preserved_and_invalid_values_are_removed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime_settings.json"
            saved = save_runtime_settings(
                {
                    "scan": {"times": ["8:05", "bad", "20:10", "08:05"]},
                    "orchestration": {
                        "primary_timeout_minutes": 0,
                        "poll_interval_seconds": "15",
                        "catch_up_window_minutes": -1,
                        "lease_seconds": 10,
                        "heartbeat_seconds": 20,
                    },
                },
                path,
            )

            reloaded = load_runtime_settings(path)

        self.assertEqual(saved["scan"]["times"], ["08:05", "20:10"])
        self.assertEqual(saved["scan"]["runs_per_day"], 2)
        self.assertEqual(saved["orchestration"]["primary_timeout_minutes"], 30)
        self.assertEqual(saved["orchestration"]["poll_interval_seconds"], 15)
        self.assertEqual(saved["orchestration"]["catch_up_window_minutes"], 120)
        self.assertEqual(saved["orchestration"]["heartbeat_seconds"], 20)
        self.assertEqual(saved["orchestration"]["lease_seconds"], 300)
        self.assertEqual(reloaded, saved)

    def test_non_object_json_falls_back_to_complete_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime_settings.json"
            path.write_text("[]", encoding="utf-8")

            settings = load_runtime_settings(path)

        self.assertEqual(settings["scan"]["times"], ["07:15", "19:15"])
        self.assertEqual(settings["orchestration"]["lane_policy"], "primary_then_backup")
        self.assertIn("visual", settings)
        self.assertIn("publish", settings)


if __name__ == "__main__":
    unittest.main()
