import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.cli import run_cli
from app.services.test_runner import run_program_news_test


class ProgramNewsTestRunnerTests(unittest.TestCase):
    def test_cli_test_news_does_not_require_a_schedule(self):
        result = {
            "status": "NO_NEW_CONTENT",
            "test_id": "test-1",
            "brief_path": Path("preview.json"),
            "source_stats": {"selected_total": 0},
            "cards_result": None,
        }
        with patch("app.cli.validate_runtime_seeds"):
            with patch("app.cli.load_runtime_settings", return_value={"scan": {}, "visual": {}}):
                with patch("app.cli.run_program_news_test", return_value=result) as run_test:
                    code = run_cli(["test-news", "--limit-per-source", "3"])

        self.assertEqual(code, 0)
        self.assertEqual(run_test.call_args.kwargs["limit_per_source"], 3)

    def test_program_test_runs_outside_schedule_in_isolated_preview(self):
        item = {
            "id": 1,
            "title": "Cảng biển châu Á tăng năng lực khai thác",
            "summary": "Các cảng biển châu Á tăng năng lực khai thác trong ngày hôm nay. Doanh nghiệp cần theo dõi lịch tàu và thời gian thông quan.",
            "impact_note": "Thay đổi này có thể ảnh hưởng lịch tàu, năng lực bốc dỡ và chi phí logistics trong khu vực.",
            "category": "Port",
            "source_name": "Test Maritime Source",
            "source_type": "backup",
            "original_url": "https://example.com/port-story",
            "published_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "canonical_url": "https://example.com/port-story",
            "title_hash": "hash",
            "item_key": "url:hash",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "app.services.combined_brief_source.collect_backup_news",
                return_value=[{"status": "ok", "items": [item]}],
            ):
                with patch(
                    "app.services.test_runner.generate_image_cards",
                    return_value={"items": 1, "output_dir": "visual", "cards": []},
                ) as render:
                    result = run_program_news_test(output_root=Path(temp_dir))

            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertTrue(str(result["brief_path"]).startswith(temp_dir))
            payload = json.loads(Path(result["brief_path"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["execution_mode"], "test")
            self.assertTrue(payload["preview_only"])
            self.assertEqual(payload["items"][0]["quality_status"], "accepted")
            render.assert_called_once()
            self.assertIn("test.db", str(result["db_path"]))

    def test_old_backup_items_are_not_rendered_in_program_test(self):
        item = {
            "id": 1,
            "title": "Tin hàng hải cũ bị loại khỏi bản tin",
            "summary": "Bản tin cũ có dữ kiện đầy đủ để kiểm tra cửa sổ thời gian. Nội dung này không còn thuộc khung cập nhật hiện tại.",
            "impact_note": "Tin cũ không được đưa vào bản tin mới vì đã vượt quá cửa sổ 48 giờ.",
            "category": "Shipping News",
            "source_name": "Old Source",
            "source_type": "backup",
            "original_url": "https://example.com/old-story",
            "published_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "app.services.combined_brief_source.collect_backup_news",
                return_value=[{"status": "ok", "items": [item]}],
            ):
                result = run_program_news_test(output_root=Path(temp_dir))

        self.assertEqual(result["status"], "NO_NEW_CONTENT")
        self.assertIsNone(result["cards_result"])
        self.assertEqual(result["source_stats"]["backup_stale_removed"], 1)


if __name__ == "__main__":
    unittest.main()
