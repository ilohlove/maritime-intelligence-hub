import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import ROOT_DIR


DEFAULT_DB_PATH = ROOT_DIR / "data" / "mih.db"
SQLITE_BUSY_TIMEOUT_MS = 5000
DEFAULT_LEASE_SECONDS = 300
RUN_STATES = {
    "WAIT_PRIMARY",
    "WAIT_SHEET",
    "SHEET_STABILIZING",
    "PRIMARY_SELECTED",
    "BACKUP_SELECTED",
    "RENDERING",
    "PUBLISHING",
    "SUCCEEDED",
    "NO_NEW_CONTENT",
    "FAILED",
}
TERMINAL_RUN_STATES = {"SUCCEEDED", "NO_NEW_CONTENT", "FAILED"}
RUN_LANES = {"primary", "backup"}
DELIVERY_STATUSES = {"sending", "succeeded", "failed", "needs_review"}
FACEBOOK_GROUP_QUOTA_STATUSES = {"reserved", "published", "pending", "failed"}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_persisted_error(value):
    text = str(value or "")
    text = re.sub(r"(?i)([?&](?:key|api[_-]?key|token|access_token)=)[^&\s]+", r"\1***", text)
    text = re.sub(r"(?i)((?:x-goog-api-key|authorization|api[_-]?key)\s*[:=]\s*)[^\s,;]+", r"\1***", text)
    for env_key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        secret = os.getenv(env_key, "").strip()
        if secret:
            text = text.replace(secret, "***")
    return " ".join(text.split())[:1000]


@contextmanager
def connect_db(db_path=DEFAULT_DB_PATH):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        deadline = time.monotonic() + (SQLITE_BUSY_TIMEOUT_MS / 1000)
        while True:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        conn.execute("PRAGMA foreign_keys = ON")
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
                source_type TEXT,
                provider TEXT,
                reader_provider TEXT,
                reader_status TEXT,
                reader_error TEXT,
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
                ai_provider TEXT,
                image_prompt TEXT,
                fallback_errors TEXT,
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

            CREATE TABLE IF NOT EXISTS facebook_group_deliveries (
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
                scheduled_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                stop_reason TEXT,
                expires_at TEXT,
                priority INTEGER NOT NULL DEFAULT 100,
                owner TEXT,
                lease_expires_at TEXT,
                quota_reservation_token TEXT,
                payload_json TEXT NOT NULL,
                UNIQUE(batch_id, group_id)
            );

            CREATE INDEX IF NOT EXISTS idx_facebook_group_deliveries_status
            ON facebook_group_deliveries(status);

            CREATE TABLE IF NOT EXISTS facebook_group_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                stop_reason TEXT,
                reservation_token TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_facebook_group_attempts_time
            ON facebook_group_attempts(attempted_at);

            CREATE TABLE IF NOT EXISTS news_runs (
                run_id TEXT PRIMARY KEY,
                lane TEXT,
                state TEXT NOT NULL,
                owner TEXT,
                deadline TEXT NOT NULL,
                lease_expires_at TEXT,
                heartbeat_at TEXT,
                error_code TEXT,
                stats_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_news_runs_state
            ON news_runs(state);

            CREATE INDEX IF NOT EXISTS idx_news_runs_lease
            ON news_runs(lease_expires_at);

            CREATE TABLE IF NOT EXISTS publish_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                channel TEXT NOT NULL,
                destination TEXT NOT NULL,
                status TEXT NOT NULL,
                owner TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                retryable INTEGER NOT NULL DEFAULT 1,
                lease_expires_at TEXT,
                claimed_at TEXT,
                completed_at TEXT,
                error_message TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(run_id, item_key, channel, destination)
            );

            CREATE INDEX IF NOT EXISTS idx_publish_deliveries_run
            ON publish_deliveries(run_id, status);

            CREATE INDEX IF NOT EXISTS idx_publish_deliveries_lease
            ON publish_deliveries(status, lease_expires_at);
            """
        )
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        _ensure_column(conn, "articles", "hotness_score", "INTEGER")
        _ensure_column(conn, "articles", "hot_keywords", "TEXT")
        _ensure_column(conn, "articles", "why_hot", "TEXT")
        _ensure_column(conn, "articles", "source_type", "TEXT")
        _ensure_column(conn, "articles", "provider", "TEXT")
        _ensure_column(conn, "articles", "reader_provider", "TEXT")
        _ensure_column(conn, "articles", "reader_status", "TEXT")
        _ensure_column(conn, "articles", "reader_error", "TEXT")
        _ensure_column(conn, "article_summaries", "headline", "TEXT")
        _ensure_column(conn, "article_summaries", "category", "TEXT")
        _ensure_column(conn, "article_summaries", "importance_score", "INTEGER")
        _ensure_column(conn, "article_summaries", "source_name", "TEXT")
        _ensure_column(conn, "article_summaries", "original_url", "TEXT")
        _ensure_column(conn, "article_summaries", "ai_provider", "TEXT")
        _ensure_column(conn, "article_summaries", "image_prompt", "TEXT")
        _ensure_column(conn, "article_summaries", "fallback_errors", "TEXT")
        _ensure_column(conn, "published_items", "telegram_chat_id", "TEXT")
        _ensure_column(conn, "published_items", "facebook_page_id", "TEXT")
        _ensure_column(conn, "published_items", "facebook_post_id", "TEXT")
        _ensure_column(conn, "facebook_group_deliveries", "scheduled_at", "TEXT")
        _ensure_column(conn, "facebook_group_deliveries", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "facebook_group_deliveries", "stop_reason", "TEXT")
        _ensure_column(conn, "facebook_group_deliveries", "expires_at", "TEXT")
        _ensure_column(conn, "facebook_group_deliveries", "priority", "INTEGER NOT NULL DEFAULT 100")
        _ensure_column(conn, "facebook_group_deliveries", "owner", "TEXT")
        _ensure_column(conn, "facebook_group_deliveries", "lease_expires_at", "TEXT")
        _ensure_column(conn, "facebook_group_deliveries", "quota_reservation_token", "TEXT")
        _ensure_column(conn, "facebook_group_attempts", "reservation_token", "TEXT")
        _scrub_sensitive_records(conn)
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_facebook_group_attempts_reservation
            ON facebook_group_attempts(reservation_token)
            WHERE reservation_token IS NOT NULL
            """
        )
        conn.execute(
            """
            UPDATE facebook_group_deliveries
            SET expires_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', attempted_at, '+12 hours')
            WHERE status IN ('queued', 'failed', 'needs_login') AND expires_at IS NULL
            """
        )
        conn.execute(
            """
            INSERT INTO facebook_group_attempts (batch_id, group_id, status, attempted_at, stop_reason)
            SELECT d.batch_id, d.group_id, d.status, d.attempted_at, d.stop_reason
            FROM facebook_group_deliveries AS d
            WHERE d.status IN ('published', 'pending', 'failed')
              AND NOT EXISTS (
                  SELECT 1 FROM facebook_group_attempts AS a
                  WHERE a.batch_id = d.batch_id
                    AND a.group_id = d.group_id
                    AND a.attempted_at = d.attempted_at
              )
            """
        )


