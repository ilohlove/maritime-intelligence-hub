import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.cli import (
    _news_status,
    _publish_run,
    _publish_scheduled_cards,
    _resume_scheduled_output,
    _run_scheduled,
    run_cli,
)


class ScheduledCliTests(unittest.TestCase):
    def test_parser_routes_run_scheduled_command(self):
        with patch("app.cli._run_scheduled", return_value=0) as scheduled:
            code = run_cli(["run-scheduled", "--slot", "morning", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertEqual(scheduled.call_args.args[0].slot, "morning")
        self.assertTrue(scheduled.call_args.args[0].dry_run)

    def test_parser_routes_status_and_delivery_resolution_commands(self):
        with patch("app.cli._news_status", return_value=0) as status:
            status_code = run_cli(["news-status", "--run-id", "2026-08-01:morning"])
        with patch("app.cli._resolve_delivery", return_value=0) as resolve:
            resolve_code = run_cli(
                [
                    "resolve-delivery",
                    "--id",
                    "7",
                    "--resolution",
                    "retry",
                    "--reviewer",
                    "operator-a",
                    "--note",
                    "No remote post found",
                ]
            )
        with patch("app.cli._publish_run", return_value=0) as publish_run:
            publish_code = run_cli(
                ["publish-run", "--run-id", "2026-08-01:morning"]
            )

        self.assertEqual(status_code, 0)
        self.assertEqual(resolve_code, 0)
        self.assertEqual(publish_code, 0)
        self.assertEqual(status.call_args.args[0].run_id, "2026-08-01:morning")
        self.assertEqual(resolve.call_args.args[0].id, 7)
        self.assertEqual(publish_run.call_args.args[0].run_id, "2026-08-01:morning")

    def test_terminal_failed_run_returns_nonzero(self):
        args = SimpleNamespace(slot="morning", dry_run=False)
        failed = {**_decision(), "action": "terminal", "state": "FAILED"}
        with patch("app.cli.scheduled_datetime", return_value=datetime(2026, 8, 1, tzinfo=timezone.utc)):
            with patch("app.cli.validate_runtime_seeds"):
                with patch("app.cli.load_runtime_settings", return_value=_settings()):
                    with patch("app.cli.select_scheduled_lane", return_value=failed):
                        code = _run_scheduled(args)

        self.assertEqual(code, 1)

    def test_terminal_failed_run_with_successful_reconciliation_returns_zero(self):
        args = SimpleNamespace(slot="morning", dry_run=False)
        failed = {
            **_decision(),
            "action": "terminal",
            "state": "FAILED",
            "record": {
                "stats": {"publish_reconciliation": {"status": "succeeded"}}
            },
        }
        with patch("app.cli.scheduled_datetime", return_value=datetime(2026, 8, 1, tzinfo=timezone.utc)):
            with patch("app.cli.validate_runtime_seeds"):
                with patch("app.cli.load_runtime_settings", return_value=_settings()):
                    with patch("app.cli.select_scheduled_lane", return_value=failed):
                        code = _run_scheduled(args)

        self.assertEqual(code, 0)

    def test_completed_primary_with_no_new_items_is_terminal_noop(self):
        args = SimpleNamespace(slot="morning", dry_run=True)
        source_result = SimpleNamespace(
            payload={"items": [], "stats": {}},
            stats={"already_published": 3, "selected_total": 0},
            brief_path=Path("combined_brief.json"),
        )
        decision = _decision()
        updates = []

        with patch("app.cli.scheduled_datetime", return_value=datetime(2026, 8, 1, tzinfo=timezone.utc)):
            with patch("app.cli.validate_runtime_seeds"):
                with patch("app.cli.load_runtime_settings", return_value=_settings()):
                    with patch("app.cli.select_scheduled_lane", return_value=decision):
                        with patch("app.cli.maintain_news_run_lease", return_value=nullcontext()):
                            with patch("app.cli.build_combined_brief", return_value=source_result):
                                with patch("app.cli.write_json_atomic"):
                                    with patch("app.cli.generate_image_cards") as render:
                                        with patch(
                                            "app.cli.update_scheduled_run",
                                            side_effect=lambda _decision, state, **_kwargs: updates.append(state),
                                        ):
                                            code = _run_scheduled(args)

        self.assertEqual(code, 0)
        self.assertEqual(updates, ["RENDERING", "NO_NEW_CONTENT"])
        render.assert_not_called()

    def test_dry_run_renders_to_windows_safe_run_directory(self):
        args = SimpleNamespace(slot="morning", dry_run=True)
        source_result = SimpleNamespace(
            payload={"items": [{"title": "Tin", "original_url": "https://example.com"}], "stats": {}},
            stats={"selected_total": 1},
            brief_path=Path("combined_brief.json"),
        )
        decision = _decision()
        updates = []

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.cli.scheduled_datetime", return_value=datetime(2026, 8, 1, tzinfo=timezone.utc)):
                with patch("app.cli.ROOT_DIR", Path(temp_dir)):
                    with patch("app.cli.validate_runtime_seeds"):
                        with patch("app.cli.load_runtime_settings", return_value=_settings()):
                            with patch("app.cli.select_scheduled_lane", return_value=decision):
                                with patch("app.cli.maintain_news_run_lease", return_value=nullcontext()):
                                    with patch("app.cli.build_combined_brief", return_value=source_result):
                                        with patch("app.cli.write_json_atomic"):
                                            with patch(
                                                "app.cli.generate_image_cards",
                                                return_value={
                                                    "items": 1,
                                                    "output_dir": "visual",
                                                    "cards": [{"item_key": "url:test", "card_path": "card.png"}],
                                                },
                                            ) as render:
                                                with patch("app.cli._publish_scheduled_cards", return_value=(True, ["dry-run"])):
                                                    with patch(
                                                        "app.cli.update_scheduled_run",
                                                        side_effect=lambda _decision, state, **_kwargs: updates.append(state),
                                                    ):
                                                        code = _run_scheduled(args)

        self.assertEqual(code, 0)
        self.assertEqual(updates, ["RENDERING", "PUBLISHING", "SUCCEEDED"])
        output_dir = Path(render.call_args.kwargs["output_dir"])
        self.assertEqual(output_dir.parent.name, "2026-08-01_morning")
        self.assertNotIn(":", output_dir.parent.name)

    def test_delivery_needing_review_keeps_run_unsuccessful(self):
        cards = [{"item_key": "url:test", "card_path": "card.png"}]
        settings = {
            "publish": {
                "send_telegram": True,
                "telegram_chat_ids": ["chat-1"],
            }
        }
        claims = {
            "claimed": [],
            "skipped": [{"card": cards[0], "reason": "needs_review"}],
        }

        with patch("app.cli.load_ai_env", return_value={"TELEGRAM_BOT_TOKEN": "token"}):
            with patch("app.cli.claim_delivery_cards", return_value=claims):
                with patch("app.cli.send_photos") as send:
                    ok, lines = _publish_scheduled_cards(
                        cards,
                        "morning",
                        _decision(),
                        settings,
                    )

        self.assertFalse(ok)
        self.assertTrue(any("needs_review" in line for line in lines))
        send.assert_not_called()

    def test_terminal_retry_uses_manual_group_mode_and_stays_failed_when_queue_remains(self):
        cards = [{"item_key": "url:test", "card_path": "card.png"}]
        publish_plan = {
            "version": 1,
            "dry_run": False,
            "send_telegram": False,
            "post_facebook": False,
            "post_facebook_groups": True,
            "facebook_groups": [{"id": "group-1", "enabled": True}],
            "facebook_group_dry_run": False,
        }
        guard = Mock()
        guard.blockers = []
        group_result = {
            "counts": {
                "published": 0,
                "pending": 0,
                "failed": 0,
                "needs_login": 0,
                "queued": 1,
            },
            "results": [
                {"group_name": "Group One", "status": "queued", "message": "Waiting for manual publish."}
            ],
        }
        with patch("app.cli.load_ai_env", return_value={}):
            with patch("app.cli.validate_group_config", return_value={"ready": True, "groups": [{}]}):
                with patch("app.cli.FacebookGroupDeliveryGuard", return_value=guard):
                    with patch("app.cli.publish_to_groups", return_value=group_result) as publish:
                        ok, lines = _publish_scheduled_cards(
                            cards,
                            "morning",
                            _decision(),
                            _settings(),
                            publish_plan=publish_plan,
                            fence_run=False,
                            terminal_fence=True,
                        )

        self.assertFalse(ok)
        self.assertTrue(publish.call_args.kwargs["manual"])
        self.assertTrue(any("retry remains incomplete" in line for line in lines))

    def test_resume_publishing_reuses_run_brief_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "2026-08-01_morning"
            run_dir.mkdir()
            (run_dir / "combined_brief.json").write_text(
                '{"run_id":"2026-08-01:morning","stats":{"selected_total":2},'
                '"publish_plan":{"version":1,"dry_run":true}}',
                encoding="utf-8",
            )
            cards_result = {
                "items": 2,
                "output_dir": run_dir / "visual",
                "cards": [{"item_key": "url:a"}, {"item_key": "url:b"}],
            }
            with patch("app.cli.load_image_cards_result", return_value=cards_result) as load:
                with patch("app.cli.generate_image_cards") as render:
                    stats, resumed, publish_plan = _resume_scheduled_output(
                        run_dir,
                        "2026-08-01:morning",
                        {},
                    )

        self.assertEqual(stats["selected_total"], 2)
        self.assertEqual(stats["resumed_from_state"], "PUBLISHING")
        self.assertIs(resumed, cards_result)
        self.assertTrue(publish_plan["dry_run"])
        load.assert_called_once_with(run_dir / "visual")
        render.assert_not_called()

    def test_resume_publishing_fails_closed_when_frozen_manifest_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "2026-08-01_morning"
            run_dir.mkdir()
            (run_dir / "combined_brief.json").write_text(
                '{"run_id":"2026-08-01:morning","stats":{},'
                '"publish_plan":{"version":1,"dry_run":false}}',
                encoding="utf-8",
            )
            with patch(
                "app.cli.load_image_cards_result",
                side_effect=FileNotFoundError("manifest missing"),
            ):
                with patch("app.cli.generate_image_cards") as render:
                    with self.assertRaisesRegex(ValueError, "frozen card manifest"):
                        _resume_scheduled_output(run_dir, "2026-08-01:morning", {})

        render.assert_not_called()

    def test_publish_run_retries_terminal_run_from_frozen_output(self):
        args = SimpleNamespace(run_id="2026-08-01:morning")
        cards_result = {"cards": [{"item_key": "url:test"}]}
        publish_plan = {"version": 1, "dry_run": False}
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.cli.ROOT_DIR", Path(temp_dir)):
                with patch("app.cli.validate_runtime_seeds"):
                    with patch(
                        "app.cli.get_news_run",
                        return_value={"run_id": args.run_id, "state": "FAILED", "lane": "primary"},
                    ):
                        with patch("app.cli.load_runtime_settings", return_value=_settings()):
                            with patch(
                                "app.cli.claim_terminal_news_run_lease",
                                return_value={"acquired": True},
                            ):
                                with patch(
                                    "app.cli.maintain_terminal_news_run_lease",
                                    return_value=nullcontext(),
                                ):
                                    with patch(
                                        "app.cli.release_terminal_news_run_lease",
                                        return_value={"stats": {}},
                                    ) as release:
                                        with patch(
                                            "app.cli._resume_scheduled_output",
                                            return_value=({"selected_total": 1}, cards_result, publish_plan),
                                        ):
                                            with patch(
                                                "app.cli._publish_scheduled_cards",
                                                return_value=(True, ["retried"]),
                                            ) as publish:
                                                code = _publish_run(args)

        self.assertEqual(code, 0)
        self.assertFalse(publish.call_args.kwargs["fence_run"])
        self.assertTrue(publish.call_args.kwargs["terminal_fence"])
        self.assertIs(publish.call_args.kwargs["publish_plan"], publish_plan)
        self.assertEqual(release.call_args.kwargs["reconciliation"]["status"], "succeeded")

    def test_news_status_accepts_reconciled_failed_run(self):
        args = SimpleNamespace(run_id="2026-08-01:morning", limit=20)
        run = {
            "run_id": args.run_id,
            "state": "FAILED",
            "lane": "primary",
            "stats": {"publish_reconciliation": {"status": "succeeded"}},
        }
        with patch("app.cli.get_news_run", return_value=run):
            with patch("app.cli.list_publish_deliveries", return_value=[]):
                code = _news_status(args)

        self.assertEqual(code, 0)


def _decision():
    return {
        "run_id": "2026-08-01:morning",
        "owner": "worker-a",
        "lane": "primary",
        "state": "PRIMARY_SELECTED",
        "action": "selected",
        "reason": "completed",
        "snapshot": {
            "protocol_version": "v1",
            "run_id": "2026-08-01:morning",
            "status": "COMPLETED",
        },
    }


def _settings():
    return {
        "scan": {"times": ["07:15", "19:15"]},
        "visual": {"sheet_url": "https://docs.google.com/spreadsheets/d/test/edit"},
        "orchestration": {
            "primary_timeout_minutes": 30,
            "poll_interval_seconds": 60,
            "catch_up_window_minutes": 120,
        },
        "publish": {},
    }


if __name__ == "__main__":
    unittest.main()
