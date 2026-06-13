import csv
import tempfile
import unittest
from pathlib import Path

from app.services.source_master import REQUIRED_COLUMNS, append_manual_source, get_fetch_plan, validate_source_master


class SourceMasterTests(unittest.TestCase):
    def test_current_source_master_is_valid(self):
        result = validate_source_master(Path("NEWS_SOURCE_MASTER.csv"))

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.row_count, 32)
        self.assertEqual(result.priority_counts.get("P1"), 18)

    def test_duplicate_id_is_rejected(self):
        rows = [
            _valid_row("SRC001", "Source A", "https://example.com/a"),
            _valid_row("SRC001", "Source B", "https://example.com/b"),
        ]

        with _temporary_source_master(rows) as path:
            result = validate_source_master(path)

        self.assertFalse(result.ok)
        self.assertTrue(any("duplicate ID" in error for error in result.errors))

    def test_invalid_score_is_rejected(self):
        row = _valid_row("SRC001", "Source A", "https://example.com/a")
        row["Content Quality Score"] = "11"

        with _temporary_source_master([row]) as path:
            result = validate_source_master(path)

        self.assertFalse(result.ok)
        self.assertTrue(any("Content Quality Score" in error for error in result.errors))

    def test_fetch_plan_groups_active_p1_sources(self):
        plan = get_fetch_plan(Path("NEWS_SOURCE_MASTER.csv"), priority="P1")

        self.assertEqual(len(plan["rss"]), 6)
        self.assertEqual(len(plan["partial_rss"]), 2)
        self.assertEqual(len(plan["html"]), 10)
        self.assertEqual(len(plan["manual"]), 0)

    def test_append_manual_source_adds_valid_defaulted_row(self):
        rows = [_valid_row("SRC001", "Source A", "https://example.com/a")]

        with _temporary_source_master(rows) as path:
            row = append_manual_source(
                path,
                {
                    "Source Name": "Manual Maritime",
                    "Website": "https://manual.example.com",
                    "Priority": "P2",
                },
            )
            result = validate_source_master(path)

        self.assertEqual(row["ID"], "SRC002")
        self.assertEqual(row["Status"], "Active")
        self.assertTrue(result.ok, result.errors)

    def test_append_manual_source_rejects_duplicate_website_before_write(self):
        rows = [_valid_row("SRC001", "Source A", "https://example.com/a")]

        with _temporary_source_master(rows) as path:
            with self.assertRaises(ValueError):
                append_manual_source(
                    path,
                    {
                        "Source Name": "Duplicate",
                        "Website": "https://example.com/a",
                    },
                )
            result = validate_source_master(path)

        self.assertEqual(result.row_count, 1)


def _valid_row(source_id, source_name, website):
    return {
        "ID": source_id,
        "Source Name": source_name,
        "Website": website,
        "Country": "Global",
        "Language": "EN",
        "Type": "Media",
        "Category": "Shipping News",
        "Priority": "P1",
        "RSS": "Yes",
        "API": "No",
        "Crawl Method": "RSS",
        "Frequency": "Daily",
        "Audience": "All",
        "Content Quality Score": "8",
        "Business Value Score": "8",
        "Crawl Difficulty": "Easy",
        "Copyright Risk": "Low",
        "AI Summary Enabled": "Yes",
        "Status": "Active",
    }


class _temporary_source_master:
    def __init__(self, rows):
        self.rows = rows
        self.temp_dir = None
        self.path = None

    def __enter__(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "sources.csv"
        with self.path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=REQUIRED_COLUMNS)
            writer.writeheader()
            writer.writerows(self.rows)
        return self.path

    def __exit__(self, exc_type, exc, tb):
        self.temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
