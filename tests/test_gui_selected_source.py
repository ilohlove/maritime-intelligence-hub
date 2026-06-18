import unittest
from unittest.mock import Mock, patch

from app.gui import AppGUI


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
    app.retry_attempts_var = _Var("1")
    app._checkpoint = lambda _name: None
    app._int_var = lambda variable, default: int(variable.get() or default)
    app._retry_gui_step = lambda _name, action: action()
    app._format_card_result = AppGUI._format_card_result.__get__(app, AppGUI)
    app._format_selected_source_cards_result = AppGUI._format_selected_source_cards_result.__get__(app, AppGUI)
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
