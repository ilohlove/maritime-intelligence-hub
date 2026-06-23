import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.services.combined_brief_source import (
    build_combined_brief,
    canonicalize_url,
    filter_publishable_items,
    format_empty_combined_message,
    item_key,
    normalize_source_url,
    sheet_run_label,
    sheet_row_to_item,
    title_hash,
)
from app.services.source_master import load_sources
from app.services.storage import init_db, sync_sources, upsert_article, upsert_summary, utc_now
from app.services.visual_brief_renderer import generate_image_cards


class CombinedBriefSourceTests(unittest.TestCase):
    def test_sheet_run_label_parses_morning_and_evening(self):
        self.assertEqual(sheet_run_label("07h15"), "morning")
        self.assertEqual(sheet_run_label("08:00"), "morning")
        self.assertEqual(sheet_run_label("19:15"), "evening")
        self.assertEqual(sheet_run_label("23:29"), "evening")

    def test_sheet_run_label_rejects_invalid_time(self):
        with self.assertRaisesRegex(ValueError, "Sheet L1"):
            sheet_run_label("ready")

    def test_sheet_row_maps_vietnamese_fields(self):
        item = sheet_row_to_item(
            {
                "Date": "2026-06-13",
                "Section": "Domestic maritime news",
                "Topic": "Port",
                "Headline": "English title",
                "Vietnamese translation": "Cảng biển Việt Nam tăng hiệu suất",
                "Source": "VnEconomy",
                "Source URL": "https://example.com/story?utm_source=test#x",
                "Main summary": "English summary",
                "Main summary (Vietnamese)": "Tóm tắt tiếng Việt có dấu.",
                "Why it matters": "English impact",
                "Why it matters (Vietnamese)": "Tác động đến logistics Việt Nam.",
            },
            1,
        )

        self.assertEqual(item["title"], "Cảng biển Việt Nam tăng hiệu suất")
        self.assertEqual(item["summary"], "Tóm tắt tiếng Việt có dấu.")
        self.assertEqual(item["impact_note"], "Tác động đến logistics Việt Nam.")
        self.assertEqual(item["canonical_url"], "https://example.com/story")
        self.assertEqual(item["source_type"], "sheet")

    def test_sheet_row_normalizes_markdown_source_url(self):
        item = sheet_row_to_item(
            {
                "Date": "2026-06-22",
                "Headline": "Trio of owners emerge behind 13-ship haul",
                "Source": "Splash247",
                "Source URL": (
                    "[https://splash247.com/trio-of-owners-emerge-behind-13-ship-huangpu-wenchong-boxship-haul/]"
                    "(https://splash247.com/trio-of-owners-emerge-behind-13-ship-huangpu-wenchong-boxship-haul/)"
                ),
                "Main summary": "Summary",
                "Why it matters": "Impact",
            },
            1,
        )

        self.assertEqual(
            item["original_url"],
            "https://splash247.com/trio-of-owners-emerge-behind-13-ship-huangpu-wenchong-boxship-haul/",
        )
        self.assertEqual(
            item["canonical_url"],
            "https://splash247.com/trio-of-owners-emerge-behind-13-ship-huangpu-wenchong-boxship-haul",
        )

    def test_normalize_source_url_supports_html_anchor(self):
        url = normalize_source_url('<a href="https://safety4sea.com/story/">Safety4Sea</a>')

        self.assertEqual(url, "https://safety4sea.com/story/")

    def test_dedupe_prefers_sheet_over_app(self):
        app_item = _item("app", "App Source", "https://example.com/story?utm_campaign=x")
        sheet_item = _item("sheet", "Sheet Source", "https://example.com/story")

        selected, stats = filter_publishable_items([app_item, sheet_item])

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["source_type"], "sheet")
        self.assertEqual(selected[0]["source_name"], "Sheet Source")
        self.assertEqual(stats["duplicate_removed"], 1)

    def test_generate_cards_limit_none_uses_all_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            brief_path = temp_path / "combined_brief.json"
            output_dir = temp_path / "cards"
            payload = {
                "brief_type": "combined",
                "title": "Combined Brief",
                "items": [
                    _item("sheet", "Sheet A", "https://example.com/a"),
                    _item("app", "App B", "https://example.com/b"),
                ],
            }
            brief_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            with patch("app.services.visual_brief_renderer.resolve_card_image", return_value={"status": "fallback"}):
                with patch("app.services.visual_brief_renderer.render_html_to_png", side_effect=_write_fake_png):
                    result = generate_image_cards(
                        "combined",
                        limit=None,
                        output_dir=output_dir,
                        source_brief_path=brief_path,
                    )

        self.assertEqual(result["items"], 2)

    def test_app_mode_reports_empty_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "mih.db"
            brief_path = temp_path / "combined_brief.json"
            init_db(db_path)

            result = build_combined_brief(source_mode="app", db_path=db_path, brief_path=brief_path)
            message = format_empty_combined_message(result.stats, result.brief_path)

        self.assertEqual(result.payload["items"], [])
        self.assertIn("App database is empty. Run scan + AI summary first.", message)
        self.assertIn(str(db_path), message)

    def test_app_mode_reports_missing_ai_summaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "mih.db"
            brief_path = temp_path / "combined_brief.json"
            _seed_sources(db_path)
            upsert_article(_article(), db_path=db_path)

            result = build_combined_brief(source_mode="app", db_path=db_path, brief_path=brief_path)
            message = format_empty_combined_message(result.stats, result.brief_path)

        self.assertEqual(result.payload["items"], [])
        self.assertIn("No AI summaries yet. Run AI summarization first.", message)
        self.assertIn("Articles: 1", message)
        self.assertIn("AI summaries: 0", message)

    def test_app_mode_reports_no_fresh_summarized_articles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "mih.db"
            brief_path = temp_path / "combined_brief.json"
            _seed_sources(db_path)
            article_id, _ = upsert_article(
                _article(published_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat()),
                db_path=db_path,
            )
            upsert_summary(_summary(article_id), db_path=db_path)

            result = build_combined_brief(source_mode="app", db_path=db_path, brief_path=brief_path)
            message = format_empty_combined_message(result.stats, result.brief_path)

        self.assertEqual(result.payload["items"], [])
        self.assertIn("No fresh summarized articles in the current brief window.", message)
        self.assertIn("Fresh brief candidates: 0", message)

    def test_app_mode_builds_items_from_fresh_summarized_articles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "mih.db"
            brief_path = temp_path / "combined_brief.json"
            _seed_sources(db_path)
            article_id, _ = upsert_article(_article(), db_path=db_path)
            upsert_summary(_summary(article_id), db_path=db_path)

            result = build_combined_brief(source_mode="app", db_path=db_path, brief_path=brief_path)

        self.assertEqual(len(result.payload["items"]), 1)
        self.assertEqual(result.stats["app_db"]["candidate_window_total"], 1)

    def test_sheet_mode_records_sheet_urls_and_loads_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "mih.db"
            brief_path = temp_path / "combined_brief.json"
            sheet_url = "https://docs.google.com/spreadsheets/d/sheet123/edit?gid=456#gid=456"
            session = _FakeSession(
                "Date,Section,Topic,Headline,Vietnamese translation,Source,Source URL,Main summary,Main summary (Vietnamese),Why it matters,Why it matters (Vietnamese)\n"
                "2026-06-14,Domestic,Port,English title,Tiêu đề tiếng Việt,Sheet Source,https://example.com/sheet-story,English summary,Tóm tắt tiếng Việt,English impact,Tác động tiếng Việt\n"
            )

            result = build_combined_brief(
                source_mode="sheet",
                sheet_url=sheet_url,
                sheet_limit=None,
                db_path=db_path,
                brief_path=brief_path,
                session=session,
            )

        self.assertEqual(len(result.payload["items"]), 1)
        self.assertEqual(result.stats["source_mode"], "sheet")
        self.assertEqual(result.stats["sheet_total"], 1)
        self.assertEqual(result.stats["app_total"], 0)
        self.assertEqual(result.stats["sheet_source"]["sheet_url"], sheet_url)
        self.assertEqual(
            result.stats["sheet_source"]["csv_url"],
            "https://docs.google.com/spreadsheets/d/sheet123/export?format=csv&gid=456",
        )

    def test_sheet_mode_uses_all_rows_without_limits_or_dedupe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "mih.db"
            brief_path = temp_path / "combined_brief.json"
            sheet_url = "https://docs.google.com/spreadsheets/d/sheet123/edit?gid=456#gid=456"
            session = _FakeSession(
                "Date,Section,Topic,Headline,Vietnamese translation,Source,Source URL,Main summary,Main summary (Vietnamese),Why it matters,Why it matters (Vietnamese),19:15\n"
                "2026-06-14,Domestic,Port,English A,TiÃªu Ä‘á» A,Sheet Source,https://example.com/same,English summary,TÃ³m táº¯t A,English impact,TÃ¡c Ä‘á»™ng A,\n"
                "2026-06-14,Domestic,Port,English B,TiÃªu Ä‘á» B,Sheet Source,https://example.com/same,English summary,TÃ³m táº¯t B,English impact,TÃ¡c Ä‘á»™ng B,\n"
            )

            result = build_combined_brief(
                source_mode="sheet",
                sheet_url=sheet_url,
                sheet_limit=1,
                card_limit=1,
                db_path=db_path,
                brief_path=brief_path,
                session=session,
            )

        self.assertEqual(len(result.payload["items"]), 2)
        self.assertEqual(result.stats["sheet_total"], 2)
        self.assertEqual(result.stats["duplicate_removed"], 0)
        self.assertEqual(result.stats["selected_total"], 2)
        self.assertEqual(result.stats["sheet_source"]["run_marker"], "19:15")
        self.assertEqual(result.stats["sheet_source"]["run_label"], "evening")

    def test_sheet_mode_reports_empty_sheet_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "mih.db"
            brief_path = temp_path / "combined_brief.json"

            result = build_combined_brief(source_mode="sheet", sheet_url="", db_path=db_path, brief_path=brief_path)
            message = format_empty_combined_message(result.stats, result.brief_path)

        self.assertEqual(result.payload["items"], [])
        self.assertIn("Google Sheet URL is empty", message)
        self.assertIn("Source mode: sheet", message)


