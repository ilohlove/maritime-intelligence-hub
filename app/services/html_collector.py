import html
import re
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import requests

from app.services.rss_collector import REQUEST_TIMEOUT, USER_AGENT, normalize_title, title_hash
from app.services.storage import log_fetch, upsert_article, utc_now


MAX_EXCERPT_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 1000

DEFAULT_PATHS = {
    "IMO": ["/en/MediaCentre/PressBriefings/pages/default.aspx"],
    "ICS": ["/news-insights/news/"],
    "Maersk News": ["/news"],
    "MSC Newsroom": ["/en/newsroom"],
    "CMA CGM News": ["/news"],
    "Hapag-Lloyd News": ["/en/company/press/releases.html"],
    "Vinamarine": ["/vi/tin-tuc"],
    "Vietnam Register": ["/web/guest/tin-tuc"],
    "Saigon Newport": ["/tin-tuc"],
    "VIMC": ["/tin-tuc"],
}

TITLE_LINK_PATTERN = re.compile(
    r"<a\b(?P<attrs>[^>]*href=[\"'][^\"']+[\"'][^>]*)>(?P<body>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
HREF_PATTERN = re.compile(r"href=[\"'](?P<href>[^\"']+)[\"']", re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")
DATE_PATTERN = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")


def fetch_html_sources(sources, limit_per_source=5, db_path=None, session=None):
    results = []
    for source in sources:
        results.append(
            fetch_html_source(
                source,
                limit=limit_per_source,
                db_path=db_path,
                session=session,
            )
        )
    return results


def fetch_html_source(source, limit=5, db_path=None, session=None):
    session = session or requests.Session()
    source_name = _source_value(source, "Source Name", "name")
    website = _source_value(source, "Website", "website")
    fetch_url = _source_entry_url(source)

    try:
        response = session.get(fetch_url, timeout=REQUEST_TIMEOUT, headers=_headers())
        response.raise_for_status()
        articles = parse_html_articles(response.text, source, base_url=fetch_url, limit=limit)

        inserted = 0
        duplicates = 0
        for article in articles:
            _, created = upsert_article(article, db_path=db_path) if db_path else upsert_article(article)
            if created:
                inserted += 1
            else:
                duplicates += 1

        message = f"Fetched {len(articles)} HTML items, inserted {inserted}, duplicates {duplicates}"
        _log(_mapped_source(source), "html", "ok", fetch_url, message, db_path, inserted)
        return _result(source, "ok", message, fetch_url, fetched=len(articles), inserted=inserted)
    except Exception as exc:
        message = str(exc)
        _log(_mapped_source(source), "html", "error", fetch_url or website, message, db_path)
        return _result(source, "error", message, fetch_url)


def parse_html_articles(html_text, source, base_url=None, limit=5):
    base_url = base_url or _source_value(source, "Website", "website")
    source_id = _source_value(source, "ID", "id")
    source_name = _source_value(source, "Source Name", "name")
    language = _source_value(source, "Language", "language")
    category = _source_value(source, "Category", "category")

    candidates = []
    seen_urls = set()
    for match in TITLE_LINK_PATTERN.finditer(html_text):
        href_match = HREF_PATTERN.search(match.group("attrs"))
        if not href_match:
            continue

        title = _clean_text(match.group("body"))
        if not _looks_like_news_title(title):
            continue

        url = urljoin(base_url, html.unescape(href_match.group("href")))
        if url in seen_urls or _is_non_article_url(url):
            continue

        seen_urls.add(url)
        nearby_text = _clean_text(html_text[match.end() : match.end() + 600])
        published_at = _extract_date(nearby_text)
        description = nearby_text[:MAX_DESCRIPTION_LENGTH]
        candidates.append(
            {
                "source_id": source_id,
                "source_name": source_name,
                "title": title,
                "url": url,
                "normalized_title": normalize_title(title),
                "title_hash": title_hash(title),
                "published_at": published_at,
                "fetched_at": utc_now(),
                "language": language,
                "category": category,
                "description": description,
                "content_excerpt": description[:MAX_EXCERPT_LENGTH],
                "importance_score": None,
            }
        )

        if len(candidates) >= limit:
            break

    return candidates


def run_html_dry_run(sources, db_path=None):
    results = []
    for source in sources:
        message = "HTML live crawling deferred; source recorded for parser readiness"
        _log(source, "html_dry_run", "skipped", source.get("Website"), message, db_path)
        results.append(
            {
                "source_id": source.get("ID"),
                "source_name": source.get("Source Name"),
                "status": "skipped",
                "message": message,
                "url": source.get("Website"),
            }
        )
    return results


def _source_entry_url(source):
    source_name = _source_value(source, "Source Name", "name")
    website = _source_value(source, "Website", "website")
    paths = DEFAULT_PATHS.get(source_name) or [""]
    return urljoin(website.rstrip("/") + "/", paths[0].lstrip("/"))


def _looks_like_news_title(title):
    if not title or len(title) < 18 or len(title) > 220:
        return False
    lowered = title.lower()
    blocked = {
        "privacy",
        "cookie",
        "login",
        "subscribe",
        "contact",
        "about",
        "search",
        "language",
    }
    return not any(word in lowered for word in blocked)


def _is_non_article_url(url):
    lowered = url.lower()
    return any(
        marker in lowered
        for marker in [
            "mailto:",
            "javascript:",
            "#",
            "/login",
            "/privacy",
            "/cookie",
            "/search",
        ]
    )


def _extract_date(text):
    match = DATE_PATTERN.search(text or "")
    if not match:
        return None

    raw_date = match.group(0)
    try:
        parsed = parsedate_to_datetime(raw_date)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.replace(microsecond=0).isoformat()
    except Exception:
        return raw_date


def _clean_text(value):
    text = TAG_PATTERN.sub(" ", value or "")
    text = html.unescape(text)
    text = _repair_encoding(text)
    return re.sub(r"\s+", " ", text).strip()


def _repair_encoding(value):
    markers = ["Ã", "Â", "â€", "Ä", "áº", "á»"]
    if not value or not any(marker in value for marker in markers):
        return value

    for source_encoding in ["cp1252", "latin1"]:
        try:
            repaired = value.encode(source_encoding).decode("utf-8")
        except UnicodeError:
            continue
        if repaired != value:
            return repaired
    return value


def _source_value(source, csv_key, db_key):
    return source.get(csv_key) or source.get(db_key) or ""


def _mapped_source(source):
    return {
        "id": _source_value(source, "ID", "id"),
        "name": _source_value(source, "Source Name", "name"),
    }


def _result(source, status, message, fetch_url=None, fetched=0, inserted=0):
    return {
        "source_id": _source_value(source, "ID", "id"),
        "source_name": _source_value(source, "Source Name", "name"),
        "status": status,
        "message": message,
        "fetch_url": fetch_url,
        "fetched": fetched,
        "inserted": inserted,
    }


def _headers():
    return {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}


def _log(source, stage, status, url, message, db_path, fetched_count=0):
    mapped_source = _mapped_source(source)
    if db_path:
        log_fetch(
            mapped_source,
            stage,
            status,
            url=url,
            message=message,
            fetched_count=fetched_count,
            db_path=db_path,
        )
    else:
        log_fetch(mapped_source, stage, status, url=url, message=message, fetched_count=fetched_count)
