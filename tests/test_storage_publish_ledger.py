import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.services.storage import mark_items_published


class StoragePublishLedgerTests(unittest.TestCase):
    def test_channel_metadata_is_preserved_across_publish_updates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            item = {
                "item_key": "url:test",
                "canonical_url": "https://example.com/story",
                "title_hash": "hash",
                "title": "Story",
                "source_name": "Source",
                "source_type": "app",
            }

            mark_items_published([item], telegram_chat_id="chat-1", db_path=db_path)
            mark_items_published([item], facebook_page_id="page-1", facebook_post_id="post-1", db_path=db_path)

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT telegram_chat_id, facebook_page_id, facebook_post_id FROM published_items WHERE item_key = ?",
                    ("url:test",),
                ).fetchone()
            finally:
                conn.close()

        self.assertEqual(row, ("chat-1", "page-1", "post-1"))


if __name__ == "__main__":
    unittest.main()
