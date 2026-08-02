import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from app.cli import _render_facebook_intro_text
from app.gui import AppGUI
from app.services.runtime_settings import (
    DEFAULT_FACEBOOK_INTRO_TEXT,
    LEGACY_FACEBOOK_INTRO_TEXT,
    RECENT_DEFAULT_FACEBOOK_INTRO_TEXT,
    load_runtime_settings,
)
from app.services.facebook_publisher import FacebookAPIError


class GuiSelectedSourceTests(unittest.TestCase):
    def test_selecting_app_source_is_preview_only(self):
        app = _gui_stub()
        app._update_visual_limit_states = Mock()
        app._save_settings = Mock()
        app._run_background = Mock()

        AppGUI._on_visual_source_mode_changed(app, "app")

        app._update_visual_limit_states.assert_called_once()
        app._save_settings.assert_called_once()
        app._run_background.assert_not_called()

    def test_selecting_non_app_source_does_not_start_pipeline(self):
        app = _gui_stub()
        app._update_visual_limit_states = Mock()
        app._save_settings = Mock()
        app._run_background = Mock()

        AppGUI._on_visual_source_mode_changed(app, "sheet")

        app._update_visual_limit_states.assert_called_once()
        app._save_settings.assert_called_once()
        app._run_background.assert_not_called()

    def test_generate_app_cards_refreshes_pipeline_before_render(self):
        app = _gui_stub()
        app.visual_source_mode_var = _Var("app")
        app._refresh_app_source_if_selected = Mock(return_value=_pipeline_result())
        app._generate_combined_cards_result = Mock(return_value=_selected_result())

        output, ok = AppGUI._task_generate_combined_cards(app)

        self.assertTrue(ok)
        app._refresh_app_source_if_selected.assert_called_once()
        app._generate_combined_cards_result.assert_called_once_with(test_mode=True)
        self.assertIn("App news refreshed: evening", output)
        self.assertIn("AI summaries: 1", output)

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

    def test_send_cards_uses_latest_rendered_cards(self):
        app = _gui_stub()
        selected_result = _selected_result()
        app._latest_rendered_cards_result = Mock(return_value=selected_result)
        app._generate_selected_source_cards_result = Mock(side_effect=AssertionError("manual send should not render"))
        app._generate_latest_cards_result = Mock(side_effect=AssertionError("latest cards should not be used"))
        app._task_send_cards = Mock(return_value=("sent", True))

        output, ok = AppGUI._task_send_latest_cards(app)

        self.assertTrue(ok)
        app._latest_rendered_cards_result.assert_called_once()
        app._generate_selected_source_cards_result.assert_not_called()
        app._generate_latest_cards_result.assert_not_called()
        app._task_send_cards.assert_called_once_with(["card-1.png"], "evening")
        self.assertIn("Source mode: sheet", output)
        self.assertIn("sent", output)

    def test_preview_only_sheet_cards_cannot_be_sent(self):
        app = _gui_stub()
        selected_result = _selected_result()
        selected_result["preview_only"] = True
        app._latest_rendered_cards_result = Mock(return_value=selected_result)
        app._task_send_cards = Mock()

        output, ok = AppGUI._task_send_latest_cards(app)

        self.assertFalse(ok)
        self.assertIn("preview-only", output)
        app._task_send_cards.assert_not_called()

    def test_coordinated_latest_cards_use_delivery_claims(self):
        app = _gui_stub()
        selected_result = _selected_result()
        selected_result["run_id"] = "2026-08-01:morning"
        app._latest_rendered_cards_result = Mock(return_value=selected_result)
        app._manual_delivery_owner = Mock(return_value="gui-worker")
        app._task_send_cards = Mock(return_value=("sent", True))

        _output, ok = AppGUI._task_send_latest_cards(app)

        self.assertTrue(ok)
        app._task_send_cards.assert_called_once_with(
            ["card-1.png"],
            "evening",
            run_id="2026-08-01:morning",
            run_owner="gui-worker",
        )

    def test_latest_rendered_cards_result_reads_newest_manifest(self):
        app = _gui_stub()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            older_card = root / "older" / "card_01.png"
            newer_card = root / "newer" / "card_01.png"
            older_card.parent.mkdir(parents=True)
            newer_card.parent.mkdir(parents=True)
            older_card.write_bytes(b"old")
            newer_card.write_bytes(b"new")
            brief_path = root / "combined_brief.json"
            brief_path.write_text(json.dumps({"scan_label": "evening"}), encoding="utf-8")
            older_manifest = older_card.parent / "manifest.json"
            newer_manifest = newer_card.parent / "manifest.json"
            older_manifest.write_text(json.dumps({
                "brief_type": "combined",
                "generated_at": "2026-06-24T07:30:00",
                "source_brief_json": str(brief_path),
                "preview_path": str(older_card.parent / "preview.html"),
                "cards": [{"card_path": str(older_card), "original_url": "https://example.com/old"}],
            }), encoding="utf-8")
            newer_manifest.write_text(json.dumps({
                "brief_type": "combined",
                "generated_at": "2026-06-25T07:30:00",
                "source_brief_json": str(brief_path),
                "preview_path": str(newer_card.parent / "preview.html"),
                "cards": [{"card_path": str(newer_card), "original_url": "https://example.com/new"}],
            }), encoding="utf-8")
            os.utime(older_manifest, (1000, 1000))
            os.utime(newer_manifest, (2000, 2000))

            with patch("app.gui.DEFAULT_VISUAL_BRIEF_DIR", root), patch("app.gui.ROOT_DIR", root):
                result = AppGUI._latest_rendered_cards_result(app)

        self.assertTrue(result["latest_rendered"])
        self.assertEqual(result["brief_label"], "evening")
        self.assertEqual(result["cards_result"]["manifest_path"], newer_manifest)
        self.assertEqual(result["cards_result"]["cards"][0]["original_url"], "https://example.com/new")

    def test_latest_rendered_cards_result_rejects_missing_card_file(self):
        app = _gui_stub()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "combined" / "run"
            run_dir.mkdir(parents=True)
            manifest = run_dir / "manifest.json"
            manifest.write_text(json.dumps({
                "brief_type": "combined",
                "generated_at": "2026-06-25T07:30:00",
                "source_brief_json": "combined_brief.json",
                "preview_path": str(run_dir / "preview.html"),
                "cards": [{"card_path": str(run_dir / "missing.png"), "original_url": "https://example.com/story"}],
            }), encoding="utf-8")

            with patch("app.gui.DEFAULT_VISUAL_BRIEF_DIR", root), patch("app.gui.ROOT_DIR", root):
                with self.assertRaisesRegex(RuntimeError, "Generate Test"):
                    AppGUI._latest_rendered_cards_result(app)

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

    def test_loop_scan_posts_facebook_groups_with_selected_source_cards(self):
        app = _gui_stub()
        app.post_facebook_groups_var = _Var(True)
        app.facebook_group_dry_run_var = _Var(True)
        selected_result = _selected_result()
        app._generate_selected_source_cards_result = Mock(return_value=selected_result)
        app._task_post_facebook_groups_cards = Mock(return_value=("groups dry-run", True))

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
        app._task_post_facebook_groups_cards.assert_called_once_with(
            ["card-1.png"], "evening", dry_run=True
        )
        self.assertIn("groups dry-run", output)

    def test_group_publish_marks_items_after_pending_delivery(self):
        app = _gui_stub()
        app.facebook_groups = [
            {
                "id": "group-1",
                "name": "Group",
                "url": "facebook.com/groups/group-1",
                "enabled": True,
                "caption_template": "Group caption",
            }
        ]
        app.facebook_intro_text_var = _Var("{brief_label}")
        app.facebook_group_delay_min_var = _Var("60")
        app.facebook_group_delay_max_var = _Var("120")
        app.facebook_group_dry_run_var = _Var(False)
        cards = [
            {
                "card_path": "card.png",
                "item_key": "item-1",
                "source_name": "Source",
                "original_url": "https://example.com/story",
            }
        ]
        publish_result = {
            "batch_id": "batch-1",
            "counts": {
                "published": 0,
                "pending": 1,
                "failed": 0,
                "needs_login": 0,
                "queued": 0,
                "skipped": 0,
                "dry_run": 0,
            },
            "results": [{"group_name": "Group", "status": "pending", "message": "Awaiting approval"}],
            "safety": {"used_today": 1, "daily_limit": 4, "remaining_today": 3, "queued_total": 0},
        }

        with patch("app.gui.publish_to_groups", return_value=publish_result) as publish:
            with patch("app.gui.mark_items_published", return_value=1) as mark:
                output, ok = AppGUI._task_post_facebook_groups_cards(app, cards, "morning", dry_run=False)

        self.assertTrue(ok)
        publish.assert_called_once()
        mark.assert_called_once_with(cards)
        self.assertIn("Pending: 1", output)

    def test_manual_group_button_processes_only_manual_queue_path(self):
        app = _gui_stub()
        app._latest_rendered_cards_result = Mock(return_value=_selected_result())
        app._task_post_facebook_groups_cards = Mock(return_value=("one queued group", True))

        output, ok = AppGUI._task_post_facebook_groups_now(app, dry_run=False)

        self.assertTrue(ok)
        app._task_post_facebook_groups_cards.assert_called_once_with(
            ["card-1.png"], "evening", dry_run=False, manual=True
        )
        self.assertIn("one queued group", output)

    def test_scheduler_auto_resumes_due_queue_item(self):
        app = _gui_stub()
        app.auto_run_var = _Var(False)
        app.task_running = False
        app.facebook_group_auto_resume_var = _Var(True)
        app.root = Mock()
        app._update_next_run_label = Mock()
        app._run_background = Mock()

        with patch("app.gui.get_group_queue_status", return_value={"remaining_today": 1}):
            with patch("app.gui.get_due_queue_item", return_value={"id": 42}):
                AppGUI._scheduler_tick(app)

        app._run_background.assert_called_once()
        self.assertIn("Resuming scheduled", app._run_background.call_args.args[0])
        app.root.after.assert_called_once_with(30000, app._scheduler_tick)

    def test_queue_manager_publishes_selected_delivery(self):
        app = _gui_stub()
        app.root = Mock()
        result = {
            "delivery_id": 7,
            "group_name": "Maritime Group",
            "status": "pending",
            "message": "Awaiting approval",
        }

        with patch("app.gui.publish_queue_item", return_value=result) as publish:
            output, ok = AppGUI._task_publish_queue_item(app, 7)

        self.assertTrue(ok)
        publish.assert_called_once_with(7, max_groups_per_day=4)
        self.assertIn("Maritime Group", output)

    def test_group_card_capture_reads_multiline_caption_widget(self):
        app = _gui_stub()
        app.facebook_group_rows = [
            {
                "id": "maritime-group",
                "original_url": "https://www.facebook.com/groups/123456",
                "enabled": _Var(True),
                "priority": _Var("10"),
                "name": _Var("Maritime Group"),
                "url": _Var("https://www.facebook.com/groups/123456"),
                "caption_widget": _Textbox("Dòng một\nDòng hai\n"),
            }
        ]

        captured = AppGUI._capture_facebook_group_rows(app, show_errors=False)

        self.assertTrue(captured)
        self.assertEqual(app.facebook_groups[0]["caption_template"], "Dòng một\nDòng hai")
        self.assertEqual(app.facebook_groups[0]["id"], "maritime-group")

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
            "photo_descriptions": ["Link nguồn: https://example.com/story"],
        }) as publish:
            with patch("app.gui.mark_items_published") as mark:
                output, ok = AppGUI._task_post_facebook_cards(app, cards, "morning", dry_run=True)

        self.assertTrue(ok)
        publish.assert_called_once()
        mark.assert_not_called()
        self.assertIn("Facebook dry-run: no post created", output)
        self.assertIn("Photo descriptions planned: 1", output)

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
            "photo_descriptions": ["Link nguồn: https://example.com/story"],
            "fallback": False,
        }):
            with patch("app.gui.mark_items_published", return_value=1) as mark:
                output, ok = AppGUI._task_post_facebook_cards(app, cards, "morning", dry_run=False)

        self.assertTrue(ok)
        mark.assert_called_once_with(cards, facebook_page_id="page-1", facebook_post_id="post-1")
        self.assertIn("Facebook published", output)
        self.assertIn("Photo descriptions with source links: 1", output)

    def test_scheduled_facebook_claims_delivery_before_publish(self):
        app = _gui_stub()
        app.facebook_page_id_var = _Var("page-1")
        app.facebook_page_access_token_var = _Var("token")
        app.facebook_dry_run_var = _Var(False)
        app.facebook_intro_text_var = _Var("{date}")
        app._vietnam_now = Mock(return_value=_FakeNow("2026-08-01 07:15"))
        cards = [
            {
                "card_path": "card-1.png",
                "source_name": "Source",
                "original_url": "https://example.com/story",
                "item_key": "url:test",
            }
        ]

        with patch("app.gui.claim_delivery_cards", return_value={"claimed": cards, "skipped": []}) as claim:
            with patch(
                "app.gui.publish_photo_post",
                return_value={
                    "dry_run": False,
                    "page_id": "page-1",
                    "post_id": "post-1",
                    "uploaded_photo_ids": ["photo-1"],
                    "photo_descriptions": [],
                    "fallback": False,
                },
            ) as publish:
                with patch("app.gui.finish_delivery_cards", return_value=1) as finish:
                    with patch("app.gui.mark_items_published", return_value=1):
                        output, ok = AppGUI._task_post_facebook_cards(
                            app,
                            cards,
                            "morning",
                            dry_run=False,
                            run_id="2026-08-01:morning",
                            run_owner="worker-a",
                        )

        self.assertTrue(ok)
        claim.assert_called_once()
        publish.assert_called_once()
        self.assertTrue(finish.call_args.kwargs["succeeded"])
        self.assertIn("Facebook published", output)

    def test_scheduled_facebook_uncertain_failure_requires_review(self):
        app = _gui_stub()
        app.facebook_page_id_var = _Var("page-1")
        app.facebook_page_access_token_var = _Var("token")
        app.facebook_dry_run_var = _Var(False)
        app.facebook_intro_text_var = _Var("{date}")
        app._vietnam_now = Mock(return_value=_FakeNow("2026-08-01 07:15"))
        cards = [
            {
                "card_path": "card-1.png",
                "source_name": "Source",
                "original_url": "https://example.com/story",
                "item_key": "url:test",
            }
        ]

        with patch("app.gui.claim_delivery_cards", return_value={"claimed": cards, "skipped": []}):
            with patch("app.gui.publish_photo_post", side_effect=RuntimeError("connection lost")):
                with patch("app.gui.finish_delivery_cards", return_value=1) as finish:
                    output, ok = AppGUI._task_post_facebook_cards(
                        app,
                        cards,
                        "morning",
                        dry_run=False,
                        run_id="2026-08-01:morning",
                        run_owner="worker-a",
                    )

        self.assertFalse(ok)
        self.assertFalse(finish.call_args.kwargs["succeeded"])
        self.assertIn("connection lost", output)

    def test_scheduled_facebook_needs_review_is_not_reported_successful(self):
        app = _gui_stub()
        app.facebook_page_id_var = _Var("page-1")
        app.facebook_page_access_token_var = _Var("token")
        app.facebook_dry_run_var = _Var(False)
        cards = [
            {
                "card_path": "card-1.png",
                "item_key": "url:test",
                "source_name": "Source",
                "original_url": "https://example.com/story",
            }
        ]
        claims = {
            "claimed": [],
            "skipped": [{"card": cards[0], "reason": "needs_review"}],
        }

        with patch("app.gui.claim_delivery_cards", return_value=claims):
            with patch("app.gui.publish_photo_post") as publish:
                output, ok = AppGUI._task_post_facebook_cards(
                    app,
                    cards,
                    "morning",
                    dry_run=False,
                    run_id="2026-08-01:morning",
                    run_owner="worker-a",
                )

        self.assertFalse(ok)
        self.assertIn("needs_review", output)
        publish.assert_not_called()

    def test_facebook_preview_uses_latest_rendered_cards_without_rendering(self):
        app = _gui_stub()
        selected_result = _selected_result()
        app._latest_rendered_cards_result = Mock(return_value=selected_result)
        app._generate_selected_source_cards_result = Mock(side_effect=AssertionError("manual preview should not render"))
        app._task_post_facebook_cards = Mock(return_value=("facebook preview", True))

        output, ok = AppGUI._task_preview_facebook_post(app)

        self.assertTrue(ok)
        app._latest_rendered_cards_result.assert_called_once()
        app._generate_selected_source_cards_result.assert_not_called()
        app._task_post_facebook_cards.assert_called_once_with(["card-1.png"], "evening", dry_run=True)
        self.assertIn("facebook preview", output)

    def test_facebook_post_uses_latest_rendered_cards_without_rendering(self):
        app = _gui_stub()
        app.facebook_dry_run_var = _Var(True)
        selected_result = _selected_result()
        app._latest_rendered_cards_result = Mock(return_value=selected_result)
        app._generate_selected_source_cards_result = Mock(side_effect=AssertionError("manual post should not render"))
        app._task_post_facebook_cards = Mock(return_value=("facebook post", True))

        output, ok = AppGUI._task_post_facebook_now(app)

        self.assertTrue(ok)
        app._latest_rendered_cards_result.assert_called_once()
        app._generate_selected_source_cards_result.assert_not_called()
        app._task_post_facebook_cards.assert_called_once_with(["card-1.png"], "evening", dry_run=False)
        self.assertIn("facebook post", output)

    def test_facebook_preview_card_generation_error_is_user_friendly(self):
        app = _gui_stub()
        app._latest_rendered_cards_result = Mock(side_effect=RuntimeError("No image cards available"))
        app._generate_selected_source_cards_result = Mock(side_effect=AssertionError("manual preview should not render"))
        app._task_post_facebook_cards = Mock()

        output, ok = AppGUI._task_preview_facebook_post(app)

        self.assertFalse(ok)
        self.assertTrue(output.startswith("Facebook skipped: no valid latest rendered image cards."))
        self.assertIn("No image cards available", output)
        self.assertNotIn("Traceback", output)
        app._generate_selected_source_cards_result.assert_not_called()
        app._task_post_facebook_cards.assert_not_called()

    def test_facebook_post_card_generation_error_is_user_friendly(self):
        app = _gui_stub()
        app._latest_rendered_cards_result = Mock(side_effect=RuntimeError("Playwright browser is missing"))
        app._generate_selected_source_cards_result = Mock(side_effect=AssertionError("manual post should not render"))
        app._task_post_facebook_cards = Mock()

        output, ok = AppGUI._task_post_facebook_now(app)

        self.assertFalse(ok)
        self.assertTrue(output.startswith("Facebook skipped: no valid latest rendered image cards."))
        self.assertIn("Playwright browser is missing", output)
        self.assertNotIn("Traceback", output)
        app._generate_selected_source_cards_result.assert_not_called()
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

        self.assertIn("ĐIỂM TIN HÀNG HẢI BUỔI SÁNG", caption)
        self.assertIn("Cập nhật lúc 07:15 và 19:15 mỗi ngày.", caption)
        self.assertIn("#MaritimeBrief", caption)

    def test_facebook_intro_text_uses_evening_caption(self):
        app = _gui_stub()
        app.facebook_intro_text_var = _Var(DEFAULT_FACEBOOK_INTRO_TEXT)
        app._vietnam_now = Mock(return_value=datetime(2026, 6, 18, 19, 30))

        caption = AppGUI._facebook_intro_text(app, "evening")

        self.assertIn("ĐIỂM TIN HÀNG HẢI BUỔI TỐI", caption)
        self.assertIn("#MaritimeIntelligenceHub", caption)

    def test_facebook_intro_text_chooses_period_from_time_without_label(self):
        app = _gui_stub()
        app.facebook_intro_text_var = _Var(DEFAULT_FACEBOOK_INTRO_TEXT)
        app._vietnam_now = Mock(return_value=datetime(2026, 6, 18, 19, 30))

        caption = AppGUI._facebook_intro_text(app)

        self.assertIn("ĐIỂM TIN HÀNG HẢI BUỔI TỐI", caption)

    def test_facebook_intro_text_preserves_custom_template(self):
        app = _gui_stub()
        app.facebook_intro_text_var = _Var("Custom {brief_label} {date}")
        app._vietnam_now = Mock(return_value=datetime(2026, 6, 18, 7, 30))

        caption = AppGUI._facebook_intro_text(app, "morning")

        self.assertEqual(caption, "Custom Bản tin buổi sáng 18/06/2026")

    def test_cli_facebook_intro_text_matches_default_caption(self):
        caption = _render_facebook_intro_text(DEFAULT_FACEBOOK_INTRO_TEXT, "evening")

        self.assertIn("ĐIỂM TIN HÀNG HẢI BUỔI TỐI", caption)
        self.assertIn("Cập nhật lúc 07:15 và 19:15 mỗi ngày.", caption)
        self.assertIn("#MaritimeBrief", caption)

    def test_cli_facebook_intro_text_migrates_legacy_default_caption(self):
        caption = _render_facebook_intro_text(LEGACY_FACEBOOK_INTRO_TEXT, "morning")

        self.assertIn("ĐIỂM TIN HÀNG HẢI BUỔI SÁNG", caption)
        self.assertIn("#MaritimeBrief", caption)
        self.assertNotIn("Nguon duoc ghi tren tung anh", caption)

    def test_runtime_settings_migrates_recent_default_caption(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "runtime_settings.json"
            settings_path.write_text(
                json.dumps({"publish": {"facebook_intro_text": RECENT_DEFAULT_FACEBOOK_INTRO_TEXT}}),
                encoding="utf-8",
            )

            settings = load_runtime_settings(settings_path)

        self.assertEqual(settings["publish"]["facebook_intro_text"], DEFAULT_FACEBOOK_INTRO_TEXT)

    def test_runtime_settings_migrates_old_group_delay_to_safe_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "runtime_settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "publish": {
                            "facebook_group_delay_min_seconds": 60,
                            "facebook_group_delay_max_seconds": 120,
                        }
                    }
                ),
                encoding="utf-8",
            )

            settings = load_runtime_settings(settings_path)

        self.assertEqual(settings["publish"]["facebook_group_delay_min_seconds"], 900)
        self.assertEqual(settings["publish"]["facebook_group_delay_max_seconds"], 1800)
        self.assertEqual(settings["publish"]["facebook_group_max_per_brief"], 2)
        self.assertEqual(settings["publish"]["facebook_group_max_per_day"], 4)
        self.assertEqual(settings["publish"]["facebook_group_queue_expiry_hours"], 12)
        self.assertTrue(settings["publish"]["facebook_group_auto_resume_queue"])

    def test_run_now_uses_latest_started_slot_not_future_evening(self):
        app = _gui_stub()
        app._schedule_now = Mock(return_value=datetime(2026, 8, 1, 12, 0))
        app._schedule_times = Mock(return_value=["07:15", "19:15"])

        label, scheduled = AppGUI._latest_started_schedule(app)

        self.assertEqual(label, "morning")
        self.assertEqual(scheduled.strftime("%H:%M"), "07:15")

    def test_generate_and_send_sheet_routes_through_coordinator(self):
        app = _gui_stub()
        app.visual_source_mode_var = _Var("sheet")
        app._current_scan_label = Mock(return_value="morning")
        app._latest_started_schedule = Mock(return_value=("morning", datetime(2026, 8, 1, 7, 15)))
        app._task_run_scheduled_brief = Mock(return_value=("coordinated", True))
        app._refresh_app_source_if_selected = Mock(side_effect=AssertionError("legacy path must not run"))

        result = AppGUI._task_generate_and_send_combined_cards(app)

        self.assertEqual(result, ("coordinated", True))
        app._task_run_scheduled_brief.assert_called_once_with("morning")
        app._refresh_app_source_if_selected.assert_not_called()

    def test_scheduled_resume_publishing_does_not_rebuild_brief(self):
        app = _gui_stub()
        app.settings = {"orchestration": {}}
        app.sheet_url_var = _Var("sheet")
        app._schedule_now = Mock(return_value=datetime(2026, 8, 1, 8, 0))
        app._schedule_times = Mock(return_value=["07:15", "19:15"])
        app.active_news_run = None
        resumed = _selected_result()
        resumed.update(
            {
                "run_id": "2026-08-01:morning",
                "run_owner": "worker-a",
                "publish_plan": {"version": 1},
            }
        )
        app._resume_orchestrated_cards_result = Mock(return_value=resumed)
        app._generate_orchestrated_cards_result = Mock(side_effect=AssertionError("must not rebuild"))
        app._run_selected_completion_actions = Mock(return_value=([], True))
        decision = {
            "run_id": "2026-08-01:morning",
            "owner": "worker-a",
            "lane": "primary",
            "state": "PUBLISHING",
            "action": "selected",
            "reason": "recovered",
        }

        with patch("app.gui.select_scheduled_lane", return_value=decision):
            with patch("app.gui.maintain_news_run_lease", return_value=nullcontext()):
                with patch("app.gui.update_scheduled_run"):
                    _output, ok = AppGUI._task_run_scheduled_brief(app, "morning")

        self.assertTrue(ok)
        app._resume_orchestrated_cards_result.assert_called_once_with(decision, "morning")
        app._generate_orchestrated_cards_result.assert_not_called()

    def test_reconciled_failed_run_is_healthy_in_gui(self):
        app = _gui_stub()
        app.settings = {"orchestration": {}}
        app.sheet_url_var = _Var("sheet")
        app._schedule_now = Mock(return_value=datetime(2026, 8, 1, 8, 0))
        app._schedule_times = Mock(return_value=["07:15", "19:15"])
        decision = {
            "run_id": "2026-08-01:morning",
            "state": "FAILED",
            "action": "terminal",
            "record": {
                "stats": {"publish_reconciliation": {"status": "succeeded"}}
            },
        }

        with patch("app.gui.select_scheduled_lane", return_value=decision):
            output, ok = AppGUI._task_run_scheduled_brief(app, "morning")

        self.assertTrue(ok)
        self.assertIn("publishing reconciled", output)

    def test_scheduled_resume_fails_closed_when_frozen_manifest_is_missing(self):
        app = _gui_stub()
        decision = {"run_id": "2026-08-01:morning", "owner": "worker-a"}
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "output" / "runs" / "2026-08-01_morning"
            run_dir.mkdir(parents=True)
            (run_dir / "combined_brief.json").write_text(
                '{"run_id":"2026-08-01:morning","stats":{},'
                '"publish_plan":{"version":1}}',
                encoding="utf-8",
            )
            with patch("app.gui.ROOT_DIR", Path(temp_dir)):
                with patch(
                    "app.gui.load_image_cards_result",
                    side_effect=FileNotFoundError("manifest missing"),
                ):
                    with patch("app.gui.generate_image_cards") as render:
                        with self.assertRaisesRegex(ValueError, "frozen card manifest"):
                            AppGUI._resume_orchestrated_cards_result(app, decision, "morning")

        render.assert_not_called()

    def test_direct_sheet_publish_cannot_bypass_coordinator(self):
        for source_mode in ("sheet", "combined"):
            with self.subTest(source_mode=source_mode):
                app = _gui_stub()
                app.visual_source_mode_var = _Var(source_mode)
                with patch("app.gui.build_combined_brief") as build:
                    with self.assertRaisesRegex(RuntimeError, "coordinator"):
                        AppGUI._generate_combined_cards_result(app, test_mode=False)

                build.assert_not_called()

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

    def test_backup_cards_continue_all_selected_completion_actions(self):
        app = _gui_stub()
        app.send_telegram_var = _Var(True)
        app.post_facebook_var = _Var(True)
        app.post_facebook_groups_var = _Var(True)
        app._retry_gui_step = Mock(side_effect=lambda _name, action: action())
        app._task_send_cards = Mock(return_value=("telegram sent", True))
        app._task_post_facebook_cards = Mock(return_value=("facebook posted", True))
        app._task_post_facebook_groups_cards = Mock(return_value=("groups posted", True))
        result = _selected_result()
        result["source_stats"]["fallback_reason"] = "sheet_error"
        result["source_stats"]["backup_total"] = 1

        lines, ok = AppGUI._run_selected_completion_actions(app, result)

        self.assertTrue(ok)
        self.assertEqual(lines, ["telegram sent", "facebook posted", "groups posted"])
        app._task_send_cards.assert_called_once_with(["card-1.png"], "evening")
        app._task_post_facebook_cards.assert_called_once_with(["card-1.png"], "evening", dry_run=True)
        app._task_post_facebook_groups_cards.assert_called_once_with(["card-1.png"], "evening", dry_run=True)

    def test_scheduled_completion_uses_frozen_publish_plan(self):
        app = _gui_stub()
        app._task_send_cards = Mock(return_value=("telegram sent", True))
        plan = {
            "version": 1,
            "send_telegram": True,
            "telegram_chat_ids": ["frozen-chat"],
            "telegram_intro_text": "Frozen {date}",
            "post_facebook": False,
            "post_facebook_groups": False,
        }

        lines, ok = AppGUI._run_selected_completion_actions(
            app,
            _selected_result(),
            run_id="2026-08-01:morning",
            run_owner="worker-a",
            publish_plan=plan,
        )

        self.assertTrue(ok)
        self.assertEqual(lines, ["telegram sent"])
        app._task_send_cards.assert_called_once_with(
            ["card-1.png"],
            "evening",
            run_id="2026-08-01:morning",
            run_owner="worker-a",
            chat_ids=["frozen-chat"],
            intro_template="Frozen {date}",
            fence_run=True,
        )

    def test_scheduled_facebook_groups_receive_run_fence(self):
        app = _gui_stub()
        app._task_post_facebook_groups_cards = Mock(return_value=("groups posted", True))
        plan = {
            "version": 1,
            "send_telegram": False,
            "post_facebook": False,
            "post_facebook_groups": True,
            "facebook_group_dry_run": False,
            "facebook_groups": [],
        }

        lines, ok = AppGUI._run_selected_completion_actions(
            app,
            _selected_result(),
            run_id="2026-08-01:morning",
            run_owner="worker-a",
            publish_plan=plan,
        )

        self.assertTrue(ok)
        self.assertEqual(lines, ["groups posted"])
        app._task_post_facebook_groups_cards.assert_called_once_with(
            ["card-1.png"],
            "evening",
            dry_run=False,
            publish_plan=plan,
            run_id="2026-08-01:morning",
            run_owner="worker-a",
            fence_run=True,
        )


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
    app.post_facebook_groups_var = _Var(False)
    app.facebook_dry_run_var = _Var(True)
    app.facebook_group_dry_run_var = _Var(True)
    app.facebook_group_delay_min_var = _Var("900")
    app.facebook_group_delay_max_var = _Var("1800")
    app.facebook_group_max_per_brief_var = _Var("2")
    app.facebook_group_max_per_day_var = _Var("4")
    app.facebook_group_queue_expiry_var = _Var("12")
    app.facebook_group_auto_resume_var = _Var(True)
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
    app._current_scan_label = Mock(return_value="morning")
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


