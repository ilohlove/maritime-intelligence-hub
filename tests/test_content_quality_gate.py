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
                        "summary": "Tóm tắt tin cảng biển có tác động tới lịch tàu và logistics. Nhà khai thác cần theo dõi lịch cập cảng và năng lực bốc dỡ.",
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

    def test_primary_keeps_all_sheet_rows_and_bypasses_filters_ai_and_semantic_dedupe(self):
        items = []
        for index in range(1, 15):
            items.append(
                {
                    "title": f"Bản tin hàng hải Việt Nam số {index}",
                    "summary": (
                        "Bản tin cung cấp thông tin mới để doanh nghiệp theo dõi lịch tàu, "
                        "năng lực cảng và kế hoạch khai thác."
                    ),
                    "impact_note": (
                        "Thông tin này ảnh hưởng trực tiếp đến vận hành, lịch tàu và chi phí logistics của doanh nghiệp."
                    ),
                    "source_name": "VietnamPlus" if index <= 4 else f"Global Source {index}",
                    "source_type": "sheet",
                    "original_url": f"https://example.com/story-{index}",
                    "canonical_url": f"https://example.com/story-{index}",
                    "title_hash": f"hash-{index}",
                    "item_key": f"url:story-{index}",
                    "source_rank": index,
                    "row_index": index,
                    "published_at": "2026-08-03T07:10:00+07:00",
                }
            )
        snapshot = {
            "protocol_version": "legacy",
            "marker_mode": "legacy_time",
            "started_at": "07:15",
            "completed_at": "07:22",
            "run_id": "",
            "status": "",
            "row_count": None,
            "row_count_raw": "",
            "data_row_count": 14,
            "usable_row_count": 14,
            "row_errors": [],
            "rows": [{"Date": "2026-08-03T07:10:00+07:00"} for _ in items],
            "items": items,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.services.combined_brief_source.enrich_brief_items") as enrich:
                with patch("app.services.combined_brief_source.dedupe_similar_items") as semantic:
                    with patch("app.services.combined_brief_source.collect_backup_news") as backup:
                        result = build_combined_brief(
                            source_mode="sheet",
                            sheet_snapshot=snapshot,
                            expected_run_id="2026-08-03:morning",
                            selected_lane="primary",
                            run_id="2026-08-03:morning",
                            production=True,
                            exclude_vietnam=True,
                            sheet_limit=1,
                            card_limit=1,
                            db_path=Path(temp_dir) / "mih.db",
                            brief_path=Path(temp_dir) / "brief.json",
                        )

        self.assertEqual(len(result.payload["items"]), 14)
        self.assertEqual([item["row_index"] for item in result.payload["items"]], list(range(1, 15)))
        self.assertEqual(sum(item["source_name"] == "VietnamPlus" for item in result.payload["items"]), 4)
        enrich.assert_not_called()
        semantic.assert_not_called()
        backup.assert_not_called()

    def test_primary_reports_row_and_reason_for_repeated_sheet_url(self):
        base = {
            "summary": "Bản tin cung cấp thông tin mới để doanh nghiệp theo dõi lịch tàu và kế hoạch khai thác.",
            "impact_note": "Thông tin này ảnh hưởng trực tiếp đến lịch tàu và chi phí vận tải biển.",
            "source_name": "Sheet Source",
            "source_type": "sheet",
            "original_url": "https://example.com/same-story",
            "canonical_url": "https://example.com/same-story",
            "item_key": "url:same-story",
            "published_at": "2026-08-03T07:10:00+07:00",
        }
        items = [
            {**base, "title": "Bản tin hàng hải Việt Nam thứ nhất", "row_index": 1, "source_rank": 1},
            {**base, "title": "Bản tin hàng hải Việt Nam thứ hai", "row_index": 2, "source_rank": 2},
        ]
        snapshot = {
            "protocol_version": "v1",
            "marker_mode": "iso",
            "started_at": "2026-08-03T07:15:00+07:00",
            "completed_at": "2026-08-03T07:22:00+07:00",
            "data_row_count": 2,
            "usable_row_count": 2,
            "row_errors": [],
            "rows": [{"Date": item["published_at"]} for item in items],
            "items": items,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            result = build_combined_brief(
                source_mode="sheet",
                sheet_snapshot=snapshot,
                expected_run_id="2026-08-03:morning",
                selected_lane="primary",
                run_id="2026-08-03:morning",
                production=True,
                db_path=Path(temp_dir) / "mih.db",
                brief_path=Path(temp_dir) / "brief.json",
            )

        self.assertEqual(len(result.payload["items"]), 1)
        self.assertEqual(
            result.stats["sheet_skipped_rows"],
            [
                {
                    "row_index": 2,
                    "reason": "duplicate_url_in_sheet",
                    "title": "Bản tin hàng hải Việt Nam thứ hai",
                    "url": "https://example.com/same-story",
                }
            ],
        )

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

    def test_quality_gate_rejects_english_with_isolated_vietnam_term(self):
        self.assertFalse(
            is_valid_vietnamese_item(
                {
                    "title": "Port disruption update for Việt Nam",
                    "summary": "The organisation said the port disruption may continue. Operators should review the schedule.",
                    "impact_note": "The update affects shipping schedules and logistics costs for Việt Nam.",
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

    def test_card_keeps_impact_without_explicit_label(self):
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
        self.assertNotIn("Tại sao quan trọng", html)
        self.assertNotIn("impact-label", html)
        self.assertIn("Ảnh hưởng tới lịch tàu và chi phí logistics.", html)
        self.assertIn('class="impact"', html)

    def test_card_hides_entire_impact_block_when_disabled(self):
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
            style_settings={"show_impact": False},
        )
        self.assertNotIn('class="impact"', html)
        self.assertNotIn("Ảnh hưởng tới lịch tàu", html)

    def test_quality_gate_accepts_substantive_single_sentence_and_dotted_numbers(self):
        item = {
            "title": "Sản lượng cảng biển Việt Nam tiếp tục tăng",
            "summary": (
                "Các cảng đã xử lý 203.881 tấn hàng và 172.000 container trong kỳ báo cáo, "
                "qua đó duy trì hoạt động vận tải biển ổn định."
            ),
            "impact_note": "Sản lượng mới ảnh hưởng trực tiếp đến lịch tàu, năng lực cảng và chi phí logistics.",
        }
        self.assertTrue(is_valid_vietnamese_item(item))

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
