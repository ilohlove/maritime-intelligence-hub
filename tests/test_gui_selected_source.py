import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from app.cli import _render_facebook_intro_text
from app.gui import AppGUI
from app.services.runtime_settings import (
    DEFAULT_FACEBOOK_INTRO_TEXT,
    LEGACY_FACEBOOK_INTRO_TEXT,
    PREVIOUS_DEFAULT_FACEBOOK_INTRO_TEXT,
    load_runtime_settings,
)
from app.services.facebook_publisher import FacebookAPIError


class GuiSelectedSourceTests(unittest.TestCase):
    def test_loop_scan_uses_selected_source_cards(self):
        app = _gui_stub()
        selected_result = _selected_result()
        app._generate_selected_source_cards_result = Mock(return_value=selected_result)
        app._generate_latest_cards_result = Mock(side_effect=AssertionError("latest cards should not be used"))

        with patch("app.gui.refresh_trends", return_value={"seeded": 0, "imported": 0, "fetched": 0}):
            with patch("app.gui.fetch_rss", return_value={"ok": True, "results": [{"inserted": 1}]}):
                with patch("app.gui.fetch_html", return_value={"results": [{"inserted": 0}]}):
                    with patch("app.gui.score_articles", return_value=[{"id": 1}]):
                        with patch("app.gui.summarize_articles", return_value=[{"article_id": 1}]):
                            with patch(
                                "app.gui.write_scan_brief",
                                return_value={
                                    "items": 1,
                                    "markdown_path": "morning.md",
                                    "latest_markdown_path": "latest.md",
                                },
                            ):
                                output, ok = AppGUI._task_run_scan(app, "morning")

        self.assertTrue(ok)
        app._generate_selected_source_cards_result.assert_called_once()
        app._generate_latest_cards_result.assert_not_called()
        self.assertIn("Source mode: sheet", output)
        self.assertIn("Loaded sheet items: 1", output)

    def test_send_cards_uses_selected_source_cards(self):
        app = _gui_stub()
        selected_result = _selected_result()
        app._generate_selected_source_cards_result = Mock(return_value=selected_result)
        app._generate_latest_cards_result = Mock(side_effect=AssertionError("latest cards should not be used"))
        app._task_send_cards = Mock(return_value=("sent", True))

        output, ok = AppGUI._task_send_latest_cards(app)

        self.assertTrue(ok)
        app._generate_selected_source_cards_result.assert_called_once()
        app._generate_latest_cards_result.assert_not_called()
        app._task_send_cards.assert_called_once_with(["card-1.png"], "evening")
        self.assertIn("Source mode: sheet", output)
        self.assertIn("sent", output)

    def test_loop_scan_sends_selected_source_cards(self):
        app = _gui_stub()
        app.send_telegram_var = _Var(True)
        selected_result = _selected_result()
        app._generate_selected_source_cards_result = Mock(return_value=selected_result)
        app._generate_latest_cards_result = Mock(side_effect=AssertionError("latest cards should not be used"))
        app._task_send_cards = Mock(return_value=("telegram sent", True))

        with patch("app.gui.refresh_trends", return_value={"seeded": 0, "imported": 0, "fetched": 0}):
            with patch("app.gui.fetch_rss", return_value={"ok": True, "results": [{"inserted": 1}]}):
                with patch("app.gui.fetch_html", return_value={"results": [{"inserted": 0}]}):
                    with patch("app.gui.score_articles", return_value=[{"id": 1}]):
                        with patch("app.gui.summarize_articles", return_value=[{"article_id": 1}]):
                            with patch(
                                "app.gui.write_scan_brief",
                                return_value={
                                    "items": 1,
                                    "markdown_path": "morning.md",
                                    "latest_markdown_path": "latest.md",
                                },
                            ):
                                output, ok = AppGUI._task_run_scan(app, "morning")

        self.assertTrue(ok)
        app._generate_selected_source_cards_result.assert_called_once()
        app._generate_latest_cards_result.assert_not_called()
        app._task_send_cards.assert_called_once_with(["card-1.png"], "evening")
        self.assertIn("telegram sent", output)

    def test_loop_scan_posts_facebook_with_selected_source_cards(self):
        app = _gui_stub()
        app.post_facebook_var = _Var(True)
        app.facebook_dry_run_var = _Var(True)
        selected_result = _selected_result()
        app._generate_selected_source_cards_result = Mock(return_value=selected_result)
        app._task_post_facebook_cards = Mock(return_value=("facebook dry-run", True))

        with patch("app.gui.refresh_trends", return_value={"seeded": 0, "imported": 0, "fetched": 0}):
            with patch("app.gui.fetch_rss", return_value={"ok": True, "results": [{"inserted": 1}]}):
                with patch("app.gui.fetch_html", return_value={"results": [{"inserted": 0}]}):
                    with patch("app.gui.score_articles", return_value=[{"id": 1}]):
                        with patch("app.gui.summarize_articles", return_value=[{"article_id": 1}]):
                            with patch(
                                "app.gui.write_scan_brief",
                                return_value={
                                    "items": 1,
                                    "markdown_path": "morning.md",
                                    "latest_markdown_path": "latest.md",
                                },
                            ):
                                output, ok = AppGUI._task_run_scan(app, "morning")

        self.assertTrue(ok)
        app._generate_selected_source_cards_result.assert_called_once()
        app._task_post_facebook_cards.assert_called_once_with(["card-1.png"], "evening", dry_run=True)
        self.assertIn("facebook dry-run", output)

    def test_loop_scan_reuses_cards_for_telegram_and_facebook(self):
        app = _gui_stub()
        app.send_telegram_var = _Var(True)
        app.post_facebook_var = _Var(True)
        selected_result = _selected_result()
        app._generate_selected_source_cards_result = Mock(return_value=selected_result)
        app._task_send_cards = Mock(return_value=("telegram sent", True))
        app._task_post_facebook_cards = Mock(return_value=("facebook sent", True))

        with patch("app.gui.refresh_trends", return_value={"seeded": 0, "imported": 0, "fetched": 0}):
            with patch("app.gui.fetch_rss", return_value={"ok": True, "results": [{"inserted": 1}]}):
                with patch("app.gui.fetch_html", return_value={"results": [{"inserted": 0}]}):
                    with patch("app.gui.score_articles", return_value=[{"id": 1}]):
                        with patch("app.gui.summarize_articles", return_value=[{"article_id": 1}]):
                            with patch(
                                "app.gui.write_scan_brief",
                                return_value={
                                    "items": 1,
                                    "markdown_path": "morning.md",
                                    "latest_markdown_path": "latest.md",
                                },
                            ):
                                output, ok = AppGUI._task_run_scan(app, "morning")

        self.assertTrue(ok)
        app._generate_selected_source_cards_result.assert_called_once()
        self.assertIn("telegram sent", output)
        self.assertIn("facebook sent", output)

    def test_facebook_dry_run_does_not_mark_published(self):
        app = _gui_stub()
        app.facebook_page_id_var = _Var("page-1")
        app.facebook_page_access_token_var = _Var("token")
        app.facebook_dry_run_var = _Var(True)
        app.facebook_intro_text_var = _Var("{date}")
        app._vietnam_now = Mock(return_value=_FakeNow("2026-06-18 07:15"))
        cards = [
            {
                "card_path": "card-1.png",
                "source_name": "Source",
                "original_url": "https://example.com/story",
                "item_key": "url:test",
            }
        ]

        with patch("app.gui.publish_photo_post", return_value={
            "dry_run": True,
            "page_id": "page-1",
            "message": "caption",
            "image_paths": ["card-1.png"],
        }) as publish:
            with patch("app.gui.mark_items_published") as mark:
                output, ok = AppGUI._task_post_facebook_cards(app, cards, "morning", dry_run=True)

        self.assertTrue(ok)
        publish.assert_called_once()
        mark.assert_not_called()
        self.assertIn("Facebook dry-run: no post created", output)

    def test_facebook_publish_marks_items_with_post_id(self):
        app = _gui_stub()
        app.facebook_page_id_var = _Var("page-1")
        app.facebook_page_access_token_var = _Var("token")
        app.facebook_dry_run_var = _Var(False)
        app.facebook_intro_text_var = _Var("{date}")
        app._vietnam_now = Mock(return_value=_FakeNow("2026-06-18 07:15"))
        cards = [
            {
                "card_path": "card-1.png",
                "source_name": "Source",
                "original_url": "https://example.com/story",
                "item_key": "url:test",
            }
        ]

        with patch("app.gui.publish_photo_post", return_value={
            "dry_run": False,
            "page_id": "page-1",
            "post_id": "post-1",
            "uploaded_photo_ids": ["photo-1"],
            "fallback": False,
        }):
            with patch("app.gui.mark_items_published", return_value=1) as mark:
                output, ok = AppGUI._task_post_facebook_cards(app, cards, "morning", dry_run=False)

        self.assertTrue(ok)
        mark.assert_called_once_with(cards, facebook_page_id="page-1", facebook_post_id="post-1")
        self.assertIn("Facebook published", output)

    def test_facebook_preview_card_generation_error_is_user_friendly(self):
        app = _gui_stub()
        app._generate_selected_source_cards_result = Mock(side_effect=RuntimeError("No image cards available"))
        app._task_post_facebook_cards = Mock()

        output, ok = AppGUI._task_preview_facebook_post(app)

        self.assertFalse(ok)
        self.assertTrue(output.startswith("Facebook skipped: could not generate image cards."))
        self.assertIn("No image cards available", output)
        self.assertNotIn("Traceback", output)
        app._task_post_facebook_cards.assert_not_called()

    def test_facebook_post_card_generation_error_is_user_friendly(self):
        app = _gui_stub()
        app._generate_selected_source_cards_result = Mock(side_effect=RuntimeError("Playwright browser is missing"))
        app._task_post_facebook_cards = Mock()

        output, ok = AppGUI._task_post_facebook_now(app)

        self.assertFalse(ok)
        self.assertTrue(output.startswith("Facebook skipped: could not generate image cards."))
        self.assertIn("Playwright browser is missing", output)
        self.assertNotIn("Traceback", output)
        app._task_post_facebook_cards.assert_not_called()

    def test_facebook_page_check_reports_oauth_diagnostics(self):
        app = _gui_stub()
        error = FacebookAPIError(
            "400: Invalid OAuth access token - Cannot parse access token",
            status_code=400,
            error_type="OAuthException",
            error_code=190,
        )

        with patch("app.gui.check_page", side_effect=error):
            output, ok = AppGUI._task_check_facebook_page(app, "page-1", "short-token")

        self.assertFalse(ok)
        self.assertIn("Facebook page check failed", output)
        self.assertIn("error_code=190", output)
        self.assertIn("token looks too short", output)
        self.assertIn("create a new Facebook Page access token", output)

    def test_facebook_page_check_failure_popup_uses_diagnostics(self):
        app = _gui_stub()
        output = (
            "Facebook page check failed:\n"
            "400: Invalid OAuth access token - Cannot parse access token\n"
            "Facebook details: status_code=400, error_type=OAuthException, error_code=190\n"
            "Action: create a new Facebook Page access token for this Page."
        )

        message = AppGUI._task_failure_popup_message(app, "Checking Facebook page", output)

        self.assertIn("error_code=190", message)
        self.assertIn("create a new Facebook Page access token", message)

    def test_facebook_post_failure_popup_uses_post_error_section(self):
        app = _gui_stub()
        output = (
            "Generated 3 image cards\n"
            "Output: temp/cards\n\n"
            "Facebook post failed:\n"
            "400: Permissions error\n"
            "Facebook details: status_code=400, error_type=OAuthException, error_code=200"
        )

        message = AppGUI._task_failure_popup_message(app, "Posting Facebook cards", output)

        self.assertTrue(message.startswith("Facebook post failed:"))
        self.assertIn("error_code=200", message)
        self.assertNotIn("Generated 3 image cards", message)

    def test_facebook_preview_failure_popup_uses_skipped_section(self):
        app = _gui_stub()
        output = (
            "Generated 0 image cards\n\n"
            "Facebook skipped: publish safety failed.\n"
            "- Card 1: missing original_url"
        )

        message = AppGUI._task_failure_popup_message(app, "Previewing Facebook post", output)

        self.assertTrue(message.startswith("Facebook skipped:"))
        self.assertIn("missing original_url", message)

    def test_non_facebook_failure_popup_keeps_generic_message(self):
        app = _gui_stub()

        message = AppGUI._task_failure_popup_message(app, "Validating source master", "Detailed validation output")

        self.assertEqual(message, "The task did not complete successfully. See output for details.")

    def test_facebook_intro_text_uses_morning_caption(self):
        app = _gui_stub()
        app.facebook_intro_text_var = _Var(DEFAULT_FACEBOOK_INTRO_TEXT)
        app._vietnam_now = Mock(return_value=datetime(2026, 6, 18, 7, 30))

        caption = AppGUI._facebook_intro_text(app, "morning")

        self.assertIn("Tóm tắt nhanh cho anh em những chuyển động đáng chú ý nhất", caption)
        self.assertIn("Khung giờ phát sóng: 7:30 và 19:30 mỗi ngày.", caption)
        self.assertIn("Nhớ ấn Follow và thêm trang vào Yêu thích", caption)
        self.assertNotIn("#MaritimeBrief", caption)

    def test_facebook_intro_text_uses_evening_caption(self):
        app = _gui_stub()
        app.facebook_intro_text_var = _Var(DEFAULT_FACEBOOK_INTRO_TEXT)
        app._vietnam_now = Mock(return_value=datetime(2026, 6, 18, 19, 30))

        caption = AppGUI._facebook_intro_text(app, "evening")

        self.assertEqual(caption, DEFAULT_FACEBOOK_INTRO_TEXT)

    def test_facebook_intro_text_chooses_period_from_time_without_label(self):
        app = _gui_stub()
        app.facebook_intro_text_var = _Var(DEFAULT_FACEBOOK_INTRO_TEXT)
        app._vietnam_now = Mock(return_value=datetime(2026, 6, 18, 19, 30))

        caption = AppGUI._facebook_intro_text(app)

        self.assertEqual(caption, DEFAULT_FACEBOOK_INTRO_TEXT)

    def test_facebook_intro_text_preserves_custom_template(self):
        app = _gui_stub()
        app.facebook_intro_text_var = _Var("Custom {brief_label} {date}")
        app._vietnam_now = Mock(return_value=datetime(2026, 6, 18, 7, 30))

        caption = AppGUI._facebook_intro_text(app, "morning")

        self.assertEqual(caption, "Custom Bản tin buổi sáng 18/06/2026")

    def test_cli_facebook_intro_text_matches_default_caption(self):
        caption = _render_facebook_intro_text(DEFAULT_FACEBOOK_INTRO_TEXT, "evening")

        self.assertEqual(caption, DEFAULT_FACEBOOK_INTRO_TEXT)
        self.assertIn("Khung giờ phát sóng: 7:30 và 19:30 mỗi ngày.", caption)

    def test_cli_facebook_intro_text_migrates_legacy_default_caption(self):
        caption = _render_facebook_intro_text(LEGACY_FACEBOOK_INTRO_TEXT, "morning")

        self.assertEqual(caption, DEFAULT_FACEBOOK_INTRO_TEXT)
        self.assertNotIn("Nguon duoc ghi tren tung anh", caption)

    def test_runtime_settings_migrates_previous_default_caption(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "runtime_settings.json"
            settings_path.write_text(
                json.dumps({"publish": {"facebook_intro_text": PREVIOUS_DEFAULT_FACEBOOK_INTRO_TEXT}}),
                encoding="utf-8",
            )

            settings = load_runtime_settings(settings_path)

        self.assertEqual(settings["publish"]["facebook_intro_text"], DEFAULT_FACEBOOK_INTRO_TEXT)

    def test_wait_for_sheet_l1_until_matching_current_brief(self):
        app = _gui_stub()
        app.sheet_url_var = _Var("https://docs.google.com/spreadsheets/d/sheet123/edit?gid=0#gid=0")
        app._current_scan_label = Mock(return_value="evening")
        app._vietnam_now = Mock(return_value=_FakeNow("2026-06-18 19:16:00 +07"))
        app._sleep_with_controls = Mock()

        with patch(
            "app.gui.get_sheet_run_status",
            side_effect=[
                {"run_marker": "08:00", "run_label": "morning"},
                {"run_marker": "19:20", "run_label": "evening"},
            ],
        ) as status:
            result = AppGUI._wait_for_sheet_if_needed(app, "sheet")

        self.assertEqual(status.call_count, 2)
        app._sleep_with_controls.assert_called_once_with(60)
        self.assertTrue(result["sheet_ready"])
        self.assertEqual(result["run_label"], "evening")

    def test_telegram_intro_text_prefixes_vietnamese_brief_label(self):
        app = _gui_stub()
        app.telegram_intro_text_var = _Var("{date}")
        app._vietnam_now = Mock(return_value=_FakeNow("2026-06-18 19:30"))

        intro = AppGUI._telegram_intro_text(app, "evening")

        self.assertEqual(intro, "Bản tin buổi tối\n2026-06-18 19:30")

    def test_telegram_intro_text_uses_brief_label_placeholder_once(self):
        app = _gui_stub()
        app.telegram_intro_text_var = _Var("{brief_label} - {datetime}")
        app._vietnam_now = Mock(return_value=_FakeNow("2026-06-18 07:15"))

        intro = AppGUI._telegram_intro_text(app, "morning")

        self.assertEqual(intro, "Bản tin buổi sáng - 2026-06-18 07:15")


def _gui_stub():
    app = AppGUI.__new__(AppGUI)
    app.priority_var = _Var("P1")
    app.limit_var = _Var("10")
    app.min_score_var = _Var("6")
    app.force_summary_var = _Var(False)
    app.brief_limit_var = _Var("12")
    app.create_image_cards_var = _Var(True)
    app.send_telegram_var = _Var(False)
    app.post_facebook_var = _Var(False)
    app.facebook_dry_run_var = _Var(True)
    app.retry_attempts_var = _Var("1")
    app._checkpoint = lambda _name: None
    app._int_var = lambda variable, default: int(variable.get() or default)
    app._var_bool = AppGUI._var_bool.__get__(app, AppGUI)
    app._retry_gui_step = lambda _name, action: action()
    app._format_card_result = AppGUI._format_card_result.__get__(app, AppGUI)
    app._format_selected_source_cards_result = AppGUI._format_selected_source_cards_result.__get__(app, AppGUI)
    app._format_errors = AppGUI._format_errors.__get__(app, AppGUI)
    app._brief_label_text = AppGUI._brief_label_text.__get__(app, AppGUI)
    app._facebook_intro_text = AppGUI._facebook_intro_text.__get__(app, AppGUI)
    return app


def _selected_result():
    return {
        "brief_path": "combined_brief.json",
        "source_stats": {
            "source_mode": "sheet",
            "app_total": 0,
            "sheet_total": 1,
            "raw_total": 1,
            "already_published": 0,
            "duplicate_removed": 0,
            "eligible_total": 1,
            "selected_total": 1,
            "duplicate_groups": [],
            "sheet_source": {
                "sheet_url": "https://docs.google.com/spreadsheets/d/sheet123/edit?gid=0#gid=0",
                "csv_url": "https://docs.google.com/spreadsheets/d/sheet123/export?format=csv&gid=0",
                "loaded_items": 1,
            },
        },
        "cards_result": {
            "items": 1,
            "output_dir": "cards",
            "manifest_path": "cards/manifest.json",
            "preview_path": "cards/preview.html",
            "cards": ["card-1.png"],
        },
        "brief_label": "evening",
    }


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _FakeNow:
    def __init__(self, formatted):
        self.formatted = formatted

    def strftime(self, _format):
        return self.formatted


if __name__ == "__main__":
    unittest.main()
