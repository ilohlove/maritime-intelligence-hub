import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.facebook_group_publisher import (
    FacebookGroupBrowser,
    FacebookSafetyStop,
    build_batch_id,
    build_group_caption,
    cancel_group_queue_item,
    get_due_queue_item,
    list_group_queue,
    normalize_group_url,
    publish_to_groups,
    publish_queue_item,
    validate_group_config,
)
from app.services.storage import (
    count_facebook_group_attempts_since,
    get_facebook_group_delivery,
    record_facebook_group_delivery,
)


class FacebookGroupPublisherTests(unittest.TestCase):
    def test_normalize_group_url_removes_query_and_rejects_other_hosts(self):
        self.assertEqual(
            normalize_group_url("https://m.facebook.com/groups/maritime.vn/?ref=share"),
            "https://www.facebook.com/groups/maritime.vn",
        )
        with self.assertRaisesRegex(ValueError, "facebook.com"):
            normalize_group_url("https://example.com/groups/test")

    def test_group_validation_rejects_duplicate_id_and_url(self):
        result = validate_group_config(
            [
                {"id": "one", "name": "One", "url": "facebook.com/groups/one", "enabled": True},
                {"id": "one", "name": "Two", "url": "facebook.com/groups/two", "enabled": True},
                {"id": "three", "name": "Three", "url": "facebook.com/groups/one", "enabled": True},
            ]
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("duplicate id" in error for error in result["errors"]))
        self.assertTrue(any("duplicate URL" in error for error in result["errors"]))

    def test_group_caption_always_contains_sources(self):
        caption = build_group_caption(
            "Morning brief",
            [_card("unused.png", "item-1", "https://example.com/story")],
        )
        self.assertIn("Morning brief", caption)
        self.assertIn("Safety4Sea: https://example.com/story", caption)

    def test_publish_continues_after_failure_and_records_each_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "card.png"
            image.write_bytes(b"png")
            db_path = root / "test.db"
            cards = [_card(image, "item-1", "https://example.com/story")]
            groups = [
                {"id": "bad", "name": "Bad", "url": "facebook.com/groups/bad", "enabled": True, "caption_template": "Bad caption"},
                {"id": "good", "name": "Good", "url": "facebook.com/groups/good", "enabled": True, "caption_template": "Good caption"},
            ]
            fake = _FakeBrowser({"bad": RuntimeError("composer changed"), "good": {"status": "pending"}})

            result = publish_to_groups(
                cards,
                groups,
                "Caption",
                delay_min_seconds=0,
                delay_max_seconds=0,
                db_path=db_path,
                browser_factory=_factory(fake),
            )

            self.assertEqual([item["status"] for item in result["results"]], ["failed", "pending"])
            batch_id = build_batch_id(cards)
            self.assertEqual(get_facebook_group_delivery(batch_id, "bad", db_path=db_path)["status"], "failed")
            self.assertEqual(get_facebook_group_delivery(batch_id, "good", db_path=db_path)["status"], "pending")

    def test_pending_delivery_is_not_posted_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "card.png"
            image.write_bytes(b"png")
            db_path = root / "test.db"
            cards = [_card(image, "item-1", "https://example.com/story")]
            groups = [{"id": "group", "name": "Group", "url": "facebook.com/groups/group", "enabled": True, "caption_template": "Group caption"}]
            first_browser = _FakeBrowser({"group": {"status": "pending"}})
            publish_to_groups(cards, groups, "Caption", db_path=db_path, browser_factory=_factory(first_browser))
            second_browser = _FakeBrowser({"group": {"status": "published"}})

            result = publish_to_groups(cards, groups, "Caption", db_path=db_path, browser_factory=_factory(second_browser))

            self.assertEqual(result["results"][0]["status"], "skipped")
            self.assertEqual(second_browser.calls, [])

    def test_dry_run_checks_group_without_writing_ledger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "card.png"
            image.write_bytes(b"png")
            db_path = root / "test.db"
            cards = [_card(image, "item-1", "https://example.com/story")]
            groups = [{"id": "group", "name": "Group", "url": "facebook.com/groups/group", "enabled": True, "caption_template": "Group caption"}]
            fake = _FakeBrowser({})

            result = publish_to_groups(cards, groups, "Caption", dry_run=True, db_path=db_path, browser_factory=_factory(fake))

            self.assertEqual(result["results"][0]["status"], "dry_run")
            self.assertEqual(len(fake.checked), 1)
            self.assertIsNone(get_facebook_group_delivery(build_batch_id(cards), "group", db_path=db_path))

    def test_browser_adapter_posts_against_local_composer_fixture(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "card.png"
            image.write_bytes(b"png")
            fixture = root / "composer.html"
            fixture.write_text(
                """
                <!doctype html><html><body>
                <button aria-label="Write something" onclick="document.getElementById('composer').style.display='block'">Open</button>
                <div id="composer" role="dialog" style="display:none">
                  <div contenteditable="true" role="textbox"></div>
                  <input type="file" multiple>
                  <button onclick="document.getElementById('composer').style.display='none'">Post</button>
                </div>
                </body></html>
                """,
                encoding="utf-8",
            )
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch()
                except Exception as exc:
                    if "browser" in str(exc).lower() or "executable" in str(exc).lower():
                        self.skipTest(str(exc))
                    raise
                try:
                    page = browser.new_page()
                    result = FacebookGroupBrowser(page).publish_group(
                        fixture.resolve().as_uri(), "Caption", [image], "fixture"
                    )
                finally:
                    browser.close()

            self.assertEqual(result["status"], "published")

    def test_auto_publish_limits_each_brief_to_two_groups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "card.png"
            image.write_bytes(b"png")
            cards = [_card(image, "item-1", "https://example.com/story")]
            groups = [_group(str(index)) for index in range(1, 4)]
            fake = _FakeBrowser({str(index): {"status": "published"} for index in range(1, 4)})
            sleeps = []

            result = publish_to_groups(
                cards,
                groups,
                "unused",
                db_path=root / "test.db",
                browser_factory=_factory(fake),
                sleep_fn=sleeps.append,
                random_uniform=lambda _minimum, _maximum: 900,
            )

            self.assertEqual(fake.calls, ["1", "2"])
            self.assertEqual(sleeps, [900])
            self.assertEqual(result["counts"]["queued"], 1)
            self.assertEqual(result["safety"]["used_today"], 2)

    def test_manual_publish_processes_one_queued_group_and_counts_daily_quota(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "card.png"
            image.write_bytes(b"png")
            cards = [_card(image, "item-1", "https://example.com/story")]
            groups = [_group(str(index)) for index in range(1, 4)]
            first = _FakeBrowser({"1": {"status": "published"}, "2": {"status": "published"}})
            publish_to_groups(
                cards,
                groups,
                "unused",
                db_path=root / "test.db",
                browser_factory=_factory(first),
                sleep_fn=lambda _seconds: None,
            )
            manual = _FakeBrowser({"3": {"status": "pending"}})

            result = publish_to_groups(
                cards,
                groups,
                "unused",
                manual=True,
                db_path=root / "test.db",
                browser_factory=_factory(manual),
            )

            self.assertEqual(manual.calls, ["3"])
            self.assertEqual(result["counts"]["pending"], 1)
            self.assertEqual(result["safety"]["used_today"], 3)

    def test_daily_limit_queues_without_opening_browser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            for index in range(4):
                record_facebook_group_delivery(f"old-{index}", _group(f"old-{index}"), "published", db_path=db_path)
            image = root / "card.png"
            image.write_bytes(b"png")
            cards = [_card(image, "item-1", "https://example.com/story")]
            fake = _FakeBrowser({"new": {"status": "published"}})

            result = publish_to_groups(
                cards,
                [_group("new")],
                "unused",
                db_path=db_path,
                browser_factory=_factory(fake),
                now=datetime.now(timezone.utc),
            )

            self.assertEqual(fake.calls, [])
            self.assertEqual(result["counts"]["queued"], 1)
            self.assertEqual(result["safety"]["remaining_today"], 0)

    def test_duplicate_or_missing_group_captions_are_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "card.png"
            image.write_bytes(b"png")
            cards = [_card(image, "item-1", "https://example.com/story")]
            groups = [_group("one", caption="Same caption"), _group("two", caption="Same caption")]

            with self.assertRaisesRegex(ValueError, "caption duplicates"):
                publish_to_groups(cards, groups, "unused", dry_run=True, browser_factory=_factory(_FakeBrowser({})))

            missing = [_group("one", caption="")]
            with self.assertRaisesRegex(ValueError, "caption is required"):
                publish_to_groups(cards, missing, "unused", dry_run=True, browser_factory=_factory(_FakeBrowser({})))

    def test_safety_signal_stops_remaining_queue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            image = root / "card.png"
            image.write_bytes(b"png")
            cards = [_card(image, "item-1", "https://example.com/story")]
            groups = [_group("one"), _group("two"), _group("three")]
            fake = _FakeBrowser(
                {
                    "one": FacebookSafetyStop("Facebook temporarily blocked this action"),
                    "two": {"status": "published"},
                    "three": {"status": "published"},
                }
            )

            result = publish_to_groups(
                cards,
                groups,
                "unused",
                db_path=db_path,
                browser_factory=_factory(fake),
                sleep_fn=lambda _seconds: None,
            )

            self.assertEqual(fake.calls, ["one"])
            self.assertEqual(result["counts"]["failed"], 1)
            self.assertEqual(result["counts"]["queued"], 2)
            queued = get_facebook_group_delivery(build_batch_id(cards), "two", db_path=db_path)
            self.assertIn("temporarily blocked", queued["stop_reason"])
            excess = get_facebook_group_delivery(build_batch_id(cards), "three", db_path=db_path)
            self.assertIn("temporarily blocked", excess["stop_reason"])

    def test_persisted_queue_resumes_for_same_batch_after_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            image = root / "card.png"
            image.write_bytes(b"png")
            cards = [_card(image, "item-1", "https://example.com/story")]
            group = _group("resume")
            record_facebook_group_delivery(build_batch_id(cards), group, "queued", db_path=db_path)
            fake = _FakeBrowser({"resume": {"status": "published"}})

            result = publish_to_groups(
                cards,
                [group],
                "unused",
                db_path=db_path,
                browser_factory=_factory(fake),
            )

            self.assertEqual(fake.calls, ["resume"])
            self.assertEqual(result["counts"]["published"], 1)

    def test_daily_attempt_log_counts_retries_of_same_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            group = _group("retry")
            record_facebook_group_delivery("batch", group, "failed", db_path=db_path)
            record_facebook_group_delivery("batch", group, "failed", db_path=db_path)

            count = count_facebook_group_attempts_since(
                (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), db_path=db_path
            )

            self.assertEqual(count, 2)

    def test_queue_manager_lists_publishes_and_cancels_selected_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            image = root / "card.png"
            image.write_bytes(b"png")
            cards = [_card(image, "item-1", "https://example.com/story")]
            groups = [_group("one"), _group("two"), _group("three")]
            first = _FakeBrowser({"one": {"status": "published"}})
            publish_to_groups(
                cards,
                groups,
                "unused",
                max_groups_per_brief=1,
                db_path=db_path,
                browser_factory=_factory(first),
            )
            queue = list_group_queue(db_path=db_path)
            selected = next(item for item in queue if item["group_id"] == "two")
            manual = _FakeBrowser({"two": {"status": "pending"}})

            result = publish_queue_item(selected["id"], db_path=db_path, browser_factory=_factory(manual))

            self.assertEqual(result["status"], "pending")
            remaining = list_group_queue(db_path=db_path)
            cancellable = next(item for item in remaining if item["group_id"] == "three")
            self.assertTrue(cancel_group_queue_item(cancellable["id"], db_path=db_path))
            self.assertFalse(any(item["group_id"] == "three" for item in list_group_queue(db_path=db_path)))

    def test_rotation_prefers_group_never_published_before(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            first_image = root / "first.png"
            second_image = root / "second.png"
            first_image.write_bytes(b"png")
            second_image.write_bytes(b"png")
            groups = [_group("one"), _group("two"), _group("three")]
            first_cards = [_card(first_image, "item-1", "https://example.com/one")]
            publish_to_groups(
                first_cards,
                groups,
                "unused",
                max_groups_per_brief=2,
                max_groups_per_day=4,
                db_path=db_path,
                browser_factory=_factory(_FakeBrowser({"one": {"status": "published"}, "two": {"status": "published"}})),
                sleep_fn=lambda _seconds: None,
            )
            second_cards = [_card(second_image, "item-2", "https://example.com/two")]
            rotated = _FakeBrowser({"three": {"status": "published"}, "one": {"status": "published"}})

            publish_to_groups(
                second_cards,
                groups,
                "unused",
                max_groups_per_brief=2,
                max_groups_per_day=4,
                db_path=db_path,
                browser_factory=_factory(rotated),
                sleep_fn=lambda _seconds: None,
            )

            self.assertEqual(rotated.calls[0], "three")

    def test_due_queue_item_survives_interrupted_delay(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            image = root / "card.png"
            image.write_bytes(b"png")
            cards = [_card(image, "item-1", "https://example.com/story")]
            groups = [_group("one"), _group("two")]
            start = datetime.now(timezone.utc)

            with self.assertRaisesRegex(RuntimeError, "app stopped"):
                publish_to_groups(
                    cards,
                    groups,
                    "unused",
                    db_path=db_path,
                    browser_factory=_factory(_FakeBrowser({"one": {"status": "published"}})),
                    sleep_fn=lambda _seconds: (_ for _ in ()).throw(RuntimeError("app stopped")),
                    random_uniform=lambda _minimum, _maximum: 900,
                    now=start,
                )

            due = get_due_queue_item(db_path=db_path, now=start + timedelta(seconds=901))
            self.assertIsNotNone(due)
            self.assertEqual(due["group_id"], "two")

    def test_queue_manager_expires_stale_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            group = _group("stale")
            record_facebook_group_delivery(
                "batch",
                group,
                "queued",
                expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                db_path=db_path,
            )

            queue = list_group_queue(db_path=db_path)

            self.assertEqual(queue, [])
            delivery = get_facebook_group_delivery("batch", "stale", db_path=db_path)
            self.assertEqual(delivery["status"], "expired")


class _FakeBrowser:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []
        self.checked = []

    def check_group(self, url):
        self.checked.append(url)

    def publish_group(self, url, caption, image_paths, group_id):
        self.calls.append(group_id)
        outcome = self.outcomes[group_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _factory(browser):
    @contextmanager
    def factory(profile_dir=None, headed=True):
        yield browser

    return factory


def _card(path, item_key, url):
    return {
        "card_path": str(path),
        "item_key": item_key,
        "source_name": "Safety4Sea",
        "original_url": url,
        "title": "Port update",
    }


def _group(group_id, caption=None):
    return {
        "id": group_id,
        "name": f"Group {group_id}",
        "url": f"facebook.com/groups/{group_id}",
        "enabled": True,
        "caption_template": caption if caption is not None else f"Caption for {group_id}",
    }


if __name__ == "__main__":
    unittest.main()
