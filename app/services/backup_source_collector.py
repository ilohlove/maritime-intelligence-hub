"""Second-lane news collection for use when the Google Sheet is unavailable."""

import csv
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from app.config import ensure_runtime_seed
from app.services.rss_collector import fetch_rss_source, parse_rss_items
from app.services.article_reader import read_article
from app.services.source_policy import filter_sources
from app.services.source_policy import vietnam_source_reason
from app.services.storage import log_fetch, upsert_article, utc_now
from app.services.storage import sync_sources


DEFAULT_BACKUP_FEED_MASTER = ensure_runtime_seed("BACKUP_FEED_MASTER.csv")
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def load_backup_feeds(path=DEFAULT_BACKUP_FEED_MASTER):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build_backup_feed_plan(
    feed_master=DEFAULT_BACKUP_FEED_MASTER,
    official_sources=None,
    exclude_vietnam=False,
):
    rows = [row for row in load_backup_feeds(feed_master) if str(row.get("Enabled", "")).lower() == "yes"]
    plan = []
    official_config = next((row for row in rows if row.get("Provider") == "official_rss"), None)
    if official_sources and official_config:
        allowed_domains = _configured_domains(official_config.get("Website/Domain"))
        official = [
            row
            for row in official_sources
            if str(row.get("RSS", "")).lower() in {"yes", "partial"}
            and str(row.get("Status", "Active")).lower() == "active"
            and (not allowed_domains or _domain_allowed(row.get("Website"), allowed_domains))
        ]
        official, _ = filter_sources(official, exclude_vietnam=exclude_vietnam)
        for source in official:
            mapped = dict(source)
            mapped.update({
                "id": source.get("ID") or source.get("id"),
                "name": source.get("Source Name") or source.get("name"),
                "website": source.get("Website") or source.get("website"),
                "rss_url": source.get("RSS URL") or source.get("rss_url"),
                "country": source.get("Country") or source.get("country"),
                "language": source.get("Language") or source.get("language"),
                "category": source.get("Category") or source.get("category"),
            })
            plan.append(
                {
                    "provider": "official_rss",
                    "source": mapped,
                    "max_items": int(official_config.get("Max Items") or 10),
                }
            )
    for row in rows:
        if row.get("Provider") == "official_rss":
            continue
        source = {
            "id": row.get("ID"),
            "name": row.get("Name"),
            "website": row.get("Website/Domain") or row.get("Feed URL"),
            "rss_url": row.get("Feed URL"),
            "country": row.get("Country"),
            "language": row.get("Language"),
            "category": row.get("Category"),
            "provider": row.get("Provider"),
            "query": row.get("Query"),
        }
        kept, _ = filter_sources([source], exclude_vietnam=exclude_vietnam)
        if kept:
            plan.append({"provider": row.get("Provider"), "source": source, "max_items": int(row.get("Max Items") or 10)})
    return plan


def collect_backup_news(
    official_sources=None,
    feed_master=DEFAULT_BACKUP_FEED_MASTER,
    limit_per_source=10,
    db_path=None,
    session=None,
    exclude_vietnam=False,
    retry_attempts=2,
):
    session = session or requests.Session()
    results = []
    plan = build_backup_feed_plan(feed_master, official_sources, exclude_vietnam)
    if db_path:
        sync_sources([_storage_source(entry["source"], entry["provider"]) for entry in plan], db_path=db_path)
    for entry in plan:
        source = dict(entry["source"])
        provider = entry["provider"]
        source["provider"] = provider
        if provider == "google_news_rss":
            source["rss_url"] = GOOGLE_NEWS_RSS.format(query=quote(source.get("query") or "maritime shipping"))
        elif provider == "rsshub" and not source.get("rss_url"):
            results.append(_result(source, provider, "RSSHub feed URL is empty", status="error"))
            continue
        result = None
        for attempt in range(1, max(1, retry_attempts) + 1):
            result = _collect_one(source, provider, min(limit_per_source, entry["max_items"]), db_path, session)
            if result["status"] == "ok":
                break
            if attempt < retry_attempts:
                time.sleep(min(8, attempt * 2))
        results.append(result)
    return results