def _ensure_column(conn, table, column, column_type):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {row["name"] for row in rows}
    if column not in existing:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        except sqlite3.OperationalError:
            refreshed = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in refreshed:
                raise


def _scrub_sensitive_records(conn):
    for table, key_column, value_column in (
        ("article_summaries", "article_id", "fallback_errors"),
        ("fetch_logs", "id", "message"),
    ):
        rows = conn.execute(
            f"SELECT {key_column}, {value_column} FROM {table} WHERE {value_column} IS NOT NULL"
        ).fetchall()
        for row in rows:
            original = row[value_column]
            sanitized = sanitize_persisted_error(original)
            if sanitized != original:
                conn.execute(
                    f"UPDATE {table} SET {value_column} = ? WHERE {key_column} = ?",
                    (sanitized, row[key_column]),
                )


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
                content_excerpt, importance_score, source_type, provider,
                reader_provider, reader_status, reader_error, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                article.get("source_type"),
                article.get("provider"),
                article.get("reader_provider"),
                article.get("reader_status"),
                article.get("reader_error"),
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
                model_name, token_usage, ai_provider, image_prompt, fallback_errors, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ai_provider=excluded.ai_provider,
                image_prompt=excluded.image_prompt,
                fallback_errors=excluded.fallback_errors,
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
                summary.get("ai_provider"),
                summary.get("image_prompt"),
                json.dumps(
                    [sanitize_persisted_error(error) for error in (summary.get("fallback_errors") or [])],
                    ensure_ascii=False,
                ),
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


