import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from app.services.ai_processor import (
    GeminiChatProvider,
    MockAIProvider,
    build_mock_summary,
    get_ai_provider,
    normalize_ai_payload,
    summarize_pending_articles,
)
from app.services.brief_writer import generate_brief
from app.services.evernote_summarizer import (
    clean_article_text,
    normalize_evernote_output,
    summarize_article_with_evernote,
)
from app.services.html_collector import fetch_html_source, parse_html_articles
from app.services.rss_collector import fetch_rss_source, parse_rss_items
from app.services.scoring import calculate_importance_score
from app.services.source_master import load_sources
from app.services.storage import (
    count_rows,
    get_brief_candidates,
    get_articles_for_summary,
    init_db,
    sync_sources,
    upsert_article,
    upsert_summary,
    utc_now,
)
from app.services.trend_collector import parse_trends_csv, parse_trends_rss


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
    <title>Sample Maritime Feed</title>
    <item>
      <title>Port safety notice issued</title>
      <link>https://example.com/port-safety</link>
      <description>Short operational safety update.</description>
      <pubDate>Tue, 09 Jun 2026 01:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

SAMPLE_HTML = """
<html>
  <body>
    <article>
      <a href="/news/port-safety-notice">Port safety notice issued for pilotage operations</a>
      <time>2026-06-09</time>
      <p>Short operational safety update for port and vessel operators.</p>
    </article>
    <article>
      <a href="https://example.com/news/container-market-update">Container market update affects Asia-Europe shipping capacity</a>
      <span>09/06/2026</span>
    </article>
  </body>
</html>
"""

SAMPLE_TRENDS_CSV = """Xu hướng,Lượng tìm kiếm,Đã bắt đầu,Trạng thái
Red Sea shipping,200K,4 giờ qua,Đang hoạt động
World Cup,500K,2 giờ qua,Đang hoạt động
"""

SAMPLE_TRENDS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Panama Canal shipping</title>
      <pubDate>Tue, 09 Jun 2026 01:00:00 GMT</pubDate>
      <ht:approx_traffic xmlns:ht="https://trends.google.com/trends/trendingsearches/daily">100K+</ht:approx_traffic>
    </item>
  </channel>
