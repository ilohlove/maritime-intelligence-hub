import csv
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from app.services.combined_brief_source import (
    build_combined_brief,
    canonicalize_url,
    evaluate_sheet_snapshot,
    filter_publishable_items,
    format_empty_combined_message,
    item_key,
    load_sheet_snapshot,
    normalize_source_url,
    parse_sheet_snapshot,
    sheet_run_label,
    sheet_row_to_item,
    title_hash,
    write_json_atomic,
)
from app.services.source_master import load_sources
from app.services.storage import init_db, mark_items_published, sync_sources, upsert_article, upsert_summary, utc_now
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
                "Vietnamese translation": "Ba chủ tàu hoàn tất thương vụ đội tàu container",
                "Source": "Splash247",
                "Source URL": (
                    "[https://splash247.com/trio-of-owners-emerge-behind-13-ship-huangpu-wenchong-boxship-haul/]"
                    "(https://splash247.com/trio-of-owners-emerge-behind-13-ship-huangpu-wenchong-boxship-haul/)"
                ),
                "Main summary": "Summary",
                "Main summary (Vietnamese)": "Tóm tắt thương vụ đội tàu container mới. Hoạt động này ảnh hưởng đến năng lực vận chuyển khu vực.",
                "Why it matters": "Impact",
                "Why it matters (Vietnamese)": "Thương vụ có thể thay đổi năng lực vận chuyển và lịch khai thác trong khu vực.",
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

    def test_exact_dedupe_does_not_remove_merely_similar_titles(self):
        first = _item_with_title(
            "backup",
            "Source A",
            "https://example.com/a",
            "Port congestion worsens across major Asian hubs",
        )
        second = _item_with_title(
            "backup",
            "Source B",
            "https://example.com/b",
            "Port congestion improves across major Asian hubs",
        )

        selected, stats = filter_publishable_items([first, second])

        self.assertEqual(len(selected), 2)
        self.assertEqual(stats["duplicate_removed"], 0)

    def test_protocol_v1_snapshot_is_loaded_and_evaluated_from_one_request(self):
        session = _FakeSession(
            _protocol_csv(
                started_at="2026-08-01T07:15:00+07:00",
                completed_at="2026-08-01T07:21:00+07:00",
                run_id="2026-08-01:morning",
                status="COMPLETED",
                row_count="1",
                rows=[_sheet_data_row("New maritime report", "https://example.com/new")],
            )
        )

        snapshot = load_sheet_snapshot(
            "https://docs.google.com/spreadsheets/d/sheet123/edit?gid=0",
            session=session,
        )
        evaluation = evaluate_sheet_snapshot(snapshot, "2026-08-01:morning")

        self.assertEqual(len(session.requested_urls), 1)
        self.assertEqual(snapshot["protocol_version"], "v1")
        self.assertEqual(snapshot["run_id"], "2026-08-01:morning")
        self.assertEqual(snapshot["status"], "COMPLETED")
        self.assertEqual(snapshot["row_count"], 1)
        self.assertEqual(snapshot["data_row_count"], 1)
        self.assertEqual(snapshot["usable_row_count"], 1)
        self.assertEqual(evaluation["state"], "ready")
        self.assertTrue(evaluation["ready"])

    def test_protocol_v1_running_snapshot_with_old_rows_waits(self):
        snapshot = parse_sheet_snapshot(
            _protocol_csv(
                started_at="2026-08-01T07:15:00+07:00",
                run_id="2026-08-01:morning",
                status="RUNNING",
                rows=[_sheet_data_row("Yesterday's report", "https://example.com/old", date="2026-07-31")],
            )
        )

        evaluation = evaluate_sheet_snapshot(snapshot, "2026-08-01:morning")

        self.assertEqual(evaluation["state"], "waiting")
        self.assertEqual(evaluation["reason"], "run_in_progress")
        self.assertFalse(evaluation["ready"])

    def test_iso_previous_completed_run_is_still_waiting(self):
        snapshot = parse_sheet_snapshot(
            _protocol_csv(
                started_at="2026-07-31T19:15:00+07:00",
                completed_at="2026-07-31T19:21:00+07:00",
                run_id="2026-07-31:evening",
                status="COMPLETED",
                row_count="1",
                rows=[_sheet_data_row("Old report", "https://example.com/old", date="2026-07-31")],
            )
        )

        evaluation = evaluate_sheet_snapshot(snapshot, "2026-08-01:morning")

        self.assertEqual(evaluation["state"], "waiting")
        self.assertEqual(evaluation["reason"], "l1_slot_mismatch")

    def test_diagnostic_row_count_mismatch_does_not_block_valid_sheet(self):
        snapshot = parse_sheet_snapshot(
            _protocol_csv(
                started_at="2026-08-01T07:15:00+07:00",
                completed_at="2026-08-01T07:21:00+07:00",
                run_id="2026-08-01:morning",
                status="COMPLETED",
                row_count="2",
                rows=[_sheet_data_row("Valid report", "https://example.com/valid")],
            )
        )

        evaluation = evaluate_sheet_snapshot(snapshot, "2026-08-01:morning")

        self.assertEqual(evaluation["state"], "ready")
        self.assertTrue(any("P1 row_count" in value for value in evaluation["diagnostics"]))

    def test_hhmm_markers_accept_empty_diagnostics_columns(self):
        snapshot = parse_sheet_snapshot(
            _protocol_csv(
                started_at="07:15",
                completed_at="07:22",
                run_id="",
                status="",
                row_count="",
                rows=[_sheet_data_row("Current report", "https://example.com/current")],
            )
        )

        evaluation = evaluate_sheet_snapshot(snapshot, "2026-08-03:morning")

        self.assertEqual(snapshot["marker_mode"], "legacy_time")
        self.assertEqual(evaluation["state"], "ready")
        self.assertTrue(any("N1 is empty" in value for value in evaluation["diagnostics"]))

    def test_content_hash_ignores_lm_but_snapshot_hash_includes_them(self):
        row = _sheet_data_row("Current report", "https://example.com/current")
        first = parse_sheet_snapshot(
            _protocol_csv(
                started_at="07:15",
                completed_at="07:22",
                run_id="",
                status="",
                rows=[row],
            )
        )
        second = parse_sheet_snapshot(
            _protocol_csv(
                started_at="07:15",
                completed_at="07:23",
                run_id="",
                status="",
                rows=[row],
            )
        )

        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertNotEqual(first["snapshot_hash"], second["snapshot_hash"])

    def test_protocol_v1_rejects_malformed_source_url(self):
        snapshot = parse_sheet_snapshot(
            _protocol_csv(
                started_at="2026-08-01T07:15:00+07:00",
                completed_at="2026-08-01T07:21:00+07:00",
                run_id="2026-08-01:morning",
                status="COMPLETED",
                row_count="1",
                rows=[_sheet_data_row("Invalid report", "not-a-url")],
            )
        )

        evaluation = evaluate_sheet_snapshot(snapshot, "2026-08-01:morning")

        self.assertEqual(snapshot["usable_row_count"], 0)
        self.assertEqual(evaluation["state"], "invalid")
        self.assertTrue(any("Only 0 of 1 Sheet rows" in error for error in evaluation["errors"]))

    def test_nq_failed_status_is_diagnostic_when_m1_is_empty(self):
        snapshot = parse_sheet_snapshot(
            _protocol_csv(
                started_at="2026-08-01T07:15:00+07:00",
                run_id="2026-08-01:morning",
                status="FAILED",
                error_code="AGENT_TIMEOUT",
            )
        )

        evaluation = evaluate_sheet_snapshot(snapshot, "2026-08-01:morning")

        self.assertEqual(evaluation["state"], "waiting")
        self.assertEqual(evaluation["reason"], "run_in_progress")
        self.assertFalse(evaluation["terminal"])
        self.assertTrue(any("O1 is FAILED" in value for value in evaluation["diagnostics"]))

    def test_protocol_v1_rejects_naive_timestamp_and_wrong_slot(self):
        naive = parse_sheet_snapshot(
            _protocol_csv(
                started_at="2026-08-01T07:15:00",
                run_id="2026-08-01:morning",
                status="RUNNING",
            )
        )
        wrong_slot = parse_sheet_snapshot(
            _protocol_csv(
                started_at="2026-08-01T19:15:00+07:00",
                run_id="2026-08-01:morning",
                status="RUNNING",
            )
        )

        naive_result = evaluate_sheet_snapshot(naive, "2026-08-01:morning")
        wrong_slot_result = evaluate_sheet_snapshot(wrong_slot, "2026-08-01:morning")

        self.assertEqual(naive_result["state"], "invalid")
        self.assertTrue(any("timezone" in error for error in naive_result["errors"]))
        self.assertEqual(wrong_slot_result["state"], "waiting")
        self.assertEqual(wrong_slot_result["reason"], "l1_slot_mismatch")

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

    def test_sheet_mode_uses_all_rows_then_applies_exact_dedupe(self):
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

        self.assertEqual(len(result.payload["items"]), 1)
        self.assertEqual(result.stats["sheet_total"], 2)
        self.assertEqual(result.stats["duplicate_removed"], 1)
        self.assertEqual(result.stats["selected_total"], 1)
        self.assertEqual(result.stats["sheet_source"]["run_marker"], "19:15")
        self.assertEqual(result.stats["sheet_source"]["run_label"], "evening")

    def test_sheet_mode_skips_already_published_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "mih.db"
            brief_path = temp_path / "combined_brief.json"
            sheet_url = "https://docs.google.com/spreadsheets/d/sheet123/edit?gid=456#gid=456"
            csv_text = (
                "Date,Section,Topic,Headline,Vietnamese translation,Source,Source URL,Main summary,Main summary (Vietnamese),Why it matters,Why it matters (Vietnamese),07:30\n"
                "2026-06-24,Global,Safety,English A,TiÃƒÂªu Ã„â€˜Ã¡Â»Â A,Sheet Source,https://example.com/published,English summary,TÃƒÂ³m tÃ¡ÂºÂ¯t A,English impact,TÃƒÂ¡c Ã„â€˜Ã¡Â»â„¢ng A,\n"
                "2026-06-24,Global,Safety,English B,TiÃƒÂªu Ã„â€˜Ã¡Â»Â B,Sheet Source,https://example.com/fresh,English summary,TÃƒÂ³m tÃ¡ÂºÂ¯t B,English impact,TÃƒÂ¡c Ã„â€˜Ã¡Â»â„¢ng B,\n"
            )
            published_item = sheet_row_to_item(
                {
                    "Date": "2026-06-24",
                    "Headline": "English A",
                    "Vietnamese translation": "TiÃƒÂªu Ã„â€˜Ã¡Â»Â A",
                    "Source": "Sheet Source",
                    "Source URL": "https://example.com/published",
                    "Main summary": "English summary",
                    "Why it matters": "English impact",
                },
                1,
            )
            mark_items_published([published_item], facebook_page_id="page-1", db_path=db_path)

            result = build_combined_brief(
                source_mode="sheet",
                sheet_url=sheet_url,
                sheet_limit=None,
                card_limit=None,
                db_path=db_path,
                brief_path=brief_path,
                session=_FakeSession(csv_text),
            )

        self.assertEqual(len(result.payload["items"]), 1)
        self.assertEqual(result.payload["items"][0]["canonical_url"], "https://example.com/fresh")
        self.assertEqual(result.stats["raw_total"], 2)
        self.assertEqual(result.stats["sheet_total"], 2)
        self.assertEqual(result.stats["already_published"], 1)
        self.assertEqual(result.stats["selected_total"], 1)

    def test_sheet_mode_reports_empty_sheet_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "mih.db"
            brief_path = temp_path / "combined_brief.json"

            with patch("app.services.combined_brief_source.collect_backup_news", return_value=[]):
                result = build_combined_brief(source_mode="sheet", sheet_url="", db_path=db_path, brief_path=brief_path)

        self.assertEqual(result.payload["items"], [])
        self.assertEqual(result.stats["fallback_reason"], "sheet_empty")
        self.assertEqual(result.stats["backup_status"], "failed")
        self.assertEqual(result.stats["source_mode"], "sheet")

    def test_completed_empty_primary_is_invalid_and_does_not_mix_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            snapshot = parse_sheet_snapshot(
                _protocol_csv(
                    started_at="2026-08-01T07:15:00+07:00",
                    completed_at="2026-08-01T07:16:00+07:00",
                    run_id="2026-08-01:morning",
                    status="COMPLETED",
                    row_count="0",
                )
            )
            with patch("app.services.combined_brief_source.collect_backup_news") as backup:
                result = build_combined_brief(
                    source_mode="sheet",
                    sheet_url="https://docs.google.com/spreadsheets/d/sheet123/edit?gid=0",
                    sheet_snapshot=snapshot,
                    expected_run_id="2026-08-01:morning",
                    allow_backup=True,
                    db_path=temp_path / "mih.db",
                    brief_path=temp_path / "brief.json",
                )

        backup.assert_not_called()
        self.assertEqual(result.payload["items"], [])
        self.assertEqual(result.stats["sheet_source"]["evaluation"]["state"], "invalid")

    def test_build_reuses_supplied_snapshot_without_second_get(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            snapshot = parse_sheet_snapshot(
                _protocol_csv(
                    started_at="2026-08-01T07:15:00+07:00",
                    completed_at="2026-08-01T07:16:00+07:00",
                    run_id="2026-08-01:morning",
                    status="COMPLETED",
                    row_count="1",
                    rows=[_sheet_data_row("Current report", "https://example.com/current")],
                )
            )
            session = _FakeSession("must not be requested")
            result = build_combined_brief(
                source_mode="sheet",
                sheet_url="https://docs.google.com/spreadsheets/d/sheet123/edit?gid=0",
                sheet_snapshot=snapshot,
                expected_run_id="2026-08-01:morning",
                allow_backup=False,
                session=session,
                db_path=temp_path / "mih.db",
                brief_path=temp_path / "brief.json",
            )

        self.assertEqual(session.requested_urls, [])
        self.assertEqual(len(result.payload["items"]), 1)

    def test_backup_items_receive_one_final_exact_dedupe_and_complete_stats(self):
        duplicate_a = _item_with_title("backup", "Backup A", "https://example.com/duplicate", "Report A")
        duplicate_b = _item_with_title("backup", "Backup B", "https://example.com/duplicate?utm_source=rss", "Report B")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch(
                "app.services.combined_brief_source.collect_backup_news",
                return_value=[{"items": [duplicate_a, duplicate_b]}],
            ):
                result = build_combined_brief(
                    source_mode="sheet",
                    sheet_url="",
                    db_path=temp_path / "mih.db",
                    brief_path=temp_path / "brief.json",
                )

        self.assertEqual(len(result.payload["items"]), 1)
        self.assertEqual(result.stats["raw_total"], 2)
        self.assertEqual(result.stats["backup_total"], 2)
        self.assertEqual(result.stats["eligible_total"], 2)
        self.assertEqual(result.stats["duplicate_removed"], 1)
        self.assertEqual(result.stats["selected_total"], 1)
        self.assertEqual(result.stats["backup_status"], "ok")

    def test_all_backup_source_errors_are_not_reported_as_no_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch(
                "app.services.combined_brief_source.collect_backup_news",
                return_value=[
                    {"status": "error", "message": "timeout", "items": []},
                    {"status": "error", "message": "connection failed", "items": []},
                ],
            ):
                result = build_combined_brief(
                    source_mode="sheet",
                    sheet_url="",
                    db_path=temp_path / "mih.db",
                    brief_path=temp_path / "brief.json",
                )

        self.assertEqual(result.payload["items"], [])
        self.assertEqual(result.stats["backup_status"], "failed")
        self.assertEqual(result.stats["backup_failed_sources"], 2)

    def test_json_write_replaces_destination_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "brief.json"
            path.write_text('{"old": true}', encoding="utf-8")
            with patch("app.services.combined_brief_source.os.replace", wraps=os.replace) as replace:
                write_json_atomic(path, {"new": True})

            payload = json.loads(path.read_text(encoding="utf-8"))
            temporary_files = list(path.parent.glob(f".{path.name}.*.tmp"))

        self.assertEqual(payload, {"new": True})
        replace.assert_called_once()
        self.assertEqual(temporary_files, [])


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


def _item_with_title(source_type, source_name, url, title):
    item = _item(source_type, source_name, url)
    item["title"] = title
    item["title_hash"] = title_hash(title)
    item["item_key"] = item_key(item)
    return item


SHEET_HEADERS = [
    "Date",
    "Section",
    "Topic",
    "Headline",
    "Vietnamese translation",
    "Source",
    "Source URL",
    "Main summary",
    "Main summary (Vietnamese)",
    "Why it matters",
    "Why it matters (Vietnamese)",
]


def _sheet_data_row(title, url, date="2026-08-01"):
    return {
        "Date": date,
        "Section": "Global",
        "Topic": "Shipping",
        "Headline": title,
        "Vietnamese translation": f"Bản tin hàng hải mới về {title}",
        "Source": "Sheet Source",
        "Source URL": url,
        "Main summary": "Summary",
        "Main summary (Vietnamese)": "Bản tin cung cấp thông tin hàng hải mới và yêu cầu doanh nghiệp theo dõi kế hoạch khai thác.",
        "Why it matters": "Impact",
        "Why it matters (Vietnamese)": "Thông tin này ảnh hưởng trực tiếp đến lịch tàu và chi phí vận tải biển.",
    }


def _protocol_csv(
    *,
    started_at,
    run_id,
    status,
    completed_at="",
    row_count="",
    error_code="",
    rows=None,
):
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(SHEET_HEADERS + [started_at, completed_at, run_id, status, row_count, error_code])
    for row in rows or []:
        writer.writerow([row.get(header, "") for header in SHEET_HEADERS])
    return output.getvalue()


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
