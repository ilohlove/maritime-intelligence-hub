import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import ROOT_DIR


DEFAULT_DB_PATH = ROOT_DIR / "data" / "mih.db"


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def connect_db(db_path=DEFAULT_DB_PATH):
    path = Path(db_path)
    path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=DEFAULT_DB_PATH):
    with connect_db(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                website TEXT NOT NULL UNIQUE,
                country TEXT NOT NULL,
                language TEXT NOT NULL,
                type TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                rss TEXT NOT NULL,
                api TEXT NOT NULL,
                crawl_method TEXT NOT NULL,
                frequency TEXT NOT NULL,
                audience TEXT NOT NULL,
                content_quality_score INTEGER NOT NULL,
                business_value_score INTEGER NOT NULL,
                crawl_difficulty TEXT NOT NULL,
                copyright_risk TEXT NOT NULL,
                ai_summary_enabled TEXT NOT NULL,
                status TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                normalized_title TEXT NOT NULL,
                title_hash TEXT NOT NULL,
                published_at TEXT,
                fetched_at TEXT NOT NULL,
                language TEXT,
                category TEXT,
                description TEXT,
                content_excerpt TEXT,
                importance_score INTEGER,
                hotness_score INTEGER,
                hot_keywords TEXT,
                why_hot TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                FOREIGN KEY(source_id) REFERENCES sources(id)
            );

            CREATE INDEX IF NOT EXISTS idx_articles_source_id ON articles(source_id);
            CREATE INDEX IF NOT EXISTS idx_articles_title_hash ON articles(title_hash);
            CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);

            CREATE TABLE IF NOT EXISTS article_summaries (
                article_id INTEGER PRIMARY KEY,
                headline TEXT,
                summary TEXT NOT NULL,
                impact_note TEXT NOT NULL,
                category TEXT,
                importance_score INTEGER,
                source_name TEXT,
                original_url TEXT,
                prompt_version TEXT NOT NULL,
                model_name TEXT NOT NULL,
                token_usage INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brief_type TEXT NOT NULL,
                title TEXT NOT NULL,
                markdown TEXT NOT NULL,
                json_payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fetch_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                url TEXT,
                message TEXT,
                fetched_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trend_keywords (
                keyword TEXT NOT NULL,
                category TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                search_volume INTEGER,
                started_at TEXT,
                status TEXT,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY(keyword, timeframe, source)
            );

            CREATE TABLE IF NOT EXISTS published_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_key TEXT NOT NULL UNIQUE,
                canonical_url TEXT,
                title_hash TEXT,
                title TEXT,
                source_name TEXT,
                source_type TEXT,
                published_at TEXT,
                sent_at TEXT NOT NULL,
                telegram_chat_id TEXT,
                facebook_page_id TEXT,
                facebook_post_id TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_published_items_url ON published_items(canonical_url);
            CREATE INDEX IF NOT EXISTS idx_published_items_title_hash ON published_items(title_hash);
            """
        )
        _ensure_column(conn, "articles", "hotness_score", "INTEGER")
        _ensure_column(conn, "articles", "hot_keywords", "TEXT")
        _ensure_column(conn, "articles", "why_hot", "TEXT")
        _ensure_column(conn, "article_summaries", "headline", "TEXT")
        _ensure_column(conn, "article_summaries", "category", "TEXT")
        _ensure_column(conn, "article_summaries", "importance_score", "INTEGER")
        _ensure_column(conn, "article_summaries", "source_name", "TEXT")
        _ensure_column(conn, "article_summaries", "original_url", "TEXT")
        _ensure_column(conn, "published_items", "telegram_chat_id", "TEXT")
        _ensure_column(conn, "published_items", "facebook_page_id", "TEXT")
        _ensure_column(conn, "published_items", "facebook_post_id", "TEXT")


def _ensure_column(conn, table, column, column_type):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {row["name"] for row in rows}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def sync_sources(rows, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    now = utc_now()
    with connect_db(db_path) as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO sources (
                    id, name, website, country, language, type, category, priority,
                    rss, api, crawl_method, frequency, audience,
                    content_quality_score, business_value_score, crawl_difficulty,
                    copyright_risk, ai_summary_enabled, status, raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    website=excluded.website,
                    country=excluded.country,
                    language=excluded.language,
                    type=excluded.type,
                    category=excluded.category,
                    priority=excluded.priority,
                    rss=excluded.rss,
                    api=excluded.api,
                    crawl_method=excluded.crawl_method,
                    frequency=excluded.frequency,
                    audience=excluded.audience,
                    content_quality_score=excluded.content_quality_score,
                    business_value_score=excluded.business_value_score,
                    crawl_difficulty=excluded.crawl_difficulty,
                    copyright_risk=excluded.copyright_risk,
                    ai_summary_enabled=excluded.ai_summary_enabled,
                    status=excluded.status,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    row["ID"],
                    row["Source Name"],
                    row["Website"],
                    row["Country"],
                    row["Language"],
                    row["Type"],
                    row["Category"],
                    row["Priority"],
                    row["RSS"],
                    row["API"],
                    row["Crawl Method"],
                    row["Frequency"],
                    row["Audience"],
                    int(row["Content Quality Score"]),
                    int(row["Business Value Score"]),
                    row["Crawl Difficulty"],
                    row["Copyright Risk"],
                    row["AI Summary Enabled"],
                    row["Status"],
                    json.dumps(row, ensure_ascii=False),
                    now,
                ),
            )
    return count_rows("sources", db_path=db_path)


def count_rows(table, db_path=DEFAULT_DB_PATH):
    with connect_db(db_path) as conn:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"])


def list_active_sources(db_path=DEFAULT_DB_PATH, priority="P1", include_partial=True):
    init_db(db_path)
    rss_values = ("Yes", "Partial") if include_partial else ("Yes",)
    placeholders = ",".join("?" for _ in rss_values)
    with connect_db(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM sources
            WHERE status = 'Active'
              AND priority = ?
              AND rss IN ({placeholders})
            ORDER BY id
            """,
            (priority, *rss_values),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_article(article, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM articles WHERE url = ?",
            (article["url"],),
        ).fetchone()
        if existing:
            return int(existing["id"]), False

        duplicate_title = conn.execute(
            "SELECT id FROM articles WHERE title_hash = ?",
            (article["title_hash"],),
        ).fetchone()
        status = "duplicate_title" if duplicate_title else "new"

        cursor = conn.execute(
            """
            INSERT INTO articles (
                source_id, source_name, title, url, normalized_title, title_hash,
                published_at, fetched_at, language, category, description,
                content_excerpt, importance_score, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article["source_id"],
                article["source_name"],
                article["title"],
                article["url"],
                article["normalized_title"],
                article["title_hash"],
                article.get("published_at"),
                article["fetched_at"],
                article.get("language"),
                article.get("category"),
                article.get("description"),
                article.get("content_excerpt"),
                article.get("importance_score"),
                status,
            ),
        )
        return int(cursor.lastrowid), True


def update_article_score(article_id, score, db_path=DEFAULT_DB_PATH, hotness_score=None, hot_keywords=None, why_hot=None):
    with connect_db(db_path) as conn:
        conn.execute(
            """
            UPDATE articles
            SET importance_score = ?,
                hotness_score = ?,
                hot_keywords = ?,
                why_hot = ?
            WHERE id = ?
            """,
            (
                score,
                hotness_score if hotness_score is not None else score,
                json.dumps(hot_keywords or [], ensure_ascii=False),
                why_hot,
                article_id,
            ),
        )


def get_articles_for_scoring(db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.*, s.priority, s.content_quality_score, s.business_value_score,
                   s.copyright_risk, s.ai_summary_enabled, s.country
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.status IN ('new', 'duplicate_title')
            ORDER BY a.fetched_at DESC, a.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_articles_for_summary(db_path=DEFAULT_DB_PATH, min_score=6, force=False, limit=None):
    init_db(db_path)
    summary_filter = "" if force else "AND sm.article_id IS NULL"
    limit_clause = "LIMIT ?" if limit else ""
    params = [min_score]
    if limit:
        params.append(limit)
    with connect_db(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT a.*
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            LEFT JOIN article_summaries sm ON sm.article_id = a.id
            WHERE 1 = 1
              {summary_filter}
              AND a.status = 'new'
              AND COALESCE(a.hotness_score, a.importance_score, 0) >= ?
              AND s.ai_summary_enabled = 'Yes'
            ORDER BY COALESCE(a.hotness_score, a.importance_score, 0) DESC,
                     COALESCE(a.published_at, '') DESC
            {limit_clause}
            """,
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def get_article_for_summary_by_id(article_id, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT a.*, s.priority, s.content_quality_score, s.business_value_score,
                   s.copyright_risk, s.ai_summary_enabled, s.country
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.id = ?
            """,
            (article_id,),
        ).fetchone()
    return dict(row) if row else None


def upsert_summary(summary, db_path=DEFAULT_DB_PATH):
    now = utc_now()
    with connect_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO article_summaries (
                article_id, headline, summary, impact_note, category,
                importance_score, source_name, original_url, prompt_version,
                model_name, token_usage, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                headline=excluded.headline,
                summary=excluded.summary,
                impact_note=excluded.impact_note,
                category=excluded.category,
                importance_score=excluded.importance_score,
                source_name=excluded.source_name,
                original_url=excluded.original_url,
                prompt_version=excluded.prompt_version,
                model_name=excluded.model_name,
                token_usage=excluded.token_usage,
                created_at=excluded.created_at
            """,
            (
                summary["article_id"],
                summary.get("headline"),
                summary["summary"],
                summary["impact_note"],
                summary.get("category"),
                summary.get("importance_score"),
                summary.get("source_name"),
                summary.get("original_url"),
                summary["prompt_version"],
                summary["model_name"],
                summary["token_usage"],
                now,
            ),
        )


def get_brief_candidates(db_path=DEFAULT_DB_PATH, limit=20, brief_type="morning"):
    init_db(db_path)
    cutoff = _brief_cutoff(brief_type)
    effective_limit = int(limit or 1000000)
    with connect_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.*, sm.summary, sm.impact_note, s.country
                   , sm.headline AS ai_headline
                   , sm.category AS ai_category
                   , sm.importance_score AS ai_importance_score
                   , sm.source_name AS ai_source_name
                   , sm.original_url AS ai_original_url
            FROM articles a
            JOIN article_summaries sm ON sm.article_id = a.id
            JOIN sources s ON s.id = a.source_id
            WHERE a.status = 'new'
              AND a.published_at IS NOT NULL
              AND datetime(a.published_at) >= datetime(?)
            ORDER BY COALESCE(a.hotness_score, a.importance_score, 0) DESC,
                     CASE WHEN s.country = 'Vietnam' THEN 1 ELSE 0 END,
                     datetime(a.published_at) DESC
            LIMIT ?
            """,
            (cutoff, effective_limit * 3),
        ).fetchall()
    return _balance_brief_candidates([dict(row) for row in rows], effective_limit)


def get_brief_candidate_diagnostics(db_path=DEFAULT_DB_PATH, brief_type="morning"):
    init_db(db_path)
    cutoff = _brief_cutoff(brief_type)
    with connect_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM articles) AS articles_total,
                (SELECT COUNT(*) FROM article_summaries) AS summaries_total,
                (
                    SELECT COUNT(*)
                    FROM articles a
                    JOIN article_summaries sm ON sm.article_id = a.id
                ) AS summarized_articles_total,
                (
                    SELECT COUNT(*)
                    FROM articles a
                    JOIN article_summaries sm ON sm.article_id = a.id
                    WHERE a.status = 'new'
                ) AS summarized_new_total,
                (
                    SELECT COUNT(*)
                    FROM articles a
                    JOIN article_summaries sm ON sm.article_id = a.id
                    WHERE a.status = 'new'
                      AND a.published_at IS NOT NULL
                ) AS summarized_with_published_at_total,
                (
                    SELECT COUNT(*)
                    FROM articles a
                    JOIN article_summaries sm ON sm.article_id = a.id
                    JOIN sources s ON s.id = a.source_id
                    WHERE a.status = 'new'
                      AND a.published_at IS NOT NULL
                      AND datetime(a.published_at) >= datetime(?)
                ) AS candidate_window_total,
                (SELECT COUNT(*) FROM published_items) AS published_items_total
            """,
            (cutoff,),
        ).fetchone()
    result = dict(row)
    result["db_path"] = str(Path(db_path))
    result["brief_type"] = brief_type
    result["cutoff"] = cutoff
    return result


def list_published_item_keys(db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT item_key, canonical_url, title_hash
            FROM published_items
            """
        ).fetchall()
    return [dict(row) for row in rows]


def mark_items_published(items, telegram_chat_id="", facebook_page_id="", facebook_post_id="", db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    now = utc_now()
    saved = 0
    with connect_db(db_path) as conn:
        for item in items or []:
            item_key = item.get("item_key") or item.get("dedupe_key")
            if not item_key:
                continue
            conn.execute(
                """
                INSERT INTO published_items (
                    item_key, canonical_url, title_hash, title, source_name,
                    source_type, published_at, sent_at, telegram_chat_id,
                    facebook_page_id, facebook_post_id, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_key) DO UPDATE SET
                    sent_at=excluded.sent_at,
                    telegram_chat_id=CASE
                        WHEN excluded.telegram_chat_id != '' THEN excluded.telegram_chat_id
                        ELSE published_items.telegram_chat_id
                    END,
                    facebook_page_id=CASE
                        WHEN excluded.facebook_page_id != '' THEN excluded.facebook_page_id
                        ELSE published_items.facebook_page_id
                    END,
                    facebook_post_id=CASE
                        WHEN excluded.facebook_post_id != '' THEN excluded.facebook_post_id
                        ELSE published_items.facebook_post_id
                    END,
                    payload_json=excluded.payload_json
                """,
                (
                    item_key,
                    item.get("canonical_url"),
                    item.get("title_hash"),
                    item.get("title"),
                    item.get("source_name"),
                    item.get("source_type"),
                    item.get("published_at"),
                    now,
                    str(telegram_chat_id or ""),
                    str(facebook_page_id or ""),
                    str(facebook_post_id or ""),
                    json.dumps(item, ensure_ascii=False),
                ),
            )
            saved += 1
    return saved


def upsert_trend_keyword(keyword, category, timeframe, source, search_volume=None, started_at=None, status=None, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO trend_keywords (
                keyword, category, timeframe, search_volume, started_at,
                status, source, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(keyword, timeframe, source) DO UPDATE SET
                category=excluded.category,
                search_volume=excluded.search_volume,
                started_at=excluded.started_at,
                status=excluded.status,
                fetched_at=excluded.fetched_at
            """,
            (
                keyword,
                category,
                timeframe,
                search_volume,
                started_at,
                status,
                source,
                utc_now(),
            ),
        )


def list_trend_keywords(db_path=DEFAULT_DB_PATH, timeframes=None):
    init_db(db_path)
    timeframes = timeframes or ["24h", "48h", "7d", "seed"]
    placeholders = ",".join("?" for _ in timeframes)
    with connect_db(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM trend_keywords
            WHERE timeframe IN ({placeholders})
            ORDER BY COALESCE(search_volume, 0) DESC, fetched_at DESC
            """,
            tuple(timeframes),
        ).fetchall()
    return [dict(row) for row in rows]


def insert_brief(brief_type, title, markdown, payload, db_path=DEFAULT_DB_PATH):
    with connect_db(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO briefs (brief_type, title, markdown, json_payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                brief_type,
                title,
                markdown,
                json.dumps(payload, ensure_ascii=False, indent=2),
                utc_now(),
            ),
        )
        return int(cursor.lastrowid)


def log_fetch(source, stage, status, url=None, message=None, fetched_count=0, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO fetch_logs (
                source_id, source_name, stage, status, url, message, fetched_count, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.get("id") or source.get("ID"),
                source.get("name") or source.get("Source Name"),
                stage,
                status,
                url,
                message,
                int(fetched_count or 0),
                utc_now(),
            ),
        )


def _brief_cutoff(brief_type):
    days = 7 if brief_type == "weekly" else 2
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def _balance_brief_candidates(rows, limit):
    international = [row for row in rows if row.get("country") != "Vietnam"]
    vietnam = [row for row in rows if row.get("country") == "Vietnam"]
    minimum_international = min(len(international), max(1, round(limit * 0.65)))
    selected = international[:minimum_international]

    for row in rows:
        if len(selected) >= limit:
            break
        if row not in selected:
            selected.append(row)

    if not selected and vietnam:
        selected = vietnam[:limit]
    return selected[:limit]