</rss>
"""


class MvpPipelineTests(unittest.TestCase):
    def test_sqlite_schema_and_source_sync_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            rows, _ = load_sources("NEWS_SOURCE_MASTER.csv")

            init_db(db_path)
            first_count = sync_sources(rows, db_path=db_path)
            second_count = sync_sources(rows, db_path=db_path)

            self.assertEqual(first_count, 32)
            self.assertEqual(second_count, 32)
            self.assertEqual(count_rows("sources", db_path=db_path), 32)

    def test_parse_sample_rss(self):
        source = _source()

        items = parse_rss_items(SAMPLE_RSS, source, feed_url="https://example.com/feed")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Port safety notice issued")
        self.assertEqual(items[0]["url"], "https://example.com/port-safety")

    def test_parse_rss_repairs_common_mojibake(self):
        source = _source()
        rss = SAMPLE_RSS.replace(
            "Port safety notice issued",
            "China\u00e2\u20ac\u2122s Navy Sends Patrol Ship",
        )

        items = parse_rss_items(rss, source, feed_url="https://example.com/feed")

        self.assertEqual(items[0]["title"], "China\u2019s Navy Sends Patrol Ship")

    def test_malformed_rss_failure_does_not_crash_source_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            init_db(db_path)
            response = Mock()
            response.text = "<rss><channel><item>"
            response.raise_for_status.return_value = None
            session = Mock()
            session.get.return_value = response
            source = _source()
            source["rss_url"] = "https://example.com/feed"

            result = fetch_rss_source(source, db_path=db_path, session=session)

            self.assertEqual(result["status"], "error")

    def test_article_dedup_by_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            init_db(db_path)
            rows, _ = load_sources("NEWS_SOURCE_MASTER.csv")
            sync_sources(rows, db_path=db_path)
            article = _article()

            _, created_first = upsert_article(article, db_path=db_path)
            _, created_second = upsert_article(article, db_path=db_path)

            self.assertTrue(created_first)
            self.assertFalse(created_second)
            self.assertEqual(count_rows("articles", db_path=db_path), 1)

    def test_score_stays_in_range(self):
        score = calculate_importance_score(
            {
                "content_quality_score": 10,
                "business_value_score": 10,
                "priority": "P1",
                "category": "Safety",
                "copyright_risk": "Low",
                "status": "new",
                "title": "Major port safety accident update",
            }
        )

        self.assertGreaterEqual(score, 1)
        self.assertLessEqual(score, 10)

    def test_parse_google_trends_csv_sample(self):
        trends = parse_trends_csv(SAMPLE_TRENDS_CSV, timeframe="24h")

        self.assertEqual(len(trends), 2)
        self.assertEqual(trends[0].keyword, "Red Sea shipping")
        self.assertEqual(trends[0].category, "maritime_core")
        self.assertEqual(trends[1].category, "general_hot")

    def test_parse_google_trends_rss_sample(self):
        trends = parse_trends_rss(SAMPLE_TRENDS_RSS, timeframe="24h")

        self.assertEqual(len(trends), 1)
        self.assertEqual(trends[0].keyword, "Panama Canal shipping")
        self.assertEqual(trends[0].search_volume, 100)

    def test_hot_keyword_increases_hotness_score(self):
        from app.services.scoring import calculate_hotness_score

        article = _article()
        article.update(
            {
                "title": "Red Sea shipping disruption raises container freight rates",
                "content_quality_score": 8,
                "business_value_score": 8,
                "priority": "P1",
                "copyright_risk": "Low",
                "country": "Global",
            }
        )
        result = calculate_hotness_score(
            article,
            trends=[{"keyword": "Red Sea", "category": "maritime_core"}],
        )

        self.assertIn("Red Sea", result["hot_keywords"])
        self.assertGreaterEqual(result["hotness_score"], result["importance_score"])

    def test_mock_summary_contains_source_context(self):
        summary = build_mock_summary(
            {
                "id": 1,
                "source_name": "Safety4Sea",
                "category": "Safety",
                "title": "Safety update",
                "description": "Short description.",
            }
        )

        self.assertIn("Safety4Sea", summary["summary"])
        self.assertEqual(summary["token_usage"], 0)
        self.assertIn("đưa tin", summary["summary"])
        self.assertIn("Tác động hàng hải", summary["impact_note"])

    def test_unaccented_ai_output_falls_back_to_accented_vietnamese(self):
        article = _article()
        payload = {
            "headline": "Cap nhat hang hai",
            "summary": "Tom tat nguon tin ve hang hai va tac dong logistics.",
            "impact_note": "Tac dong hang hai can theo doi.",
        }

        summary = normalize_ai_payload(payload, article, prompt_version="test", model_name="test-model")

        self.assertIn("Tác động hàng hải", summary["impact_note"])
        self.assertIn("đưa tin", summary["summary"])

    def test_parse_sample_html_articles(self):
        articles = parse_html_articles(SAMPLE_HTML, _html_source(), base_url="https://example.com/news", limit=2)

        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0]["title"], "Port safety notice issued for pilotage operations")
        self.assertEqual(articles[0]["url"], "https://example.com/news/port-safety-notice")
        self.assertEqual(articles[0]["source_id"], "SRC001")

    def test_html_fetch_failure_does_not_crash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            init_db(db_path)
            response = Mock()
            response.raise_for_status.side_effect = RuntimeError("network unavailable")
            session = Mock()
            session.get.return_value = response

            result = fetch_html_source(_html_source(), db_path=db_path, session=session)

            self.assertEqual(result["status"], "error")

    def test_ai_provider_defaults_to_mock_without_api_key(self):
        with patch.dict("os.environ", {"AI_PROVIDER": "openai", "OPENAI_API_KEY": ""}, clear=False):
            provider = get_ai_provider()

        self.assertIsInstance(provider, MockAIProvider)

    def test_ai_provider_supports_gemini(self):
        with patch.dict(
            "os.environ",
            {
                "AI_PROVIDER": "gemini",
                "GEMINI_API_KEY": "test-key",
                "GEMINI_MODEL": "gemini-test-model",
            },
            clear=False,
        ):
            provider = get_ai_provider()

        self.assertIsInstance(provider, GeminiChatProvider)
        self.assertEqual(provider.model_name, "gemini-test-model")

    def test_summary_candidates_skip_duplicates_and_low_scores(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            init_db(db_path)
            rows, _ = load_sources("NEWS_SOURCE_MASTER.csv")
            sync_sources(rows, db_path=db_path)
            high_score_id, _ = upsert_article(_article(), db_path=db_path)

            duplicate = _article()
            duplicate["url"] = "https://safety4sea.com/duplicate-title"
            duplicate["importance_score"] = 9
            upsert_article(duplicate, db_path=db_path)

            low_score = _article()
            low_score["title"] = "Low score article"
            low_score["url"] = "https://safety4sea.com/low-score"
            low_score["normalized_title"] = "low score article"
            low_score["title_hash"] = "low-score-title-hash"
            low_score["importance_score"] = 5
            upsert_article(low_score, db_path=db_path)

            candidates = get_articles_for_summary(db_path=db_path, min_score=6)
            summaries = summarize_pending_articles(db_path=db_path, min_score=6, provider=MockAIProvider())

            self.assertEqual([item["id"] for item in candidates], [high_score_id])
            self.assertEqual(len(summaries), 1)

    def test_brief_outputs_markdown_and_json_with_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "mih.db"
            output_dir = temp_path / "briefs"
            init_db(db_path)
            rows, _ = load_sources("NEWS_SOURCE_MASTER.csv")
            sync_sources(rows, db_path=db_path)
            article_id, _ = upsert_article(_article(), db_path=db_path)
            upsert_summary(
                {
                    "article_id": article_id,
                    "summary": "Tom tat nguon tin co link goc.",
                    "impact_note": "Tac dong hang hai can theo doi.",
                    "prompt_version": "mock-v1",
                    "model_name": "rule-based-mock",
                    "token_usage": 0,
                },
                db_path=db_path,
            )

            result = generate_brief("morning", db_path=db_path, output_dir=output_dir)
            payload = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
            markdown = Path(result["markdown_path"]).read_text(encoding="utf-8")

            self.assertEqual(payload["items"][0]["original_url"], "https://safety4sea.com/test")
            self.assertEqual(payload["publish_safety"]["ready"], True)
            self.assertIn("https://safety4sea.com/test", markdown)

    def test_daily_brief_excludes_old_and_missing_published_at(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            init_db(db_path)
            rows, _ = load_sources("NEWS_SOURCE_MASTER.csv")
            sync_sources(rows, db_path=db_path)
            recent_id, _ = upsert_article(_article(), db_path=db_path)
            old_article = _article(
                title="Old maritime article",
                url="https://safety4sea.com/old",
                published_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
            )
            missing_date_article = _article(
                title="Missing date maritime article",
                url="https://safety4sea.com/missing-date",
                published_at=None,
            )
            old_id, _ = upsert_article(old_article, db_path=db_path)
            missing_date_id, _ = upsert_article(missing_date_article, db_path=db_path)
            for article_id in [recent_id, old_id, missing_date_id]:
                upsert_summary(
                    {
                        "article_id": article_id,
                        "summary": "Tom tat co link goc.",
                        "impact_note": "Tac dong hang hai.",
                        "prompt_version": "mock-v1",
                        "model_name": "rule-based-mock",
                        "token_usage": 0,
                    },
                    db_path=db_path,
                )

            candidates = get_brief_candidates(db_path=db_path, brief_type="morning", limit=10)

            self.assertEqual([item["id"] for item in candidates], [recent_id])

    def test_brief_prefers_international_sources_when_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mih.db"
            init_db(db_path)
            rows, _ = load_sources("NEWS_SOURCE_MASTER.csv")
            sync_sources(rows, db_path=db_path)
            article_ids = []
            for index in range(8):
                article = _article(
                    source_id="SRC006",
                    source_name="gCaptain",
                    title=f"International hot maritime article {index}",
                    url=f"https://gcaptain.com/hot-{index}",
                )
                article_id, _ = upsert_article(article, db_path=db_path)
                article_ids.append(article_id)
            for index in range(4):
                article = _article(
                    source_id="SRC017",
                    source_name="VIMC",
                    title=f"Vietnam maritime article {index}",
                    url=f"https://vimc.co/hot-{index}",
                )
                article_id, _ = upsert_article(article, db_path=db_path)
                article_ids.append(article_id)
            for article_id in article_ids:
                upsert_summary(
                    {
                        "article_id": article_id,
                        "summary": "Tom tat co link goc.",
                        "impact_note": "Tac dong hang hai.",
                        "prompt_version": "mock-v1",
                        "model_name": "rule-based-mock",
                        "token_usage": 0,
                    },
                    db_path=db_path,
                )

            candidates = get_brief_candidates(db_path=db_path, brief_type="morning", limit=10)
            international_count = sum(1 for item in candidates if item["country"] != "Vietnam")

            self.assertGreaterEqual(international_count, 6)

    def test_general_hot_trends_are_limited_to_two(self):
        from app.services.brief_writer import build_general_hot_payload

        trends = [
            {"keyword": "World Cup", "category": "general_hot", "timeframe": "24h"},
            {"keyword": "bong da", "category": "general_hot", "timeframe": "24h"},
            {"keyword": "ket qua", "category": "general_hot", "timeframe": "24h"},
        ]

        payload = build_general_hot_payload(trends)

        self.assertEqual(len(payload), 2)

    def test_evernote_clean_article_text_removes_html(self):
        article = {
            "description": "<nav>Menu</nav><p>Port congestion update&nbsp;for container terminals.</p><script>x()</script>"
        }

        cleaned = clean_article_text(article)

        self.assertEqual(cleaned, "Port congestion update for container terminals.")

    def test_evernote_normalize_output_matches_summary_schema(self):
        article = _article()

        summary = normalize_evernote_output("Tom tat ngan ve tin hang hai.", article)

        self.assertEqual(summary["article_id"], article["id"])
        self.assertEqual(summary["summary"], "Tom tat ngan ve tin hang hai.")
        self.assertEqual(summary["model_name"], "evernote-web")
        self.assertEqual(summary["prompt_version"], "evernote-web-v1")
        self.assertEqual(summary["original_url"], article["url"])

    def test_evernote_dry_run_generates_prompt_without_calling_web(self):
        article = _article()
        article["description"] = "A" * 320

        result = summarize_article_with_evernote(article, dry_run=True, save=False)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "dry_run")
        self.assertIn("Tóm tắt tin hàng hải", result.prompt)

    def test_evernote_short_text_is_rejected(self):
        article = _article()
        article["description"] = "Too short"

        result = summarize_article_with_evernote(article, dry_run=True, save=False)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "text_too_short")


def _source():
    return {
        "id": "SRC004",
        "name": "Safety4Sea",
        "website": "https://safety4sea.com",
        "language": "EN",
        "category": "Safety",
    }


def _html_source():
    return {
        "ID": "SRC001",
        "Source Name": "IMO",
        "Website": "https://example.com",
        "Language": "EN",
        "Category": "Regulation",
    }


def _article(source_id="SRC004", source_name="Safety4Sea", title="Safety4Sea test article", url="https://safety4sea.com/test", published_at="auto"):
    published_at = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        if published_at == "auto"
        else published_at
    )
    normalized = title.lower()
    return {
        "id": 1,
        "source_id": source_id,
        "source_name": source_name,
        "title": title,
        "url": url,
        "normalized_title": normalized,
        "title_hash": f"test-title-hash-{abs(hash((title, url))) }",
        "published_at": published_at,
        "fetched_at": utc_now(),
        "language": "EN",
        "category": "Safety",
        "description": "Short safety description.",
        "content_excerpt": "Short safety description.",
        "importance_score": 8,
    }


if __name__ == "__main__":
    unittest.main()
