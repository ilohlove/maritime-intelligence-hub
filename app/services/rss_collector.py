import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree import ElementTree

import requests

from app.services.storage import log_fetch, upsert_article, utc_now


USER_AGENT = "MaritimeIntelligenceHub/1.0 (+source-metadata-only MVP)"
REQUEST_TIMEOUT = 15


def fetch_rss_sources(sources, limit_per_source=10, db_path=None):
    results = []
    for source in sources:
        result = fetch_rss_source(source, limit=limit_per_source, db_path=db_path)
        results.append(result)
    return results


def fetch_rss_source(source, limit=10, db_path=None, session=None):
    session = session or requests.Session()
    fetch_url = source.get("rss_url") or source.get("RSS URL")

    try:
        if not fetch_url:
            fetch_url = discover_feed_url(source["website"], session=session)

        if not fetch_url:
            message = "RSS feed not found by autodiscovery"
            _log(source, "rss", "skipped", source["website"], message, db_path)
            return _result(source, "skipped", message)

        response = session.get(fetch_url, timeout=REQUEST_TIMEOUT, headers=_headers())
        response.raise_for_status()

        items = parse_rss_items(response.text, source, feed_url=fetch_url)
        inserted = 0
        duplicates = 0
        for item in items[:limit]:
            article_id, created = upsert_article(item, db_path=db_path) if db_path else upsert_article(item)
            if created:
                inserted += 1
            else:
                duplicates += 1

        message = f"Fetched {len(items[:limit])} items, inserted {inserted}, duplicates {duplicates}"
        _log(source, "rss", "ok", fetch_url, message, db_path, inserted)
        return _result(source, "ok", message, feed_url=fetch_url, fetched=len(items[:limit]), inserted=inserted)
    except Exception as exc:
        message = str(exc)
        _log(source, "rss", "error", fetch_url or source.get("website"), message, db_path)
        return _result(source, "error", message, feed_url=fetch_url)


def discover_feed_url(website, session=None):
    session = session or requests.Session()
    response = session.get(website, timeout=REQUEST_TIMEOUT, headers=_headers())
    response.raise_for_status()
    html_text = response.text

    link_pattern = re.compile(
        r"<link[^>]+(?:type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*|rel=[\"']alternate[\"'][^>]*)>",
        re.IGNORECASE,
    )
    href_pattern = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)

    for match in link_pattern.finditer(html_text):
        href_match = href_pattern.search(match.group(0))
        if href_match:
            return urljoin(website, html.unescape(href_match.group(1)))

    common_paths = ["/feed", "/rss", "/rss.xml", "/feed.xml"]
    for path in common_paths:
        candidate = urljoin(website, path)
        try:
            candidate_response = session.get(candidate, timeout=REQUEST_TIMEOUT, headers=_headers())
            if candidate_response.ok and _looks_like_feed(candidate_response.text):
                return candidate
        except requests.RequestException:
            continue

    return None


def parse_rss_items(xml_text, source, feed_url=None):
    try:
        root = ElementTree.fromstring(xml_text.encode("utf-8"))
    except ElementTree.ParseError:
        root = ElementTree.fromstring(xml_text)

    if _strip_namespace(root.tag) == "rss":
        raw_items = root.findall(".//item")
    elif _strip_namespace(root.tag) == "feed":
        raw_items = root.findall("{*}entry")
    else:
        raw_items = root.findall(".//item") or root.findall("{*}entry")

    return [_article_from_item(item, source, feed_url) for item in raw_items if _item_title(item)]


def normalize_title(title):
    cleaned = re.sub(r"\s+", " ", html.unescape(title or "")).strip().lower()
    return cleaned


def title_hash(title):
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


def _article_from_item(item, source, feed_url):
    title = _item_title(item)
    url = _item_link(item) or feed_url or source["website"]
    description = _item_text(item, ["description", "summary", "content"]) or ""
    description = _clean_text(description)

    return {
        "source_id": source["id"],
        "source_name": source["name"],
        "title": _clean_text(title),
        "url": url,
        "normalized_title": normalize_title(title),
        "title_hash": title_hash(title),
        "published_at": _item_date(item),
        "fetched_at": utc_now(),
        "language": source.get("language"),
        "category": source.get("category"),
        "description": description[:1000],
        "content_excerpt": description[:500],
        "importance_score": None,
    }


def _item_title(item):
    return _item_text(item, ["title"])


def _item_link(item):
    link = _item_text(item, ["link"])
    if link:
        return link

    for child in list(item):
        if _strip_namespace(child.tag) == "link":
            href = child.attrib.get("href")
            if href:
                return href
    return None


def _item_date(item):
    raw_date = _item_text(item, ["pubDate", "published", "updated", "dc:date"])
    if not raw_date:
        return None
    try:
        parsed = parsedate_to_datetime(raw_date)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.replace(microsecond=0).isoformat()
    except Exception:
        return raw_date.strip()


def _item_text(item, names):
    wanted = {name.lower() for name in names}
    for child in item.iter():
        local_name = _strip_namespace(child.tag).lower()
        if local_name in wanted and child.text:
            return child.text.strip()
    return None


def _clean_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
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


def _repair_mojibake(value):
    if not value or "â" not in value:
        return value

    try:
        repaired = value.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return value

    return repaired if "â" not in repaired else value


def _strip_namespace(tag):
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _looks_like_feed(text):
    lowered = text[:500].lower()
    return "<rss" in lowered or "<feed" in lowered


def _headers():
    return {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, text/html"}


def _result(source, status, message, feed_url=None, fetched=0, inserted=0):
    return {
        "source_id": source.get("id"),
        "source_name": source.get("name"),
        "status": status,
        "message": message,
        "feed_url": feed_url,
        "fetched": fetched,
        "inserted": inserted,
    }


def _log(source, stage, status, url, message, db_path, fetched_count=0):
    if db_path:
        log_fetch(source, stage, status, url=url, message=message, fetched_count=fetched_count, db_path=db_path)
    else:
        log_fetch(source, stage, status, url=url, message=message, fetched_count=fetched_count)
