import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.services.ai_processor import AIEnrichmentError, GeminiChatProvider
from app.services.combined_brief_source import (
    build_combined_brief,
    dedupe_similar_items,
    enrich_brief_items,
    is_valid_vietnamese_item,
    rank_backup_items,
)
from app.services.visual_brief_renderer import render_card_html


class ContentQualityGateTests(unittest.TestCase):
    def test_production_primary_keeps_lane_identity_and_vietnamese_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            brief_path = Path(temp_dir) / "runs" / "combined_brief.json"
            snapshot = {
                "protocol_version": "v1",
                "started_at": "2026-08-02T07:15:00+07:00",
                "completed_at": "2026-08-02T07:20:00+07:00",
                "run_id": "2026-08-02:morning",
                "status": "COMPLETED",
                "row_count": 1,
                "row_count_raw": "1",
                "data_row_count": 1,
                "usable_row_count": 1,
                "error_code": "",
                "rows": [],
                "items": [
                    {
                        "title": "Cảng biển Việt Nam tăng hiệu suất",
                        "summary": "Tóm tắt tin cảng biển có tác động tới lịch tàu và logistics.",
                        "impact_note": "Tin quan trọng vì ảnh hưởng tới năng lực cảng và chi phí vận tải.",
                        "source_name": "Sheet Source",
                        "source_type": "sheet",
                        "original_url": "https://example.com/story",
                        "canonical_url": "https://example.com/story",
                        "title_hash": "hash",
                        "item_key": "url:hash",
                        "source_rank": 1,
                    }
                ],
            }
            result = build_combined_brief(
                source_mode="sheet",
                sheet_snapshot=snapshot,
                expected_run_id="2026-08-02:morning",
                selected_lane="primary",
                run_id="2026-08-02:morning",
                production=True,
                db_path=Path(temp_dir) / "mih.db",
                brief_path=brief_path,
            )

        self.assertEqual(result.payload["run_id"], "2026-08-02:morning")
        self.assertEqual(result.payload["selected_lane"], "primary")
        self.assertFalse(result.payload["preview_only"])
        self.assertEqual(result.payload["items"][0]["quality_status"], "accepted")

    def test_backup_item_is_rejected_when_ai_is_unavailable(self):
        item = _backup_item()
        with patch(
            "app.services.combined_brief_source.get_ai_provider",
            side_effect=AIEnrichmentError("No production AI provider is configured"),
        ):
            accepted, stats = enrich_brief_items([item], db_path=None, production=True)

        self.assertEqual(accepted, [])
        self.assertEqual(stats["quality_rejected"], 1)

    def test_backup_item_is_translated_before_production_render(self):
        provider = Mock()
        provider.model_name = "test-model"
        provider.prompt_version = "test-v1"
        provider.provider_name = "test-provider"
        provider._request_summary.return_value = {
            "headline": "Cảng biển châu Á siết kiểm soát an toàn",
            "summary": "Các cảng biển châu Á tăng cường kiểm soát an toàn sau sự cố mới. Nhà khai thác cần rà soát lịch tàu và phương án khai thác.",
            "impact_note": "Tin này quan trọng vì có thể làm thay đổi lịch tàu, thời gian thông quan và chi phí vận tải trong khu vực.",
            "category": "Safety",
            "importance_score": 8,
        }
        with patch("app.services.combined_brief_source.get_ai_provider", return_value=provider):
            accepted, stats = enrich_brief_items([_backup_item()], db_path=None, production=True)

        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["quality_status"], "accepted")
        self.assertEqual(stats["quality_rejected"], 0)
        self.assertNotIn("English summary", accepted[0]["summary"])

    def test_quality_gate_rejects_english_and_empty_impact(self):
        self.assertFalse(
            is_valid_vietnamese_item(
                {
                    "title": "English title",
                    "summary": "The organisation advised shipping companies to review their schedule.",
                    "impact_note": "",
                }
            )
        )

    def test_backup_ranking_applies_threshold_and_source_diversity(self):
        items = [
            {"title": "A", "source_name": "A", "category": "Safety", "editorial_score": 90},
            {"title": "B", "source_name": "A", "category": "Safety", "editorial_score": 80},
            {"title": "C", "source_name": "B", "category": "Port", "editorial_score": 70},
            {"title": "D", "source_name": "B", "category": "Port", "editorial_score": 40},
        ]
        selected = rank_backup_items(items, limit=12)
        self.assertEqual([item["title"] for item in selected], ["A", "B", "C"])

    def test_similar_cross_source_titles_are_deduped(self):
        first = _backup_item()
        first["title"] = "Yemen Houthis deny fees on ships transiting Bab el Mandeb"
        second = _backup_item()
        second["title"] = "Yemen Houthis deny fees on ships sailing through Red Sea"
        second["source_name"] = "Other source"
        selected, removed = dedupe_similar_items([first, second])
        self.assertEqual(len(selected), 1)
        self.assertEqual(removed, 1)

    def test_card_contains_explicit_why_important_label(self):
        html = render_card_html(
            {"title": "Brief"},
            {
                "title": "Cảng biển tăng kiểm soát",
                "summary": "Tóm tắt sự kiện hàng hải.",
                "impact_note": "Ảnh hưởng tới lịch tàu và chi phí logistics.",
                "source_name": "Nguồn tin",
                "original_url": "https://example.com/story",
            },
            1,
            {"status": "fallback"},
        )
        self.assertIn("Tại sao quan trọng", html)

    def test_gemini_key_is_sent_in_header_not_url_query(self):
        response = Mock()
        response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"headline":"Tiêu đề hàng hải","summary":"Tóm tắt tin hàng hải có tác động tới lịch tàu.","impact_note":"Ảnh hưởng tới vận tải và chi phí logistics.","category":"Safety","importance_score":7}'
                            }
                        ]
                    }
                }
            ]
        }
        response.raise_for_status.return_value = None
        provider = GeminiChatProvider("secret-key", model="test-model")
        with patch("app.services.ai_processor.requests.post", return_value=response) as post:
            provider._request_summary(
                {"id": 1, "title": "English title", "source_name": "Source", "url": "https://example.com"}
            )

        kwargs = post.call_args.kwargs
        self.assertNotIn("params", kwargs)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "secret-key")


def _backup_item():
    return {
        "id": 1,
        "title": "English title",
        "summary": "English summary",
        "impact_note": "",
        "description": "English description about a maritime safety incident.",
        "source_name": "Backup RSS",
        "source_type": "backup",
        "original_url": "https://example.com/story",
        "url": "https://example.com/story",
        "category": "Shipping News",
        "importance_score": 7,
    }


if __name__ == "__main__":
    unittest.main()
