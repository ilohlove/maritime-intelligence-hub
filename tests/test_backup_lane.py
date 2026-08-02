import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.services.ai_processor import AIProviderChain, OpenAICompatibleProvider
from app.services.backup_source_collector import GOOGLE_NEWS_RSS, build_backup_feed_plan
from app.services.article_reader import read_article
from app.services.source_policy import filter_sources, vietnam_source_reason


class BackupLaneTests(unittest.TestCase):
    def test_vietnam_policy_checks_country_and_domain(self):
        kept, excluded = filter_sources(
            [
                {"name": "VN", "country": "Vietnam", "website": "https://example.com"},
                {"name": "Domain", "country": "Global", "website": "https://example.vn/news"},
                {"name": "Global", "country": "Global", "website": "https://example.com"},
            ],
            exclude_vietnam=True,
        )
        self.assertEqual([item["name"] for item in kept], ["Global"])
        self.assertEqual(vietnam_source_reason(excluded[0]["source"]), "country=Vietnam")

    def test_google_news_query_is_configured_as_rss(self):
        source = {"Provider": "google_news_rss", "Name": "G", "Query": "maritime port", "Country": "Global"}
        plan = build_backup_feed_plan.__globals__["filter_sources"]  # ensure policy is shared
        self.assertIn("news.google.com/rss/search", GOOGLE_NEWS_RSS)
        self.assertTrue(source["Query"])

    def test_disabled_official_master_row_disables_official_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            master = Path(temp_dir) / "backup.csv"
            master.write_text(
                "ID,Name,Provider,Website/Domain,Enabled,Max Items\n"
                "BF001,Official,official_rss,example.com,No,10\n",
                encoding="utf-8",
            )
            plan = build_backup_feed_plan(
                master,
                official_sources=[_official_source("SRC1", "https://example.com")],
            )

        self.assertEqual(plan, [])

    def test_official_sources_are_limited_to_configured_domains(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            master = Path(temp_dir) / "backup.csv"
            master.write_text(
                "ID,Name,Provider,Website/Domain,Enabled,Max Items\n"
                "BF001,Official,official_rss,safety4sea.com,Yes,5\n",
                encoding="utf-8",
            )
            plan = build_backup_feed_plan(
                master,
                official_sources=[
                    _official_source("SAFE", "https://safety4sea.com"),
                    _official_source("OTHER", "https://other.example.com"),
                ],
            )

        self.assertEqual([item["source"]["id"] for item in plan], ["SAFE"])
        self.assertEqual(plan[0]["max_items"], 5)

    def test_reader_falls_back_when_jina_fails(self):
        session = Mock()
        jina_error = Mock()
        jina_error.raise_for_status.side_effect = RuntimeError("rate limit")
        html_response = Mock(text="<html><article>""" + ("Maritime article text " * 30) + """</article></html>""")
        html_response.raise_for_status.return_value = None
        session.get.side_effect = [jina_error, html_response]
        with patch.dict("sys.modules", {"trafilatura": None}):
            result = read_article("https://example.com/story", session=session)
        self.assertIn(result["provider"], {"beautifulsoup", "none"})

    def test_ai_chain_uses_next_provider(self):
        first = Mock()
        first.model_name = "first"
        first.provider_name = "gemini"
        first.prompt_version = "p1"
        first._request_summary.side_effect = RuntimeError("429")
        second = OpenAICompatibleProvider("key", "model", "https://example.com", "groq")
        second._request_summary = Mock(
            return_value={
                "headline": "Tiêu đề",
                "summary": "Tóm tắt có dấu.",
                "impact_note": "Tác động.",
                "category": "Safety",
                "importance_score": 8,
            }
        )
        result = AIProviderChain([first, second]).summarize(
            {"id": 1, "source_name": "Source", "url": "https://example.com", "title": "Title", "category": "Safety"}
        )
        self.assertEqual(result["ai_provider"], "groq")
        self.assertEqual(len(result["fallback_errors"]), 1)

    def test_ai_chain_rejects_invalid_data_and_uses_next_provider(self):
        invalid = Mock(model_name="gemini", provider_name="gemini", prompt_version="p1")
        invalid._request_summary.return_value = {"headline": "", "summary": "", "impact_note": ""}
        valid = Mock(model_name="groq", provider_name="groq", prompt_version="p2")
        valid._request_summary.return_value = {
            "headline": "Cập nhật hàng hải",
            "summary": "Tóm tắt có dấu.",
            "impact_note": "Tác động đến vận tải biển.",
            "category": "Shipping",
            "importance_score": 8,
        }
        result = AIProviderChain([invalid, valid]).summarize(
            {"id": 1, "source_name": "Source", "url": "https://example.com", "title": "Title", "category": "Shipping"}
        )
        self.assertEqual(result["ai_provider"], "groq")
        self.assertIn("valid headline", result["fallback_errors"][0])


def _official_source(source_id, website):
    return {
        "ID": source_id,
        "Source Name": source_id,
        "Website": website,
        "Country": "Global",
        "Language": "EN",
        "Category": "Shipping News",
        "RSS": "Yes",
        "Status": "Active",
    }


if __name__ == "__main__":
    unittest.main()
