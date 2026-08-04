import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.services.combined_brief_source import sheet_content_fingerprint, sheet_snapshot_fingerprint
from app.services.business_logic import (
    build_publish_plan,
    claim_delivery_cards,
    due_scheduled_slot,
    FacebookGroupDeliveryGuard,
    select_scheduled_lane,
    update_scheduled_run,
)
from app.services.storage import (
    claim_news_run,
    get_news_run,
    select_news_run_lane,
    update_news_run_state,
)


ICT = timezone(timedelta(hours=7))


class NewsRunCoordinatorTests(unittest.TestCase):
    def test_publish_plan_freezes_destinations_without_secrets(self):
        plan = build_publish_plan(
            {
                "send_telegram": True,
                "telegram_intro_text": "Brief {date}",
                "post_facebook": True,
                "facebook_groups": [
                    {
                        "id": "g1",
                        "url": "https://facebook.com/groups/g1",
                        "caption_template": "Caption A",
                        "cookies": "must-not-persist",
                    }
                ],
            },
            telegram_chat_ids=["chat-1"],
            facebook_page_id="page-1",
            dry_run=True,
        )

        self.assertEqual(plan["telegram_chat_ids"], ["chat-1"])
        self.assertEqual(plan["facebook_page_id"], "page-1")
        self.assertTrue(plan["dry_run"])
        self.assertNotIn("cookies", plan["facebook_groups"][0])

    def test_scheduled_delivery_claim_is_blocked_after_run_lease_loss(self):
        cards = [{"item_key": "url:test", "card_path": "card.png"}]
        with patch("app.services.business_logic.heartbeat_news_run", return_value=False):
            with patch("app.services.business_logic.claim_publish_delivery") as claim:
                result = claim_delivery_cards(
                    "2026-08-01:morning",
                    cards,
                    "telegram",
                    "chat-1",
                    "worker-a",
                    fence_run=True,
                )

        self.assertEqual(result["claimed"], [])
        self.assertEqual(result["skipped"][0]["reason"], "run_lease_lost")
        claim.assert_not_called()

    def test_facebook_group_guard_claims_entire_batch_before_publish(self):
        cards = [
            {"item_key": "url:a", "card_path": "a.png"},
            {"item_key": "url:b", "card_path": "b.png"},
        ]
        guard = FacebookGroupDeliveryGuard(
            "2026-08-01:morning",
            cards,
            "worker-a",
            fence_run=True,
        )
        claims = {"claimed": cards, "skipped": []}
        with patch("app.services.business_logic.claim_delivery_cards", return_value=claims) as claim:
            with patch("app.services.business_logic.finish_delivery_cards", return_value=2) as finish:
                with patch("app.services.business_logic.mark_items_published") as published:
                    allowed = guard.before_publish({"id": "group-1"}, "batch-1")
                    guard.after_publish(
                        {"id": "group-1"},
                        "batch-1",
                        {"status": "published", "post_url": "https://facebook.test/post"},
                    )

        self.assertTrue(allowed["allowed"])
        self.assertEqual(claim.call_args.args[2:4], ("facebook_group", "group-1"))
        self.assertTrue(claim.call_args.kwargs["fence_run"])
        self.assertTrue(finish.call_args.kwargs["succeeded"])
        published.assert_called_once_with(cards, db_path=guard.db_path)
        self.assertEqual(guard.blockers, [])

    def test_facebook_group_guard_marks_all_succeeded_batch_as_terminal(self):
        cards = [{"item_key": "url:a", "card_path": "a.png"}]
        guard = FacebookGroupDeliveryGuard(
            "2026-08-01:morning",
            cards,
            "worker-a",
            fence_run=True,
        )
        claims = {
            "claimed": [],
            "skipped": [{"card": cards[0], "reason": "succeeded"}],
        }
        with patch("app.services.business_logic.claim_delivery_cards", return_value=claims):
            result = guard.before_publish({"id": "group-1"}, "batch-1")

        self.assertFalse(result["allowed"])
        self.assertEqual(result["terminal_status"], "published")
        self.assertEqual(guard.blockers, [])

    def test_due_slot_respects_catch_up_window(self):
        slot, scheduled = due_scheduled_slot(
            datetime(2026, 8, 1, 8, 0, tzinfo=ICT),
            ["07:15", "19:15"],
            120,
        )
        self.assertEqual(slot, "morning")
        self.assertEqual(scheduled.strftime("%H:%M"), "07:15")

        slot, scheduled = due_scheduled_slot(
            datetime(2026, 8, 1, 12, 0, tzinfo=ICT),
            ["07:15", "19:15"],
            120,
        )
        self.assertIsNone(slot)
        self.assertIsNone(scheduled)

    def test_completed_primary_snapshot_is_selected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 18, tzinfo=timezone.utc))
            snapshot = _snapshot("COMPLETED")

            decision = select_scheduled_lane(
                "morning",
                "https://docs.google.com/spreadsheets/d/test/edit",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: snapshot,
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["action"], "selected")
        self.assertEqual(decision["lane"], "primary")
        self.assertIs(decision["snapshot"], snapshot)
        self.assertEqual(clock.sleeps, [10])

    def test_running_primary_waits_then_becomes_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 15, tzinfo=timezone.utc))
            snapshots = iter(
                [
                    _snapshot("RUNNING"),
                    _snapshot("COMPLETED", completed_at="2026-08-01T07:15:30+07:00"),
                    _snapshot("COMPLETED", completed_at="2026-08-01T07:15:30+07:00"),
                ]
            )
            statuses = []

            decision = select_scheduled_lane(
                "morning",
                "https://docs.google.com/spreadsheets/d/test/edit",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                orchestration={
                    "primary_target_minutes": 2,
                    "primary_running_grace_minutes": 1,
                    "sheet_stability_seconds": 10,
                    "poll_interval_seconds": 30,
                },
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: next(snapshots),
                now_fn=clock.now,
                sleep_fn=clock.sleep,
                wait_callback=lambda status: statuses.append(status["state"]),
            )

        self.assertEqual(decision["lane"], "primary")
        self.assertEqual(clock.sleeps, [30, 10])
        self.assertEqual(statuses, ["WAIT_SHEET", "SHEET_STABILIZING"])

    def test_hhmm_snapshot_with_empty_nq_is_selected_after_two_stable_reads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 22, tzinfo=timezone.utc))
            snapshot = _legacy_snapshot(completed_at="07:22")

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: snapshot,
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )
            stored = get_news_run(decision["run_id"], db_path=db_path)

        self.assertEqual(decision["lane"], "primary")
        self.assertEqual(clock.sleeps, [10])
        self.assertEqual(stored["state"], "PRIMARY_SELECTED")
        self.assertEqual(stored["stats"]["marker_mode"], "legacy_time")
        self.assertTrue(stored["stats"]["sheet_stable"])

    def test_completed_snapshot_first_seen_at_target_gets_stability_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc))
            snapshot = _snapshot("COMPLETED", completed_at="2026-08-01T07:22:00+07:00")

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: snapshot,
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["lane"], "primary")
        self.assertEqual(clock.sleeps, [10])

    def test_completed_snapshot_is_preferred_during_late_catch_up(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc))
            snapshot = _snapshot("COMPLETED", completed_at="2026-08-01T07:22:00+07:00")

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: snapshot,
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["lane"], "primary")
        self.assertEqual(clock.sleeps, [10])

    def test_changed_snapshot_restarts_stability_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 20, tzinfo=timezone.utc))
            first = {**_snapshot("COMPLETED"), "snapshot_hash": "hash-a"}
            second = {**_snapshot("COMPLETED"), "snapshot_hash": "hash-b"}
            snapshots = iter([first, second, second])

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: next(snapshots),
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["lane"], "primary")
        self.assertEqual(clock.sleeps, [10, 10])
        self.assertEqual(decision["snapshot"]["snapshot_hash"], "hash-b")

    def test_running_at_target_can_complete_during_grace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc))
            completed = _snapshot("COMPLETED", completed_at="2026-08-01T07:34:00+07:00")

            def load_snapshot(_url, expected_run_id=None):
                return completed if clock.now().astimezone(ICT).strftime("%H:%M") >= "07:34" else _snapshot("RUNNING")

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                orchestration={"poll_interval_seconds": 240},
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=load_snapshot,
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["lane"], "primary")
        self.assertEqual(clock.now().astimezone(ICT).strftime("%H:%M:%S"), "07:34:10")

    def test_completion_at_last_grace_second_gets_post_deadline_verification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 30, 29, tzinfo=timezone.utc))
            completed = _snapshot("COMPLETED", completed_at="2026-08-01T07:34:59+07:00")

            def load_snapshot(_url, expected_run_id=None):
                return completed if clock.now().astimezone(ICT).strftime("%H:%M:%S") >= "07:34:59" else _snapshot("RUNNING")

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=load_snapshot,
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["lane"], "primary")
        self.assertEqual(clock.now().astimezone(ICT).strftime("%H:%M:%S"), "07:35:09")

    def test_completion_after_hard_deadline_uses_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc))
            late = _snapshot("COMPLETED", completed_at="2026-08-01T07:36:00+07:00")

            def load_snapshot(_url, expected_run_id=None):
                return late if clock.now().astimezone(ICT).strftime("%H:%M") >= "07:36" else _snapshot("RUNNING")

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                orchestration={"poll_interval_seconds": 300},
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=load_snapshot,
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["lane"], "backup")
        self.assertIn("primary_hard_deadline", decision["reason"])

    def test_first_legacy_snapshot_without_fresh_article_uses_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc))
            snapshot = _legacy_snapshot(completed_at="07:22", article_at="2026-07-31T18:00:00+07:00")

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: snapshot,
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["lane"], "backup")
        self.assertIn("legacy_snapshot_has_no_fresh_article", decision["reason"])

    def test_previous_slot_snapshot_hash_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            snapshot = _legacy_snapshot(completed_at="07:22")
            fingerprint = sheet_snapshot_fingerprint(snapshot)
            prior_now = datetime(2026, 7, 31, 0, 30, tzinfo=timezone.utc)
            prior = claim_news_run(
                "2026-07-31:morning",
                "worker-old",
                prior_now + timedelta(hours=1),
                db_path=db_path,
                now=prior_now,
            )
            select_news_run_lane(
                prior["run_id"],
                "worker-old",
                "primary",
                db_path=db_path,
                now=prior_now,
            )
            update_news_run_state(
                prior["run_id"],
                "worker-old",
                "PRIMARY_SELECTED",
                db_path=db_path,
                stats={"sheet_snapshot_hash": fingerprint},
                now=prior_now,
            )
            clock = _Clock(datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc))

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: snapshot,
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["lane"], "backup")
        self.assertIn("snapshot_hash_replayed", decision["reason"])

    def test_marker_only_change_cannot_bypass_content_replay_guard(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            previous = _legacy_snapshot(completed_at="07:22")
            current = _legacy_snapshot(completed_at="07:23")
            prior_now = datetime(2026, 7, 31, 0, 30, tzinfo=timezone.utc)
            prior = claim_news_run(
                "2026-07-31:morning",
                "worker-old",
                prior_now + timedelta(hours=1),
                db_path=db_path,
                now=prior_now,
            )
            select_news_run_lane(
                prior["run_id"],
                "worker-old",
                "primary",
                db_path=db_path,
                now=prior_now,
            )
            update_news_run_state(
                prior["run_id"],
                "worker-old",
                "PRIMARY_SELECTED",
                db_path=db_path,
                stats={
                    "sheet_snapshot_hash": sheet_snapshot_fingerprint(previous),
                    "sheet_content_hash": sheet_content_fingerprint(previous),
                },
                now=prior_now,
            )
            current["content_hash"] = sheet_content_fingerprint(current)
            clock = _Clock(datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc))

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: current,
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["lane"], "backup")
        self.assertIn("sheet_content_hash_replayed", decision["reason"])

    def test_future_m1_is_not_selected_before_its_marker_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 18, tzinfo=timezone.utc))
            snapshot = _snapshot("COMPLETED", completed_at="2026-08-01T07:20:00+07:00")

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: snapshot,
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["lane"], "primary")
        self.assertGreaterEqual(clock.now().astimezone(ICT).strftime("%H:%M:%S"), "07:20:10")

    def test_timeout_selects_backup_once_and_latches_lane(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 15, tzinfo=timezone.utc))

            decision = select_scheduled_lane(
                "morning",
                "https://docs.google.com/spreadsheets/d/test/edit",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                orchestration={
                    "primary_target_minutes": 1,
                    "primary_running_grace_minutes": 1,
                    "poll_interval_seconds": 30,
                },
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: _snapshot("RUNNING"),
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )
            conflicting = select_news_run_lane(
                decision["run_id"],
                decision["owner"],
                "primary",
                db_path=db_path,
                now=clock.now(),
            )

        self.assertEqual(decision["lane"], "backup")
        self.assertIn("primary_hard_deadline", decision["reason"])
        self.assertFalse(conflicting["lane_selected"])
        self.assertEqual(conflicting["selection_reason"], "lane_conflict")
        self.assertEqual(conflicting["lane"], "backup")

    def test_invalid_sheet_at_target_selects_backup_without_sleep(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc))

            decision = select_scheduled_lane(
                "morning",
                "https://docs.google.com/spreadsheets/d/test/edit",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: {
                    **_snapshot("COMPLETED"),
                    "started_at": "2026-07-31T19:15:00+07:00",
                },
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(decision["lane"], "backup")
        self.assertEqual(clock.sleeps, [])

    def test_primary_completed_after_deadline_cannot_win(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            now = datetime(2026, 8, 1, 0, 50, tzinfo=timezone.utc)
            late_snapshot = _snapshot(
                "COMPLETED",
                completed_at="2026-08-01T07:46:00+07:00",
            )

            decision = select_scheduled_lane(
                "morning",
                "https://docs.google.com/spreadsheets/d/test/edit",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                orchestration={
                    "primary_target_minutes": 15,
                    "primary_running_grace_minutes": 5,
                },
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: late_snapshot,
                now_fn=lambda: now,
            )

        self.assertEqual(decision["lane"], "backup")
        self.assertIn("primary_target_deadline", decision["reason"])

    def test_recovered_wait_uses_persisted_deadline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            scheduled = datetime(2026, 8, 1, 7, 15, tzinfo=ICT)
            first_now = datetime(2026, 8, 1, 0, 15, tzinfo=timezone.utc)
            claim_news_run(
                "2026-08-01:morning",
                "worker-old",
                first_now + timedelta(minutes=1),
                db_path=db_path,
                lease_seconds=30,
                now=first_now,
            )
            recovered_now = first_now + timedelta(minutes=2)

            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=scheduled,
                orchestration={"primary_target_minutes": 15},
                db_path=db_path,
                owner="worker-new",
                snapshot_loader=lambda _url, expected_run_id=None: _snapshot("RUNNING"),
                now_fn=lambda: recovered_now,
            )

        self.assertEqual(decision["lane"], "backup")
        self.assertIn("primary_hard_deadline", decision["reason"])

    def test_state_update_fails_fast_after_lease_loss(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 18, tzinfo=timezone.utc))
            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: _snapshot("COMPLETED"),
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

            with self.assertRaisesRegex(RuntimeError, "Lost ownership or lease"):
                update_scheduled_run(
                    decision,
                    "PUBLISHING",
                    db_path=db_path,
                    now=clock.now() + timedelta(minutes=6),
                )

    def test_second_process_cannot_acquire_active_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 18, tzinfo=timezone.utc))
            first = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: _snapshot("COMPLETED"),
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )
            second = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-b",
                snapshot_loader=lambda _url, expected_run_id=None: _snapshot("COMPLETED"),
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )

        self.assertEqual(first["action"], "selected")
        self.assertEqual(second["action"], "busy")
        self.assertEqual(second["reason"], "leased")

    def test_empty_primary_falls_back_and_terminal_result_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            clock = _Clock(datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc))
            decision = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-a",
                snapshot_loader=lambda _url, expected_run_id=None: _snapshot("COMPLETED", row_count=0),
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )
            self.assertEqual(decision["lane"], "backup")
            update_scheduled_run(
                decision,
                "NO_NEW_CONTENT",
                stats={"selected_total": 0},
                db_path=db_path,
                now=clock.now(),
            )
            repeat = select_scheduled_lane(
                "morning",
                "sheet",
                scheduled_at=datetime(2026, 8, 1, 7, 15, tzinfo=ICT),
                db_path=db_path,
                owner="worker-b",
                snapshot_loader=lambda _url, expected_run_id=None: _snapshot("COMPLETED", row_count=0),
                now_fn=clock.now,
                sleep_fn=clock.sleep,
            )
            stored = get_news_run(decision["run_id"], db_path=db_path)

        self.assertEqual(stored["state"], "NO_NEW_CONTENT")
        self.assertEqual(repeat["action"], "terminal")


