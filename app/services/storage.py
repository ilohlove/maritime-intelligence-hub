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
                stop_reason TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_facebook_group_attempts_time
            ON facebook_group_attempts(attempted_at);
            """
        )
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
                json.dumps(summary.get("fallback_errors") or [], ensure_ascii=False),
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
                   priority, payload_json
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
        conn.execute(
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
                payload_json=excluded.payload_json
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
        if status in {"published", "pending", "failed"}:
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
               priority, payload_json
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
                   priority, payload_json
            FROM facebook_group_deliveries
            WHERE id = ?
            """,
            (int(delivery_id),),
        ).fetchone()
    return dict(row) if row else None


def cancel_facebook_group_delivery(delivery_id, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE facebook_group_deliveries
            SET status = 'cancelled', scheduled_at = NULL, stop_reason = 'Cancelled by user'
            WHERE id = ? AND status IN ('queued', 'failed', 'needs_login')
            """,
            (int(delivery_id),),
        )
    return cursor.rowcount > 0


def expire_facebook_group_deliveries(now_iso, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    with connect_db(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE facebook_group_deliveries
            SET status = 'expired', scheduled_at = NULL, stop_reason = 'Queue item expired'
            WHERE status IN ('queued', 'failed', 'needs_login')
              AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (str(now_iso),),
        )
    return cursor.rowcount


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
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM facebook_group_attempts
            WHERE attempted_at >= ?
            """,
            (str(since_iso),),
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