def get_facebook_group_delivery(batch_id, group_id, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, batch_id, group_id, group_name, group_url, status,
                   attempted_at, completed_at, post_url, error_message,
                   scheduled_at, attempt_count, stop_reason, expires_at,
                   priority, owner, lease_expires_at, quota_reservation_token,
                   payload_json
            FROM facebook_group_deliveries
            WHERE batch_id = ? AND group_id = ?
            """,
            (str(batch_id), str(group_id)),
        ).fetchone()
    return dict(row) if row else None


def record_facebook_group_delivery(
    batch_id,
    group,
    status,
    post_url="",
    error_message="",
    scheduled_at=None,
    stop_reason="",
    expires_at=None,
    payload=None,
    db_path=DEFAULT_DB_PATH,
):
    init_db(db_path)
    now = utc_now()
    group = dict(group or {})
    status = str(status or "failed").strip().lower()
    completed_at = now if status in {"published", "pending"} else None
    safe_error = str(error_message or "").strip()[:1000]
    safe_stop_reason = str(stop_reason or "").strip()[:500]
    attempt_increment = 0 if status == "queued" else 1
    safe_payload = dict(payload or {})
    safe_payload.pop("cookies", None)
    safe_payload.pop("storage_state", None)
    with connect_db(db_path) as conn:
        current = conn.execute(
            """
            SELECT quota_reservation_token
            FROM facebook_group_deliveries
            WHERE batch_id = ? AND group_id = ?
            """,
            (str(batch_id), str(group.get("id") or "")),
        ).fetchone()
        reservation_token = str(current["quota_reservation_token"] or "") if current else ""
        delivery_cursor = conn.execute(
            """
            INSERT INTO facebook_group_deliveries (
                batch_id, group_id, group_name, group_url, status, attempted_at,
                completed_at, post_url, error_message, scheduled_at,
                attempt_count, stop_reason, expires_at, priority, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(batch_id, group_id) DO UPDATE SET
                group_name=excluded.group_name,
                group_url=excluded.group_url,
                status=excluded.status,
                attempted_at=excluded.attempted_at,
                completed_at=excluded.completed_at,
                post_url=excluded.post_url,
                error_message=excluded.error_message,
                scheduled_at=excluded.scheduled_at,
                attempt_count=facebook_group_deliveries.attempt_count +
                    CASE WHEN excluded.status != 'queued' THEN 1 ELSE 0 END,
                stop_reason=excluded.stop_reason,
                expires_at=CASE
                    WHEN excluded.status IN ('queued', 'failed', 'needs_login')
                        THEN COALESCE(excluded.expires_at, facebook_group_deliveries.expires_at)
                    ELSE NULL
                END,
                priority=excluded.priority,
                owner=NULL,
                lease_expires_at=NULL,
                quota_reservation_token=NULL,
                payload_json=excluded.payload_json
            WHERE NOT (
                excluded.status = 'queued'
                AND facebook_group_deliveries.status IN ('sending', 'published', 'pending', 'needs_review')
            )
            """,
            (
                str(batch_id),
                str(group.get("id") or ""),
                str(group.get("name") or ""),
                str(group.get("url") or ""),
                status,
                now,
                completed_at,
                str(post_url or ""),
                safe_error,
                str(scheduled_at or "") or None,
                attempt_increment,
                safe_stop_reason,
                str(expires_at or "") or None,
                max(1, int(group.get("priority") or 100)),
                json.dumps(safe_payload, ensure_ascii=False),
            ),
        )
        if reservation_token and delivery_cursor.rowcount == 1:
            if status in {"published", "pending", "failed"}:
                conn.execute(
                    """
                    UPDATE facebook_group_attempts
                    SET status = ?, attempted_at = ?, stop_reason = ?
                    WHERE reservation_token = ? AND status = 'reserved'
                    """,
                    (status, now, safe_stop_reason, reservation_token),
                )
            else:
                conn.execute(
                    "DELETE FROM facebook_group_attempts WHERE reservation_token = ? AND status = 'reserved'",
                    (reservation_token,),
                )
        elif not reservation_token and delivery_cursor.rowcount == 1 and status in {"published", "pending", "failed"}:
            conn.execute(
                """
                INSERT INTO facebook_group_attempts (
                    batch_id, group_id, status, attempted_at, stop_reason
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(batch_id),
                    str(group.get("id") or ""),
                    status,
                    now,
                    safe_stop_reason,
                ),
            )
    return get_facebook_group_delivery(batch_id, group.get("id"), db_path=db_path)


def list_facebook_group_deliveries(batch_id=None, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    query = """
        SELECT id, batch_id, group_id, group_name, group_url, status,
               attempted_at, completed_at, post_url, error_message,
               scheduled_at, attempt_count, stop_reason, expires_at,
               priority, owner, lease_expires_at, quota_reservation_token,
               payload_json
        FROM facebook_group_deliveries
    """
    params = ()
    if batch_id is not None:
        query += " WHERE batch_id = ?"
        params = (str(batch_id),)
    query += " ORDER BY attempted_at DESC, id DESC"
    with connect_db(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_facebook_group_delivery_by_id(delivery_id, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, batch_id, group_id, group_name, group_url, status,
                   attempted_at, completed_at, post_url, error_message,
                   scheduled_at, attempt_count, stop_reason, expires_at,
                   priority, owner, lease_expires_at, quota_reservation_token,
                   payload_json
            FROM facebook_group_deliveries
            WHERE id = ?
            """,
            (int(delivery_id),),
        ).fetchone()
    return dict(row) if row else None


def cancel_facebook_group_delivery(delivery_id, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT quota_reservation_token FROM facebook_group_deliveries WHERE id = ?",
            (int(delivery_id),),
        ).fetchone()
        reservation_token = str(row["quota_reservation_token"] or "") if row else ""
        cursor = conn.execute(
            """
            UPDATE facebook_group_deliveries
            SET status = 'cancelled', scheduled_at = NULL, owner = NULL,
                lease_expires_at = NULL, quota_reservation_token = NULL,
                stop_reason = 'Cancelled after operator review'
            WHERE id = ? AND status IN ('queued', 'failed', 'needs_login', 'needs_review')
            """,
            (int(delivery_id),),
        )
        if cursor.rowcount == 1 and reservation_token:
            conn.execute(
                """
                UPDATE facebook_group_attempts
                SET status = 'cancelled', stop_reason = 'confirmed_no_post'
                WHERE reservation_token = ? AND status = 'reserved'
                """,
                (reservation_token,),
            )
    return cursor.rowcount > 0


def expire_facebook_group_deliveries(now_iso, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        queue_cursor = conn.execute(
            """
            UPDATE facebook_group_deliveries
            SET status = 'expired', scheduled_at = NULL, stop_reason = 'Queue item expired'
            WHERE status IN ('queued', 'failed', 'needs_login')
              AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (str(now_iso),),
        )
        sending_cursor = conn.execute(
            """
            UPDATE facebook_group_deliveries
            SET status = 'needs_review', owner = NULL, lease_expires_at = NULL,
                scheduled_at = NULL,
                stop_reason = 'Worker lease expired; verify the remote group before retrying'
            WHERE status = 'sending'
              AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
            """,
            (str(now_iso),),
        )
    return queue_cursor.rowcount + sending_cursor.rowcount


def claim_facebook_group_delivery(
    delivery_id,
    owner,
    db_path=DEFAULT_DB_PATH,
    *,
    lease_seconds=DEFAULT_LEASE_SECONDS,
    daily_since_iso=None,
    daily_limit=None,
    now=None,
):
    """Atomically claim a queued group post before any browser side effect."""
    safe_owner = _required_text(owner, "owner")
    now_dt = _utc_datetime(now)
    now_iso = _utc_iso(now_dt)
    lease_iso = _lease_iso(now_dt, lease_seconds)
    init_db(db_path)
    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM facebook_group_deliveries WHERE id = ?",
            (int(delivery_id),),
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        if record.get("status") == "sending":
            if _timestamp_expired(record.get("lease_expires_at"), now_dt):
                conn.execute(
                    """
                    UPDATE facebook_group_deliveries
                    SET status = 'needs_review', owner = NULL, lease_expires_at = NULL,
                        scheduled_at = NULL,
                        stop_reason = 'Worker lease expired; verify the remote group before retrying'
                    WHERE id = ? AND status = 'sending'
                    """,
                    (int(delivery_id),),
                )
                record = dict(conn.execute(
                    "SELECT * FROM facebook_group_deliveries WHERE id = ?", (int(delivery_id),)
                ).fetchone())
                return {**record, "acquired": False, "claim_reason": "needs_review"}
            return {**record, "acquired": False, "claim_reason": "sending"}
        if record.get("status") not in {"queued", "failed", "needs_login"}:
            return {**record, "acquired": False, "claim_reason": record.get("status") or "unavailable"}
        reservation_token = None
        if daily_limit is not None:
            if daily_since_iso is None:
                raise ValueError("daily_since_iso is required when daily_limit is set")
            safe_limit = max(1, int(daily_limit))
            placeholders = ",".join("?" for _ in FACEBOOK_GROUP_QUOTA_STATUSES)
            used = conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM facebook_group_attempts
                WHERE attempted_at >= ? AND status IN ({placeholders})
                """,
                (str(daily_since_iso), *sorted(FACEBOOK_GROUP_QUOTA_STATUSES)),
            ).fetchone()["total"]
            if int(used or 0) >= safe_limit:
                return {**record, "acquired": False, "claim_reason": "daily_quota_exhausted"}
            reservation_token = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO facebook_group_attempts (
                    batch_id, group_id, status, attempted_at, stop_reason, reservation_token
                ) VALUES (?, ?, 'reserved', ?, '', ?)
                """,
                (
                    str(record.get("batch_id") or ""),
                    str(record.get("group_id") or ""),
                    now_iso,
                    reservation_token,
                ),
            )
        cursor = conn.execute(
            """
            UPDATE facebook_group_deliveries
            SET status = 'sending', owner = ?, lease_expires_at = ?,
                attempted_at = ?, stop_reason = '', quota_reservation_token = ?
            WHERE id = ? AND status IN ('queued', 'failed', 'needs_login')
            """,
            (safe_owner, lease_iso, now_iso, reservation_token, int(delivery_id)),
        )
        if cursor.rowcount != 1:
            if reservation_token:
                conn.execute(
                    "DELETE FROM facebook_group_attempts WHERE reservation_token = ?",
                    (reservation_token,),
                )
            row = conn.execute(
                "SELECT * FROM facebook_group_deliveries WHERE id = ?", (int(delivery_id),)
            ).fetchone()
            return {**dict(row), "acquired": False, "claim_reason": "claim_lost"}
        claimed = dict(conn.execute(
            "SELECT * FROM facebook_group_deliveries WHERE id = ?", (int(delivery_id),)
        ).fetchone())
        return {**claimed, "acquired": True, "claim_reason": "claimed"}


def release_facebook_group_delivery_claim(
    delivery_id,
    owner,
    reason,
    db_path=DEFAULT_DB_PATH,
):
    """Return an unstarted browser delivery to its queue after a guard blocks it."""
    init_db(db_path)
    with connect_db(db_path) as conn:
        row = conn.execute(
            "SELECT quota_reservation_token FROM facebook_group_deliveries WHERE id = ?",
            (int(delivery_id),),
        ).fetchone()
        reservation_token = str(row["quota_reservation_token"] or "") if row else ""
        cursor = conn.execute(
            """
            UPDATE facebook_group_deliveries
            SET status = 'queued', owner = NULL, lease_expires_at = NULL,
                quota_reservation_token = NULL, stop_reason = ?
            WHERE id = ? AND status = 'sending' AND owner = ?
            """,
            (_clean_error(reason, 500), int(delivery_id), str(owner)),
        )
        if cursor.rowcount == 1 and reservation_token:
            conn.execute(
                "DELETE FROM facebook_group_attempts WHERE reservation_token = ? AND status = 'reserved'",
                (reservation_token,),
            )
    return cursor.rowcount == 1


def confirm_facebook_group_delivery_claim_succeeded(
    delivery_id,
    owner,
    db_path=DEFAULT_DB_PATH,
    *,
    now=None,
):
    """Close a queue claim when the per-item delivery ledger already proves success."""
    now_iso = _utc_iso(now)
    init_db(db_path)
    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT quota_reservation_token FROM facebook_group_deliveries WHERE id = ?",
            (int(delivery_id),),
        ).fetchone()
        reservation_token = str(row["quota_reservation_token"] or "") if row else ""
        cursor = conn.execute(
            """
            UPDATE facebook_group_deliveries
            SET status = 'published', completed_at = ?, scheduled_at = NULL,
                owner = NULL, lease_expires_at = NULL,
                quota_reservation_token = NULL,
                stop_reason = 'Confirmed by publish delivery ledger'
            WHERE id = ? AND status = 'sending' AND owner = ?
            """,
            (now_iso, int(delivery_id), str(owner)),
        )
        if cursor.rowcount == 1 and reservation_token:
            conn.execute(
                "DELETE FROM facebook_group_attempts WHERE reservation_token = ? AND status = 'reserved'",
                (reservation_token,),
            )
    return cursor.rowcount == 1


def get_facebook_group_last_delivery_times(db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT group_id, MAX(completed_at) AS last_delivered_at
            FROM facebook_group_deliveries
            WHERE status IN ('published', 'pending')
            GROUP BY group_id
            """
        ).fetchall()
    return {str(row["group_id"]): row["last_delivered_at"] for row in rows}


def count_facebook_group_attempts_since(since_iso, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        placeholders = ",".join("?" for _ in FACEBOOK_GROUP_QUOTA_STATUSES)
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM facebook_group_attempts
            WHERE attempted_at >= ? AND status IN ({placeholders})
            """,
            (str(since_iso), *sorted(FACEBOOK_GROUP_QUOTA_STATUSES)),
        ).fetchone()
    return int(row["total"] or 0)


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


def claim_news_run(
    run_id,
    owner,
    deadline,
    db_path=DEFAULT_DB_PATH,
    *,
    lease_seconds=DEFAULT_LEASE_SECONDS,
    initial_state="WAIT_SHEET",
    now=None,
):
    """Atomically create or acquire a non-terminal scheduled news run.

    The returned record always includes ``acquired`` and ``claim_reason``. An
    active lease held by another process and a terminal run both return the
    existing record with ``acquired=False`` rather than hiding why the claim
    was rejected.
    """
    safe_run_id = _required_text(run_id, "run_id")
    safe_owner = _required_text(owner, "owner")
    safe_state = _run_state(initial_state)
    deadline_iso = _utc_iso(deadline)
    now_dt = _utc_datetime(now)
    now_iso = _utc_iso(now_dt)
    lease_iso = _lease_iso(now_dt, lease_seconds)
    init_db(db_path)

    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _select_news_run(conn, safe_run_id)
        if row is None:
            conn.execute(
                """
                INSERT INTO news_runs (
                    run_id, lane, state, owner, deadline, lease_expires_at,
                    heartbeat_at, error_code, stats_json, created_at,
                    updated_at, completed_at
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, NULL, '{}', ?, ?, NULL)
                """,
                (
                    safe_run_id,
                    safe_state,
                    safe_owner,
                    deadline_iso,
                    lease_iso,
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )
            row = _select_news_run(conn, safe_run_id)
            return _run_claim_result(row, True, "created")

        record = _news_run_record(row)
        if record["state"] in TERMINAL_RUN_STATES:
            return _run_claim_result(row, False, "terminal")

        lease_expired = _timestamp_expired(record.get("lease_expires_at"), now_dt)
        if record.get("owner") not in {None, "", safe_owner} and not lease_expired:
            return _run_claim_result(row, False, "leased")

        reason = "renewed" if record.get("owner") == safe_owner and not lease_expired else "recovered"
        conn.execute(
            """
            UPDATE news_runs
            SET owner = ?, lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (safe_owner, lease_iso, now_iso, now_iso, safe_run_id),
        )
        return _run_claim_result(_select_news_run(conn, safe_run_id), True, reason)


def get_news_run(run_id, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        row = _select_news_run(conn, str(run_id))
    return _news_run_record(row) if row else None


def list_news_runs(db_path=DEFAULT_DB_PATH, state=None, limit=None):
    init_db(db_path)
    query = "SELECT * FROM news_runs"
    params = []
    if state is not None:
        query += " WHERE state = ?"
        params.append(_run_state(state))
    query += " ORDER BY created_at DESC, run_id DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(max(1, int(limit)))
    with connect_db(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_news_run_record(row) for row in rows]


def heartbeat_news_run(
    run_id,
    owner,
    db_path=DEFAULT_DB_PATH,
    *,
    lease_seconds=DEFAULT_LEASE_SECONDS,
    now=None,
):
    """Renew an active lease; an already expired lease cannot be revived."""
    now_dt = _utc_datetime(now)
    now_iso = _utc_iso(now_dt)
    lease_iso = _lease_iso(now_dt, lease_seconds)
    with connect_db(db_path) as conn:
        cursor = conn.execute(
            f"""
            UPDATE news_runs
            SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
            WHERE run_id = ? AND owner = ?
              AND lease_expires_at > ?
              AND state NOT IN ({','.join('?' for _ in TERMINAL_RUN_STATES)})
            """,
            (
                lease_iso,
                now_iso,
                now_iso,
                str(run_id),
                str(owner),
                now_iso,
                *sorted(TERMINAL_RUN_STATES),
            ),
        )
    return cursor.rowcount == 1


def claim_terminal_news_run_lease(
    run_id,
    owner,
    db_path=DEFAULT_DB_PATH,
    *,
    lease_seconds=DEFAULT_LEASE_SECONDS,
    now=None,
):
    """Claim a terminal run exclusively for an operator-approved publish retry."""
    safe_run_id = _required_text(run_id, "run_id")
    safe_owner = _required_text(owner, "owner")
    now_dt = _utc_datetime(now)
    now_iso = _utc_iso(now_dt)
    lease_iso = _lease_iso(now_dt, lease_seconds)
    init_db(db_path)

    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _select_news_run(conn, safe_run_id)
        if row is None:
            return None
        record = _news_run_record(row)
        if record["state"] not in TERMINAL_RUN_STATES:
            return _run_claim_result(row, False, "non_terminal")
        lease_expired = _timestamp_expired(record.get("lease_expires_at"), now_dt)
        if record.get("owner") not in {None, "", safe_owner} and not lease_expired:
            return _run_claim_result(row, False, "leased")
        reason = "renewed" if record.get("owner") == safe_owner and not lease_expired else "retry_claimed"
        conn.execute(
            """
            UPDATE news_runs
            SET owner = ?, lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
            WHERE run_id = ? AND state IN (?, ?, ?)
            """,
            (
                safe_owner,
                lease_iso,
                now_iso,
                now_iso,
                safe_run_id,
                *sorted(TERMINAL_RUN_STATES),
            ),
        )
        return _run_claim_result(_select_news_run(conn, safe_run_id), True, reason)


def heartbeat_terminal_news_run_lease(
    run_id,
    owner,
    db_path=DEFAULT_DB_PATH,
    *,
    lease_seconds=DEFAULT_LEASE_SECONDS,
    now=None,
):
    """Renew an unexpired terminal-run retry lease."""
    now_dt = _utc_datetime(now)
    now_iso = _utc_iso(now_dt)
    lease_iso = _lease_iso(now_dt, lease_seconds)
    with connect_db(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE news_runs
            SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
            WHERE run_id = ? AND owner = ? AND lease_expires_at > ?
              AND state IN (?, ?, ?)
            """,
            (
                lease_iso,
                now_iso,
                now_iso,
                str(run_id),
                str(owner),
                now_iso,
                *sorted(TERMINAL_RUN_STATES),
            ),
        )
    return cursor.rowcount == 1


def release_terminal_news_run_lease(
    run_id,
    owner,
    db_path=DEFAULT_DB_PATH,
    *,
    reconciliation=None,
    now=None,
):
    """Release a publish-retry lease and append its audited reconciliation result."""
    now_iso = _utc_iso(now)
    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _select_news_run(conn, str(run_id))
        if row is None:
            return None
        record = _news_run_record(row)
        if record["state"] not in TERMINAL_RUN_STATES or record.get("owner") != str(owner):
            return None
        stats = dict(record.get("stats") or {})
        if reconciliation is not None:
            entry = _json_object(reconciliation, "reconciliation")
            entry = {**entry, "owner": str(owner), "recorded_at": now_iso}
            history = list(stats.get("publish_reconciliation_history") or [])
            history.append(entry)
            stats["publish_reconciliation"] = entry
            stats["publish_reconciliation_history"] = history[-20:]
        conn.execute(
            """
            UPDATE news_runs
            SET owner = NULL, lease_expires_at = NULL, heartbeat_at = ?,
                stats_json = ?, updated_at = ?
            WHERE run_id = ? AND owner = ? AND state IN (?, ?, ?)
            """,
            (
                now_iso,
                json.dumps(stats, ensure_ascii=False, sort_keys=True),
                now_iso,
                str(run_id),
                str(owner),
                *sorted(TERMINAL_RUN_STATES),
            ),
        )
        return _news_run_record(_select_news_run(conn, str(run_id)))


def select_news_run_lane(
    run_id,
    owner,
    lane,
    state=None,
    db_path=DEFAULT_DB_PATH,
    *,
    lease_seconds=DEFAULT_LEASE_SECONDS,
    now=None,
):
    """Latch ``primary`` or ``backup`` once for the lifetime of a run."""
    safe_lane = str(lane or "").strip().lower()
    if safe_lane not in RUN_LANES:
        raise ValueError(f"lane must be one of {sorted(RUN_LANES)}")
    selected_state = _run_state(
        state or ("PRIMARY_SELECTED" if safe_lane == "primary" else "BACKUP_SELECTED")
    )
    now_dt = _utc_datetime(now)
    now_iso = _utc_iso(now_dt)
    lease_iso = _lease_iso(now_dt, lease_seconds)

    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _select_news_run(conn, str(run_id))
        if row is None:
            return None
        record = _news_run_record(row)
        if record["state"] in TERMINAL_RUN_STATES:
            return _run_lane_result(row, False, "terminal")
        if record.get("owner") != str(owner):
            return _run_lane_result(row, False, "not_owner")
        if _timestamp_expired(record.get("lease_expires_at"), now_dt):
            return _run_lane_result(row, False, "lease_expired")
        if record.get("lane") and record["lane"] != safe_lane:
            return _run_lane_result(row, False, "lane_conflict")
        if record.get("lane") == safe_lane:
            conn.execute(
                """
                UPDATE news_runs
                SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (lease_iso, now_iso, now_iso, str(run_id)),
            )
            return _run_lane_result(
                _select_news_run(conn, str(run_id)), True, "already_selected"
            )

        conn.execute(
            """
            UPDATE news_runs
            SET lane = ?, state = ?, lease_expires_at = ?, heartbeat_at = ?,
                updated_at = ?
            WHERE run_id = ? AND lane IS NULL
            """,
            (safe_lane, selected_state, lease_iso, now_iso, now_iso, str(run_id)),
        )
        return _run_lane_result(_select_news_run(conn, str(run_id)), True, "selected")


def update_news_run_state(
    run_id,
    owner,
    state,
    db_path=DEFAULT_DB_PATH,
    *,
    error=None,
    stats=None,
    release=False,
    lease_seconds=DEFAULT_LEASE_SECONDS,
    now=None,
):
    """Update an owned run and release its lease automatically when terminal."""
    safe_state = _run_state(state)
    now_dt = _utc_datetime(now)
    now_iso = _utc_iso(now_dt)
    lease_iso = _lease_iso(now_dt, lease_seconds)
    stats_update = _json_object(stats, "stats") if stats is not None else None

    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _select_news_run(conn, str(run_id))
        if row is None:
            return None
        current = _news_run_record(row)
        if current.get("owner") != str(owner):
            return None
        if current["state"] in TERMINAL_RUN_STATES:
            return None
        if _timestamp_expired(current.get("lease_expires_at"), now_dt):
            return None

        merged_stats = dict(current.get("stats") or {})
        if stats_update is not None:
            merged_stats.update(stats_update)
        terminal = safe_state in TERMINAL_RUN_STATES
        clear_lease = terminal or bool(release)
        conn.execute(
            """
            UPDATE news_runs
            SET state = ?, owner = ?, lease_expires_at = ?, heartbeat_at = ?,
                error_code = ?, stats_json = ?, updated_at = ?, completed_at = ?
            WHERE run_id = ?
            """,
            (
                safe_state,
                None if clear_lease else str(owner),
                None if clear_lease else lease_iso,
                now_iso,
                _clean_error(error, 500) or None,
                json.dumps(merged_stats, ensure_ascii=False, sort_keys=True),
                now_iso,
                now_iso if terminal else None,
                str(run_id),
            ),
        )
        return _news_run_record(_select_news_run(conn, str(run_id)))


def recover_expired_news_runs(db_path=DEFAULT_DB_PATH, *, now=None):
    """Release expired scheduled or terminal-retry leases without changing state."""
    now_iso = _utc_iso(now)
    init_db(db_path)
    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            f"""
            UPDATE news_runs
            SET owner = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE owner IS NOT NULL
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (now_iso, now_iso),
        )
    return cursor.rowcount


def claim_publish_delivery(
    run_id,
    item_key,
    channel,
    destination,
    owner,
    db_path=DEFAULT_DB_PATH,
    *,
    lease_seconds=DEFAULT_LEASE_SECONDS,
    payload=None,
    now=None,
):
    """Atomically claim one channel/destination delivery before network I/O.

    A stale ``sending`` row becomes ``needs_review`` and is never reclaimed
    automatically because the remote publish may already have succeeded.
    """
    identity = _delivery_identity(run_id, item_key, channel, destination)
    safe_owner = _required_text(owner, "owner")
    payload_json = (
        json.dumps(_json_object(payload, "payload"), ensure_ascii=False, sort_keys=True)
        if payload is not None
        else None
    )
    now_dt = _utc_datetime(now)
    now_iso = _utc_iso(now_dt)
    lease_iso = _lease_iso(now_dt, lease_seconds)
    init_db(db_path)

    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _select_publish_delivery(conn, identity)
        if row is None:
            conn.execute(
                """
                INSERT INTO publish_deliveries (
                    run_id, item_key, channel, destination, status, owner,
                    attempt_count, retryable, lease_expires_at, claimed_at,
                    completed_at, error_message, payload_json, result_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'sending', ?, 1, 1, ?, ?, NULL, NULL, ?, '{}', ?, ?)
                """,
                (*identity, safe_owner, lease_iso, now_iso, payload_json or "{}", now_iso, now_iso),
            )
            return _delivery_claim_result(
                _select_publish_delivery(conn, identity), True, "created"
            )

        record = _publish_delivery_record(row)
        if record["status"] == "sending":
            if _timestamp_expired(record.get("lease_expires_at"), now_dt):
                conn.execute(
                    """
                    UPDATE publish_deliveries
                    SET status = 'needs_review', owner = NULL,
                        lease_expires_at = NULL, completed_at = ?,
                        error_message = 'worker_lease_expired', retryable = 0,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, now_iso, record["id"]),
                )
                return _delivery_claim_result(
                    _select_publish_delivery(conn, identity), False, "needs_review"
                )
            return _delivery_claim_result(row, False, "sending")
        if record["status"] == "succeeded":
            return _delivery_claim_result(row, False, "succeeded")
        if record["status"] == "needs_review":
            return _delivery_claim_result(row, False, "needs_review")
        if record["status"] != "failed" or not record["retryable"]:
            return _delivery_claim_result(row, False, "non_retryable_failure")

        conn.execute(
            """
            UPDATE publish_deliveries
            SET status = 'sending', owner = ?, attempt_count = attempt_count + 1,
                retryable = 1, lease_expires_at = ?, claimed_at = ?,
                completed_at = NULL, error_message = NULL,
                payload_json = COALESCE(?, payload_json), result_json = '{}',
                updated_at = ?
            WHERE id = ?
            """,
            (safe_owner, lease_iso, now_iso, payload_json, now_iso, record["id"]),
        )
        return _delivery_claim_result(
            _select_publish_delivery(conn, identity), True, "retry_claimed"
        )


def get_publish_delivery(
    run_id,
    item_key,
    channel,
    destination,
    db_path=DEFAULT_DB_PATH,
):
    init_db(db_path)
    identity = _delivery_identity(run_id, item_key, channel, destination)
    with connect_db(db_path) as conn:
        row = _select_publish_delivery(conn, identity)
    return _publish_delivery_record(row) if row else None


def list_publish_deliveries(db_path=DEFAULT_DB_PATH, run_id=None, status=None):
    init_db(db_path)
    clauses = []
    params = []
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(str(run_id))
    if status is not None:
        safe_status = str(status).strip().lower()
        if safe_status not in DELIVERY_STATUSES:
            raise ValueError(f"status must be one of {sorted(DELIVERY_STATUSES)}")
        clauses.append("status = ?")
        params.append(safe_status)
    query = "SELECT * FROM publish_deliveries"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC"
    with connect_db(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_publish_delivery_record(row) for row in rows]


def mark_publish_delivery_succeeded(
    run_id,
    item_key,
    channel,
    destination,
    owner,
    db_path=DEFAULT_DB_PATH,
    *,
    result=None,
    now=None,
):
    return _finish_publish_delivery(
        run_id,
        item_key,
        channel,
        destination,
        owner,
        "succeeded",
        db_path,
        result=result,
        retryable=False,
        now=now,
    )


def mark_publish_delivery_failed(
    run_id,
    item_key,
    channel,
    destination,
    owner,
    error_message,
    db_path=DEFAULT_DB_PATH,
    *,
    retryable=True,
    result=None,
    now=None,
):
    return _finish_publish_delivery(
        run_id,
        item_key,
        channel,
        destination,
        owner,
        "failed",
        db_path,
        error_message=error_message,
        result=result,
        retryable=retryable,
        now=now,
    )


def mark_publish_delivery_needs_review(
    run_id,
    item_key,
    channel,
    destination,
    owner,
    reason,
    db_path=DEFAULT_DB_PATH,
    *,
    result=None,
    now=None,
):
    return _finish_publish_delivery(
        run_id,
        item_key,
        channel,
        destination,
        owner,
        "needs_review",
        db_path,
        error_message=reason,
        result=result,
        retryable=False,
        now=now,
    )


def recover_stale_publish_deliveries(db_path=DEFAULT_DB_PATH, *, now=None):
    """Quarantine expired in-flight publishes for manual remote-state review."""
    now_iso = _utc_iso(now)
    init_db(db_path)
    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE publish_deliveries
            SET status = 'needs_review', owner = NULL, lease_expires_at = NULL,
                completed_at = ?, error_message = 'worker_lease_expired',
                retryable = 0, updated_at = ?
            WHERE status = 'sending'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (now_iso, now_iso, now_iso),
        )
    return cursor.rowcount


def resolve_publish_delivery(
    delivery_id,
    resolution,
    reviewer,
    note,
    db_path=DEFAULT_DB_PATH,
    *,
    now=None,
):
    """Audit and resolve a needs_review delivery after checking the remote channel."""
    safe_resolution = str(resolution or "").strip().lower()
    if safe_resolution not in {"succeeded", "retry"}:
        raise ValueError("resolution must be succeeded or retry")
    safe_reviewer = _required_text(reviewer, "reviewer")
    safe_note = _required_text(note, "note")
    now_iso = _utc_iso(now)
    target_status = "succeeded" if safe_resolution == "succeeded" else "failed"
    retryable = 0 if safe_resolution == "succeeded" else 1
    result_json = json.dumps(
        {
            "manual_review": {
                "reviewer": safe_reviewer,
                "note": safe_note,
                "resolution": safe_resolution,
                "resolved_at": now_iso,
            }
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    init_db(db_path)
    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM publish_deliveries WHERE id = ?",
            (int(delivery_id),),
        ).fetchone()
        if row is None or row["status"] != "needs_review":
            return None
        conn.execute(
            """
            UPDATE publish_deliveries
            SET status = ?, owner = NULL, retryable = ?, lease_expires_at = NULL,
                completed_at = ?, error_message = ?, result_json = ?, updated_at = ?
            WHERE id = ? AND status = 'needs_review'
            """,
            (
                target_status,
                retryable,
                now_iso,
                f"manual_review:{safe_resolution}:{safe_note}"[:2000],
                result_json,
                now_iso,
                int(delivery_id),
            ),
        )
        resolved = conn.execute(
            "SELECT * FROM publish_deliveries WHERE id = ?",
            (int(delivery_id),),
        ).fetchone()
    record = _publish_delivery_record(resolved)
    if safe_resolution == "succeeded":
        published_item = dict(record.get("payload") or {})
        published_item["item_key"] = record["item_key"]
        publish_kwargs = {}
        if record["channel"] == "telegram":
            publish_kwargs["telegram_chat_id"] = record["destination"]
        elif record["channel"] == "facebook_page":
            publish_kwargs["facebook_page_id"] = record["destination"]
        mark_items_published([published_item], db_path=db_path, **publish_kwargs)
    return record


def _finish_publish_delivery(
    run_id,
    item_key,
    channel,
    destination,
    owner,
    status,
    db_path,
    *,
    error_message=None,
    result=None,
    retryable=False,
    now=None,
):
    identity = _delivery_identity(run_id, item_key, channel, destination)
    now_iso = _utc_iso(now)
    result_json = json.dumps(
        _json_object(result, "result"), ensure_ascii=False, sort_keys=True
    )
    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _select_publish_delivery(conn, identity)
        if row is None:
            return None
        record = _publish_delivery_record(row)
        if record["status"] != "sending" or record.get("owner") != str(owner):
            return None
        conn.execute(
            """
            UPDATE publish_deliveries
            SET status = ?, owner = NULL, lease_expires_at = NULL,
                completed_at = ?, error_message = ?, retryable = ?,
                result_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                now_iso,
                _clean_error(error_message, 2000) or None,
                1 if retryable else 0,
                result_json,
                now_iso,
                record["id"],
            ),
        )
        return _publish_delivery_record(_select_publish_delivery(conn, identity))


def _select_news_run(conn, run_id):
    return conn.execute("SELECT * FROM news_runs WHERE run_id = ?", (run_id,)).fetchone()


def _select_publish_delivery(conn, identity):
    return conn.execute(
        """
        SELECT * FROM publish_deliveries
        WHERE run_id = ? AND item_key = ? AND channel = ? AND destination = ?
        """,
        identity,
    ).fetchone()


def _run_claim_result(row, acquired, reason):
    result = _news_run_record(row)
    result["acquired"] = bool(acquired)
    result["claim_reason"] = reason
    return result


def _run_lane_result(row, selected, reason):
    result = _news_run_record(row)
    result["lane_selected"] = bool(selected)
    result["selection_reason"] = reason
    return result


def _delivery_claim_result(row, acquired, reason):
    result = _publish_delivery_record(row)
    result["acquired"] = bool(acquired)
    result["claim_reason"] = reason
    return result


def _news_run_record(row):
    record = dict(row)
    record["stats"] = _decode_json_object(record.get("stats_json"))
    return record


def _publish_delivery_record(row):
    record = dict(row)
    record["retryable"] = bool(record.get("retryable"))
    record["payload"] = _decode_json_object(record.get("payload_json"))
    record["result"] = _decode_json_object(record.get("result_json"))
    return record


def _decode_json_object(value):
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_object(value, field_name):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a dict")
    return value


def _required_text(value, field_name):
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field_name} is required")
    return result


def _run_state(value):
    state = str(value or "").strip().upper()
    if state not in RUN_STATES:
        raise ValueError(f"state must be one of {sorted(RUN_STATES)}")
    return state


def _delivery_identity(run_id, item_key, channel, destination):
    return (
        _required_text(run_id, "run_id"),
        _required_text(item_key, "item_key"),
        _required_text(channel, "channel").lower(),
        _required_text(destination, "destination"),
    )


def _clean_error(value, limit):
    return str(value or "").strip()[:limit]


def _utc_datetime(value=None):
    if value is None:
        result = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        result = value
    else:
        raw_value = str(value).strip()
        if raw_value.endswith("Z"):
            raw_value = f"{raw_value[:-1]}+00:00"
        result = datetime.fromisoformat(raw_value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc).replace(microsecond=0)


def _utc_iso(value=None):
    return _utc_datetime(value).isoformat()


def _lease_iso(now, lease_seconds):
    seconds = int(lease_seconds)
    if seconds <= 0:
        raise ValueError("lease_seconds must be greater than zero")
    return _utc_iso(_utc_datetime(now) + timedelta(seconds=seconds))


def _timestamp_expired(value, now):
    if not value:
        return True
    return _utc_datetime(value) <= _utc_datetime(now)