class _Clock:
    def __init__(self, current):
        self.current = current
        self.sleeps = []

    def now(self):
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


def _snapshot(status, row_count=1, completed_at=None):
    items = [] if row_count == 0 else [
        {
            "title": "Bản tin hàng hải Việt Nam mới",
            "summary": "Bản tin cung cấp thông tin mới cho doanh nghiệp theo dõi lịch tàu và kế hoạch khai thác.",
            "impact_note": "Thông tin này ảnh hưởng trực tiếp đến lịch tàu và chi phí vận tải biển.",
            "source_name": "Sheet Source",
            "original_url": "https://example.com/new",
            "published_at": "2026-08-01T07:10:00+07:00",
            "row_index": 1,
        }
    ]
    completed = (completed_at or "2026-08-01T07:18:00+07:00") if status == "COMPLETED" else ""
    error = "PRIMARY_ERROR" if status == "FAILED" else ""
    return {
        "protocol_version": "v1",
        "started_at": "2026-08-01T07:15:00+07:00",
        "completed_at": completed,
        "run_id": "2026-08-01:morning",
        "status": status,
        "row_count": row_count if status == "COMPLETED" else None,
        "row_count_raw": str(row_count) if status == "COMPLETED" else "",
        "error_code": error,
        "data_row_count": row_count,
        "usable_row_count": row_count,
        "row_errors": [],
        "rows": [{"Date": "2026-08-01T07:10:00+07:00"} for _ in range(row_count)],
        "items": items,
    }


def _legacy_snapshot(completed_at="", article_at="2026-08-01T07:10:00+07:00"):
    snapshot = _snapshot("COMPLETED" if completed_at else "RUNNING")
    snapshot.update(
        {
            "protocol_version": "legacy",
            "marker_mode": "legacy_time",
            "started_at": "07:15",
            "completed_at": completed_at,
            "run_id": "",
            "status": "",
            "row_count": None,
            "row_count_raw": "",
            "error_code": "",
        }
    )
    for item in snapshot["items"]:
        item["published_at"] = article_at
    snapshot["rows"] = [{"Date": article_at} for _ in snapshot["items"]]
    return snapshot


if __name__ == "__main__":
    unittest.main()