def _pipeline_result():
    return {
        "ok": True,
        "fetch": {"results": [{"inserted": 2}]},
        "html_fetch": {"results": [{"inserted": 1}]},
        "scored": [{"id": 1}, {"id": 2}],
        "summaries": [{"article_id": 1}],
        "brief": {"scan_label": "evening", "items": 1},
    }


def _combined_result(items, already_published=0, selected_total=1):
    stats = {
        "source_mode": "sheet",
        "app_total": 0,
        "sheet_total": 10,
        "raw_total": 10,
        "already_published": already_published,
        "duplicate_removed": 0,
        "eligible_total": selected_total,
        "selected_total": selected_total,
        "duplicate_groups": [],
        "sheet_source": {
            "run_marker": "07:30",
            "run_label": "morning",
            "loaded_items": 10,
        },
    }
    return type(
        "CombinedResult",
        (),
        {
            "payload": {"items": items},
            "stats": stats,
            "brief_path": "combined_brief.json",
        },
    )()


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _Textbox:
    def __init__(self, value):
        self.value = value

    def get(self, _start, _end):
        return self.value


class _FakeNow:
    def __init__(self, formatted):
        self.formatted = formatted

    def strftime(self, _format):
        return self.formatted


if __name__ == "__main__":
    unittest.main()
