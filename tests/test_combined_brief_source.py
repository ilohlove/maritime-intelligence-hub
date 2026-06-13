import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.combined_brief_source import (
    canonicalize_url,
    filter_publishable_items,
    item_key,
    sheet_row_to_item,
    title_hash,
)
from app.services.visual_brief_renderer import generate_image_cards


class CombinedBriefSourceTests(unittest.TestCase):
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


def _write_fake_png(_html, output_path):
    Path(output_path).write_bytes(b"\x89PNG\r\n\x1a\nfake")


if __name__ == "__main__":
    unittest.main()