def _item(source_type, source_name, url):
    item = {
        "title": "Cảng biển Việt Nam tăng hiệu suất",
        "summary": "Tóm tắt tiếng Việt có dấu.",
        "impact_note": "Tác động đến logistics Việt Nam.",
        "source_name": source_name,
        "original_url": url,
        "source_type": source_type,
        "source_rank": 1,
    }
    item["canonical_url"] = canonicalize_url(url)
    item["title_hash"] = title_hash(item["title"])
    item["item_key"] = item_key(item)
    return item


def _seed_sources(db_path):
    rows, _ = load_sources("NEWS_SOURCE_MASTER.csv")
    init_db(db_path)
    sync_sources(rows, db_path=db_path)


def _article(published_at="auto"):
    published = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        if published_at == "auto"
        else published_at
    )
    return {
        "source_id": "SRC004",
        "source_name": "Safety4Sea",
        "title": "Safety4Sea test article",
        "url": "https://safety4sea.com/test-app-mode",
        "normalized_title": "safety4sea test article",
        "title_hash": "test-app-mode-title-hash",
        "published_at": published,
        "fetched_at": utc_now(),
        "language": "EN",
        "category": "Safety",
        "description": "Short safety description.",
        "content_excerpt": "Short safety description.",
        "importance_score": 8,
    }


def _summary(article_id):
    return {
        "article_id": article_id,
        "headline": "Tin an toàn hàng hải",
        "summary": "Tóm tắt tin hàng hải có link gốc.",
        "impact_note": "Tác động hàng hải cần theo dõi.",
        "prompt_version": "mock-v1",
        "model_name": "rule-based-mock",
        "token_usage": 0,
    }


class _FakeSession:
    def __init__(self, text):
        self.text = text
        self.requested_urls = []

    def get(self, url, timeout):
        self.requested_urls.append((url, timeout))
        return _FakeResponse(self.text)


class _FakeResponse:
    def __init__(self, text):
        self.content = text.encode("utf-8")

    def raise_for_status(self):
        return None


def _write_fake_png(_html, output_path):
    Path(output_path).write_bytes(b"\x89PNG\r\n\x1a\nfake")


if __name__ == "__main__":
    unittest.main()