def _storage_source(source, provider):
    website = (
        source.get("website")
        if provider == "official_rss"
        else f"https://backup.local/{source.get('id') or provider}"
    ) or source.get("rss_url")
    return {
        "ID": source.get("id") or f"BACKUP-{provider}",
        "Source Name": source.get("name") or provider,
        "Website": website,
        "Country": source.get("country") or "Global",
        "Language": source.get("language") or "EN",
        "Type": source.get("Type") or "Media",
        "Category": source.get("category") or "Shipping News",
        "Priority": source.get("Priority") or "P1",
        "RSS": source.get("RSS") or "Yes",
        "API": source.get("API") or "No",
        "Crawl Method": source.get("Crawl Method") or "RSS",
        "Frequency": source.get("Frequency") or "Hourly",
        "Audience": source.get("Audience") or "All",
        "Content Quality Score": source.get("Content Quality Score") or "8",
        "Business Value Score": source.get("Business Value Score") or "8",
        "Crawl Difficulty": source.get("Crawl Difficulty") or "Easy",
        "Copyright Risk": source.get("Copyright Risk") or "Medium",
        "AI Summary Enabled": source.get("AI Summary Enabled") or "Yes",
        "Status": source.get("Status") or "Active",
    }


def _collect_one(source, provider, limit, db_path, session):
    try:
        if provider == "official_rss":
            result = fetch_rss_source(source, limit=limit, db_path=db_path, session=session)
            result["provider"] = provider
            return result
        response = session.get(
            source.get("rss_url") or source.get("website"),
            timeout=20,
            headers={"User-Agent": "MaritimeIntelligenceHub/1.0"},
        )
        response.raise_for_status()
        items = parse_rss_items(response.text, source, feed_url=source.get("rss_url"))
        inserted = 0
        accepted_items = []
        for item in items[:limit]:
            if provider == "google_news_rss" and item.get("url"):
                resolved = _resolve_url(item["url"], session)
                if resolved:
                    item["url"] = resolved
            if vietnam_source_reason(item) or (source.get("country") == "Vietnam"):
                continue
            item["source_type"] = "backup"
            item["provider"] = provider
            item["fetched_at"] = item.get("fetched_at") or utc_now()
            if len(item.get("description") or "") < 180 and item.get("url"):
                extracted = read_article(item["url"], session=session)
                if extracted["text"]:
                    item["description"] = extracted["text"][:1000]
                    item["content_excerpt"] = extracted["text"][:500]
                item["reader_provider"] = extracted["provider"]
                item["reader_status"] = extracted["status"]
                item["reader_error"] = "; ".join(extracted["errors"])
            _, created = upsert_article(item, db_path=db_path) if db_path else upsert_article(item)
            inserted += int(created)
            accepted_items.append(item)
        result = _result(source, provider, f"Fetched {len(accepted_items)} usable items, inserted {inserted}")
        result.update({"fetched": len(accepted_items), "inserted": inserted, "items": accepted_items})
        return result
    except Exception as exc:
        message = str(exc)
        if db_path:
            log_fetch(source, f"backup:{provider}", "error", source.get("rss_url"), message, db_path=db_path)
        return _result(source, provider, message, status="error")


def _resolve_url(url, session):
    try:
        response = session.get(
            url,
            timeout=15,
            allow_redirects=True,
            headers={"User-Agent": "MaritimeIntelligenceHub/1.0"},
        )
        resolved = getattr(response, "url", "")
        return resolved if isinstance(resolved, str) and resolved else url
    except Exception:
        return url


def _result(source, provider, message, status="ok"):
    return {
        "source_id": source.get("id"),
        "source_name": source.get("name"),
        "provider": provider,
        "status": status,
        "message": message,
        "feed_url": source.get("rss_url"),
        "fetched": 0,
        "inserted": 0,
    }


def _configured_domains(value):
    return {
        urlparse(part.strip() if "://" in part else f"https://{part.strip()}").netloc.lower().removeprefix("www.")
        for part in str(value or "").split(";")
        if part.strip()
    }


def _domain_allowed(value, allowed_domains):
    domain = urlparse(str(value or "")).netloc.lower().removeprefix("www.")
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in allowed_domains)
