import tempfile
import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

from app.services.storage import (
    SQLITE_BUSY_TIMEOUT_MS,
    cancel_facebook_group_delivery,
    claim_facebook_group_delivery,
    claim_news_run,
    claim_publish_delivery,
    claim_terminal_news_run_lease,
    connect_db,
    count_facebook_group_attempts_since,
    expire_facebook_group_deliveries,
    get_facebook_group_delivery_by_id,
    get_news_run,
    get_publish_delivery,
    heartbeat_news_run,
    heartbeat_terminal_news_run_lease,
    init_db,
    list_published_item_keys,
    list_publish_deliveries,
    mark_publish_delivery_failed,
    mark_publish_delivery_needs_review,
    mark_publish_delivery_succeeded,
    recover_expired_news_runs,
    recover_stale_publish_deliveries,
    record_facebook_group_delivery,
    release_terminal_news_run_lease,
    resolve_publish_delivery,
    select_news_run_lane,
    update_news_run_state,
)


RUN_ID = "2026-08-01:morning"


class RunStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "mih.db"
        self.now = datetime(2026, 8, 1, 0, 15, tzinfo=timezone.utc)
        self.deadline = self.now + timedelta(minutes=30)
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_uses_wal_and_busy_timeout(self):
        with connect_db(self.db_path) as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

        self.assertEqual(journal_mode.lower(), "wal")
        self.assertEqual(busy_timeout, SQLITE_BUSY_TIMEOUT_MS)

    def test_concurrent_legacy_schema_migration_rechecks_columns_under_lock(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        conn = sqlite3.connect(legacy_path)
        try:
            conn.executescript(
                """
                CREATE TABLE facebook_group_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    group_name TEXT,
                    group_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    completed_at TEXT,
                    post_url TEXT,
                    error_message TEXT,
                    payload_json TEXT NOT NULL,
                    UNIQUE(batch_id, group_id)
                );
                CREATE TABLE facebook_group_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    stop_reason TEXT
                );
                """
            )
            conn.commit()
        finally:
            conn.close()
        barrier = Barrier(2)

        def migrate(_index):
            barrier.wait()
            init_db(legacy_path)
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(migrate, range(2)))

        self.assertEqual(results, [True, True])
        with connect_db(legacy_path) as conn:
            delivery_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(facebook_group_deliveries)")
            }
            attempt_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(facebook_group_attempts)")
            }
        self.assertTrue(
            {"owner", "lease_expires_at", "quota_reservation_token"}.issubset(delivery_columns)
        )
        self.assertIn("reservation_token", attempt_columns)

    def test_only_one_worker_acquires_same_run(self):
        barrier = Barrier(2)

        def claim(owner):
            barrier.wait()
            return claim_news_run(
                RUN_ID,
                owner,
                self.deadline,
                db_path=self.db_path,
                now=self.now,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["worker-a", "worker-b"]))

        winners = [result for result in results if result["acquired"]]
        losers = [result for result in results if not result["acquired"]]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(losers), 1)
        self.assertEqual(losers[0]["claim_reason"], "leased")
        self.assertEqual(get_news_run(RUN_ID, self.db_path)["owner"], winners[0]["owner"])

    def test_lane_is_latched_and_terminal_state_releases_lease(self):
        claim_news_run(
            RUN_ID,
            "worker-a",
            self.deadline,
            db_path=self.db_path,
            now=self.now,
        )

        selected = select_news_run_lane(
            RUN_ID,
            "worker-a",
            "primary",
            db_path=self.db_path,
            now=self.now,
        )
        conflict = select_news_run_lane(
            RUN_ID,
            "worker-a",
            "backup",
            db_path=self.db_path,
            now=self.now,
        )
        completed = update_news_run_state(
            RUN_ID,
            "worker-a",
            "SUCCEEDED",
            db_path=self.db_path,
            stats={"new_items": 4},
            now=self.now + timedelta(seconds=1),
        )
        duplicate = claim_news_run(
            RUN_ID,
            "worker-b",
            self.deadline,
            db_path=self.db_path,
            now=self.now + timedelta(minutes=10),
        )

        self.assertTrue(selected["lane_selected"])
        self.assertEqual(selected["state"], "PRIMARY_SELECTED")
        self.assertFalse(conflict["lane_selected"])
        self.assertEqual(conflict["selection_reason"], "lane_conflict")
        self.assertEqual(completed["lane"], "primary")
        self.assertEqual(completed["stats"], {"new_items": 4})
        self.assertIsNone(completed["owner"])
        self.assertIsNone(completed["lease_expires_at"])
        self.assertIsNotNone(completed["completed_at"])
        self.assertFalse(duplicate["acquired"])
        self.assertEqual(duplicate["claim_reason"], "terminal")

    def test_only_one_terminal_publish_retry_lease_and_reconciliation_is_audited(self):
        claim_news_run(
            RUN_ID,
            "scheduled-worker",
            self.deadline,
            db_path=self.db_path,
            now=self.now,
        )
        update_news_run_state(
            RUN_ID,
            "scheduled-worker",
            "FAILED",
            db_path=self.db_path,
            error="publish_failed",
            now=self.now + timedelta(seconds=1),
        )
        barrier = Barrier(2)

        def claim(owner):
            barrier.wait()
            return claim_terminal_news_run_lease(
                RUN_ID,
                owner,
                db_path=self.db_path,
                now=self.now + timedelta(seconds=2),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["retry-a", "retry-b"]))

        winner = next(result for result in results if result["acquired"])
        loser = next(result for result in results if not result["acquired"])
        self.assertEqual(loser["claim_reason"], "leased")
        self.assertTrue(
            heartbeat_terminal_news_run_lease(
                RUN_ID,
                winner["owner"],
                db_path=self.db_path,
                now=self.now + timedelta(seconds=3),
            )
        )

        released = release_terminal_news_run_lease(
            RUN_ID,
            winner["owner"],
            db_path=self.db_path,
            reconciliation={"status": "succeeded"},
            now=self.now + timedelta(seconds=4),
        )

        self.assertIsNone(released["owner"])
        self.assertEqual(released["stats"]["publish_reconciliation"]["status"], "succeeded")
        self.assertEqual(len(released["stats"]["publish_reconciliation_history"]), 1)

    def test_expired_run_can_be_recovered_and_heartbeat_extends_lease(self):
        claim_news_run(
            RUN_ID,
            "worker-a",
            self.deadline,
            db_path=self.db_path,
            lease_seconds=300,
            now=self.now,
        )
        select_news_run_lane(
            RUN_ID,
            "worker-a",
            "backup",
            db_path=self.db_path,
            now=self.now,
        )
        blocked = claim_news_run(
            RUN_ID,
            "worker-b",
            self.deadline,
            db_path=self.db_path,
            now=self.now + timedelta(seconds=299),
        )
        recovered_count = recover_expired_news_runs(
            self.db_path,
            now=self.now + timedelta(seconds=300),
        )
        recovered = claim_news_run(
            RUN_ID,
            "worker-b",
            self.deadline,
            db_path=self.db_path,
            lease_seconds=300,
            now=self.now + timedelta(seconds=300),
        )
        late_primary = select_news_run_lane(
            RUN_ID,
            "worker-b",
            "primary",
            db_path=self.db_path,
            now=self.now + timedelta(seconds=301),
        )
        heartbeat_ok = heartbeat_news_run(
            RUN_ID,
            "worker-b",
            db_path=self.db_path,
            lease_seconds=300,
            now=self.now + timedelta(seconds=400),
        )
        early_recovery = recover_expired_news_runs(
            self.db_path,
            now=self.now + timedelta(seconds=699),
        )

        self.assertFalse(blocked["acquired"])
        self.assertEqual(recovered_count, 1)
        self.assertTrue(recovered["acquired"])
        self.assertEqual(recovered["claim_reason"], "recovered")
        self.assertEqual(recovered["lane"], "backup")
        self.assertFalse(late_primary["lane_selected"])
        self.assertEqual(late_primary["selection_reason"], "lane_conflict")
        self.assertTrue(heartbeat_ok)
        self.assertEqual(early_recovery, 0)

    def test_only_one_worker_acquires_same_delivery(self):
        barrier = Barrier(2)

        def claim(owner):
            barrier.wait()
            return claim_publish_delivery(
                RUN_ID,
                "url:concurrent",
                "telegram",
                "chat-1",
                owner,
                db_path=self.db_path,
                now=self.now,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["worker-a", "worker-b"]))

        self.assertEqual(sum(result["acquired"] for result in results), 1)
        self.assertEqual(
            {result["claim_reason"] for result in results},
            {"created", "sending"},
        )

    def test_only_one_process_claims_facebook_group_browser_delivery(self):
        delivery = record_facebook_group_delivery(
            "batch-1",
            {
                "id": "group-1",
                "name": "Group One",
                "url": "https://www.facebook.com/groups/group-1",
            },
            "queued",
            db_path=self.db_path,
        )
        barrier = Barrier(2)

        def claim(owner):
            barrier.wait()
            return claim_facebook_group_delivery(
                delivery["id"],
                owner,
                db_path=self.db_path,
                now=self.now,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["group-worker-a", "group-worker-b"]))

        self.assertEqual(sum(result["acquired"] for result in results), 1)
        self.assertEqual(
            {result["claim_reason"] for result in results},
            {"claimed", "sending"},
        )
        stored = get_facebook_group_delivery_by_id(delivery["id"], self.db_path)
        self.assertEqual(stored["status"], "sending")

    def test_facebook_group_daily_quota_is_reserved_atomically(self):
        deliveries = [
            record_facebook_group_delivery(
                f"batch-{index}",
                {
                    "id": f"group-{index}",
                    "name": f"Group {index}",
                    "url": f"https://www.facebook.com/groups/group-{index}",
                },
                "queued",
                db_path=self.db_path,
            )
            for index in (1, 2)
        ]
        barrier = Barrier(2)
        day_start = (self.now - timedelta(hours=1)).isoformat()

        def claim(index):
            barrier.wait()
            return claim_facebook_group_delivery(
                deliveries[index]["id"],
                f"quota-worker-{index}",
                db_path=self.db_path,
                daily_since_iso=day_start,
                daily_limit=1,
                now=self.now,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, range(2)))

        self.assertEqual(sum(result["acquired"] for result in results), 1)
        self.assertEqual(
            {result["claim_reason"] for result in results},
            {"claimed", "daily_quota_exhausted"},
        )
        with connect_db(self.db_path) as conn:
            reserved = conn.execute(
                "SELECT COUNT(*) FROM facebook_group_attempts WHERE status = 'reserved'"
            ).fetchone()[0]
        self.assertEqual(reserved, 1)

        winner_index = next(index for index, result in enumerate(results) if result["acquired"])
        group_number = winner_index + 1
        record_facebook_group_delivery(
            f"batch-{group_number}",
            {
                "id": f"group-{group_number}",
                "name": f"Group {group_number}",
                "url": f"https://www.facebook.com/groups/group-{group_number}",
            },
            "published",
            db_path=self.db_path,
        )
        with connect_db(self.db_path) as conn:
            attempts = conn.execute(
                "SELECT status FROM facebook_group_attempts"
            ).fetchall()
        self.assertEqual([row["status"] for row in attempts], ["published"])

    def test_cancel_after_stale_group_review_releases_quota_reservation(self):
        delivery = record_facebook_group_delivery(
            "batch-review",
            {
                "id": "group-review",
                "name": "Review Group",
                "url": "https://www.facebook.com/groups/group-review",
            },
            "queued",
            db_path=self.db_path,
        )
        day_start = (self.now - timedelta(hours=1)).isoformat()
        claimed = claim_facebook_group_delivery(
            delivery["id"],
            "review-worker",
            db_path=self.db_path,
            lease_seconds=300,
            daily_since_iso=day_start,
            daily_limit=1,
            now=self.now,
        )
        self.assertTrue(claimed["acquired"])
        expire_facebook_group_deliveries(
            (self.now + timedelta(seconds=300)).isoformat(),
            db_path=self.db_path,
        )

        cancelled = cancel_facebook_group_delivery(delivery["id"], db_path=self.db_path)
        stored = get_facebook_group_delivery_by_id(delivery["id"], self.db_path)
        with connect_db(self.db_path) as conn:
            attempt = conn.execute(
                "SELECT status, stop_reason FROM facebook_group_attempts"
            ).fetchone()
        remaining_quota = count_facebook_group_attempts_since(day_start, db_path=self.db_path)

        self.assertTrue(cancelled)
        self.assertEqual(stored["status"], "cancelled")
        self.assertIsNone(stored["quota_reservation_token"])
        self.assertEqual(attempt["status"], "cancelled")
        self.assertEqual(attempt["stop_reason"], "confirmed_no_post")
        self.assertEqual(remaining_quota, 0)

    def test_delivery_claim_is_unique_per_channel_and_destination(self):
        telegram = claim_publish_delivery(
            RUN_ID,
            "url:story",
            "Telegram",
            "chat-1",
            "worker-a",
            db_path=self.db_path,
            payload={"message": "brief"},
            now=self.now,
        )
        duplicate = claim_publish_delivery(
            RUN_ID,
            "url:story",
            "telegram",
            "chat-1",
            "worker-b",
            db_path=self.db_path,
            now=self.now,
        )
        facebook = claim_publish_delivery(
            RUN_ID,
            "url:story",
            "facebook",
            "page-1",
            "worker-b",
            db_path=self.db_path,
            now=self.now,
        )
        completed = mark_publish_delivery_succeeded(
            RUN_ID,
            "url:story",
            "telegram",
            "chat-1",
            "worker-a",
            db_path=self.db_path,
            result={"message_id": "42"},
            now=self.now + timedelta(seconds=2),
        )
        after_success = claim_publish_delivery(
            RUN_ID,
            "url:story",
            "telegram",
            "chat-1",
            "worker-c",
            db_path=self.db_path,
            now=self.now + timedelta(seconds=3),
        )

        self.assertTrue(telegram["acquired"])
        self.assertFalse(duplicate["acquired"])
        self.assertEqual(duplicate["claim_reason"], "sending")
        self.assertTrue(facebook["acquired"])
        self.assertEqual(len(list_publish_deliveries(self.db_path, run_id=RUN_ID)), 2)
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["result"], {"message_id": "42"})
        self.assertFalse(after_success["acquired"])
        self.assertEqual(after_success["claim_reason"], "succeeded")

    def test_retryable_failure_can_be_reclaimed_but_non_retryable_cannot(self):
        self._claim_delivery(item_key="retry-item")
        failed = mark_publish_delivery_failed(
            RUN_ID,
            "retry-item",
            "telegram",
            "chat-1",
            "worker-a",
            "temporary network error",
            db_path=self.db_path,
            retryable=True,
            now=self.now + timedelta(seconds=1),
        )
        retried = claim_publish_delivery(
            RUN_ID,
            "retry-item",
            "telegram",
            "chat-1",
            "worker-b",
            db_path=self.db_path,
            now=self.now + timedelta(seconds=2),
        )
        final_failure = mark_publish_delivery_failed(
            RUN_ID,
            "retry-item",
            "telegram",
            "chat-1",
            "worker-b",
            "invalid destination",
            db_path=self.db_path,
            retryable=False,
            now=self.now + timedelta(seconds=3),
        )
        rejected = claim_publish_delivery(
            RUN_ID,
            "retry-item",
            "telegram",
            "chat-1",
            "worker-c",
            db_path=self.db_path,
            now=self.now + timedelta(seconds=4),
        )

        self.assertTrue(failed["retryable"])
        self.assertTrue(retried["acquired"])
        self.assertEqual(retried["attempt_count"], 2)
        self.assertFalse(final_failure["retryable"])
        self.assertFalse(rejected["acquired"])
        self.assertEqual(rejected["claim_reason"], "non_retryable_failure")

    def test_stale_sending_delivery_requires_review_and_is_not_retried(self):
        self._claim_delivery(item_key="stale-item", lease_seconds=300)

        recovered = recover_stale_publish_deliveries(
            self.db_path,
            now=self.now + timedelta(seconds=300),
        )
        rejected = claim_publish_delivery(
            RUN_ID,
            "stale-item",
            "telegram",
            "chat-1",
            "worker-b",
            db_path=self.db_path,
            now=self.now + timedelta(seconds=301),
        )
        delivery = get_publish_delivery(
            RUN_ID,
            "stale-item",
            "telegram",
            "chat-1",
            db_path=self.db_path,
        )

        self.assertEqual(recovered, 1)
        self.assertFalse(rejected["acquired"])
        self.assertEqual(rejected["claim_reason"], "needs_review")
        self.assertEqual(delivery["status"], "needs_review")
        self.assertEqual(delivery["error_message"], "worker_lease_expired")

    def test_owner_can_explicitly_quarantine_uncertain_delivery(self):
        self._claim_delivery(item_key="uncertain-item")

        reviewed = mark_publish_delivery_needs_review(
            RUN_ID,
            "uncertain-item",
            "telegram",
            "chat-1",
            "worker-a",
            "connection lost after submit",
            db_path=self.db_path,
            now=self.now + timedelta(seconds=1),
        )

        self.assertEqual(reviewed["status"], "needs_review")
        self.assertEqual(reviewed["error_message"], "connection lost after submit")
        self.assertIsNone(reviewed["owner"])

    def test_manual_review_can_resolve_or_release_delivery_for_retry(self):
        self._claim_delivery(item_key="reviewed-item")
        mark_publish_delivery_needs_review(
            RUN_ID,
            "reviewed-item",
            "telegram",
            "chat-1",
            "worker-a",
            "connection lost",
            db_path=self.db_path,
            now=self.now + timedelta(seconds=1),
        )
        delivery = get_publish_delivery(
            RUN_ID,
            "reviewed-item",
            "telegram",
            "chat-1",
            db_path=self.db_path,
        )

        resolved = resolve_publish_delivery(
            delivery["id"],
            "retry",
            "operator-a",
            "Remote channel has no matching post",
            db_path=self.db_path,
            now=self.now + timedelta(seconds=2),
        )
        reclaimed = claim_publish_delivery(
            RUN_ID,
            "reviewed-item",
            "telegram",
            "chat-1",
            "worker-b",
            db_path=self.db_path,
            now=self.now + timedelta(seconds=3),
        )

        self.assertEqual(resolved["status"], "failed")
        self.assertTrue(resolved["retryable"])
        self.assertEqual(resolved["result"]["manual_review"]["reviewer"], "operator-a")
        self.assertTrue(reclaimed["acquired"])

    def test_manual_success_resolution_updates_global_publish_ledger(self):
        claimed = claim_publish_delivery(
            RUN_ID,
            "url:remote-success",
            "telegram",
            "chat-1",
            "worker-a",
            db_path=self.db_path,
            payload={
                "item_key": "url:remote-success",
                "canonical_url": "https://example.com/remote-success",
                "title_hash": "hash-1",
                "title": "Remote success",
            },
            now=self.now,
        )
        mark_publish_delivery_needs_review(
            RUN_ID,
            "url:remote-success",
            "telegram",
            "chat-1",
            "worker-a",
            "connection lost",
            db_path=self.db_path,
            now=self.now + timedelta(seconds=1),
        )

        resolved = resolve_publish_delivery(
            claimed["id"],
            "succeeded",
            "operator-a",
            "Confirmed message exists remotely",
            db_path=self.db_path,
            now=self.now + timedelta(seconds=2),
        )
        published = list_published_item_keys(db_path=self.db_path)

        self.assertEqual(resolved["status"], "succeeded")
        self.assertTrue(any(row["item_key"] == "url:remote-success" for row in published))

    def _claim_delivery(self, item_key, lease_seconds=300):
        return claim_publish_delivery(
            RUN_ID,
            item_key,
            "telegram",
            "chat-1",
            "worker-a",
            db_path=self.db_path,
            lease_seconds=lease_seconds,
            now=self.now,
        )


if __name__ == "__main__":
    unittest.main()
