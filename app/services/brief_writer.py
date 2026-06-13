import json
import shutil
from datetime import datetime
from pathlib import Path

from app.config import ROOT_DIR
from app.services.scoring import parse_hot_keywords
from app.services.storage import get_brief_candidates, insert_brief, list_trend_keywords


DEFAULT_BRIEF_DIR = ROOT_DIR / "output" / "briefs"

BRIEF_TITLES = {
    "morning": "Morning Brief",
    "evening": "Evening Brief",
    "weekly": "Weekly Brief",
}


def generate_brief(brief_type, db_path=None, output_dir=DEFAULT_BRIEF_DIR, limit=12):
    if brief_type not in BRIEF_TITLES:
        raise ValueError(f"Unsupported brief type: {brief_type}")

    candidates = (
        get_brief_candidates(db_path=db_path, limit=limit, brief_type=brief_type)
        if db_path
        else get_brief_candidates(limit=limit, brief_type=brief_type)
    )
    trends = list_trend_keywords(db_path=db_path) if db_path else list_trend_keywords()
    payload = build_brief_payload(brief_type, candidates)
    payload["daily_hot_keywords"] = build_hot_keyword_payload(trends)
    payload["general_hot"] = build_general_hot_payload(trends)
    markdown = render_markdown(payload)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    md_path = output_path / f"{brief_type}_brief.md"
    json_path = output_path / f"{brief_type}_brief.json"

    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    brief_id = (
        insert_brief(brief_type, payload["title"], markdown, payload, db_path=db_path)
        if db_path
        else insert_brief(brief_type, payload["title"], markdown, payload)
    )

    return {
        "brief_id": brief_id,
        "brief_type": brief_type,
        "items": len(payload["items"]),
        "markdown_path": md_path,
        "json_path": json_path,
    }


def generate_scan_brief(scan_label="morning", db_path=None, output_dir=DEFAULT_BRIEF_DIR, limit=12):
    label = normalize_scan_label(scan_label)
    query_type = "weekly" if label == "weekly" else "morning"
    candidates = (
        get_brief_candidates(db_path=db_path, limit=limit, brief_type=query_type)
        if db_path
        else get_brief_candidates(limit=limit, brief_type=query_type)
    )
    trends = list_trend_keywords(db_path=db_path) if db_path else list_trend_keywords()
    payload = build_brief_payload(label, candidates)
    payload["brief_type"] = "scan"
    payload["scan_label"] = label
    payload["daily_hot_keywords"] = build_hot_keyword_payload(trends)
    payload["general_hot"] = build_general_hot_payload(trends)
    markdown = render_markdown(payload)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    md_path = output_path / f"{timestamp}_{label}.md"
    json_path = output_path / f"{timestamp}_{label}.json"
    latest_md_path = output_path / "latest_brief.md"
    latest_json_path = output_path / "latest_brief.json"

    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copyfile(md_path, latest_md_path)
    shutil.copyfile(json_path, latest_json_path)

    brief_id = (
        insert_brief(label, payload["title"], markdown, payload, db_path=db_path)
        if db_path
        else insert_brief(label, payload["title"], markdown, payload)
    )

    return {
        "brief_id": brief_id,
        "brief_type": "scan",
        "scan_label": label,
        "items": len(payload["items"]),
        "markdown_path": md_path,
        "json_path": json_path,
        "latest_markdown_path": latest_md_path,
        "latest_json_path": latest_json_path,
    }


def build_brief_payload(brief_type, articles):
    generated_at = datetime.now().replace(microsecond=0).isoformat()
    title = f"Maritime Intelligence Hub - {BRIEF_TITLES.get(brief_type, brief_type.title() + ' Scan')}"
    items = [build_brief_item(article) for article in articles]
    return {
        "brief_type": brief_type,
        "title": title,
        "generated_at": generated_at,
        "publish_safety": validate_publish_items(items),
        "items": items,
    }


def normalize_scan_label(value):
    label = str(value or "morning").strip().lower()
    return label if label in {"morning", "evening", "weekly"} else "scan"


def build_brief_item(article):
    summary = _bounded(article["summary"], 900)
    impact_note = _bounded(article["impact_note"], 600)
    return {
        "title": article.get("ai_headline") or article["title"],
        "published_at": article.get("published_at"),
        "summary": summary,
        "impact_note": impact_note,
        "category": article.get("ai_category") or article.get("category"),
        "importance_score": article.get("ai_importance_score") or article.get("importance_score"),
        "hotness_score": article.get("hotness_score") or article.get("importance_score"),
        "hot_keywords": parse_hot_keywords(article.get("hot_keywords")),
        "why_hot": article.get("why_hot") or "",
        "source_name": article.get("ai_source_name") or article["source_name"],
        "original_url": article.get("ai_original_url") or article["url"],
        "section": classify_brief_section(article),
    }


def build_hot_keyword_payload(trends, limit=12):
    items = []
    for trend in trends:
        if trend.get("category") in {"general_hot", "google_trend"}:
            continue
        items.append(
            {
                "keyword": trend.get("keyword"),
                "category": trend.get("category"),
                "timeframe": trend.get("timeframe"),
                "search_volume": trend.get("search_volume"),
                "source": trend.get("source"),
            }
        )
        if len(items) >= limit:
            break
    return items


def build_general_hot_payload(trends, limit=2):
    items = []
    for trend in trends:
        if trend.get("category") != "general_hot":
            continue
        items.append(
            {
                "keyword": trend.get("keyword"),
                "timeframe": trend.get("timeframe"),
                "search_volume": trend.get("search_volume"),
                "source": trend.get("source"),
            }
        )
        if len(items) >= limit:
            break
    return items


def classify_brief_section(article):
    category = (article.get("category") or "").lower()
    source_name = (article.get("source_name") or "").lower()
    text = " ".join(
        str(article.get(key) or "").lower()
        for key in ["title", "description", "content_excerpt"]
    )
    if "vietnam" in text or "viet nam" in text or "saigon" in source_name or "vimc" in source_name:
        return "Vietnam Port & Logistics Impact"
    if category in {"port", "logistics"}:
        return "Vietnam Port & Logistics Impact"
    return "Top Maritime Hot News"


def validate_publish_items(items):
    errors = []
    for index, item in enumerate(items, start=1):
        if not item.get("source_name"):
            errors.append(f"Item {index}: missing source_name")
        if not item.get("original_url"):
            errors.append(f"Item {index}: missing original_url")
        if len(item.get("summary") or "") > 900:
            errors.append(f"Item {index}: summary is too long")
        if _looks_like_full_article(item.get("summary") or ""):
            errors.append(f"Item {index}: summary may be too close to full article length")

    return {
        "ready": not errors,
        "errors": errors,
    }


def render_markdown(payload):
    lines = [
        f"# {payload['title']}",
        "",
        f"Generated at: {payload['generated_at']}",
        "",
    ]

    if not payload["items"]:
        lines.extend(
            [
                "No eligible articles found yet.",
                "",
                "Source links will appear here after RSS collection and mock AI processing produce candidates.",
            ]
        )
        return "\n".join(lines)

    if payload.get("daily_hot_keywords"):
        lines.extend(["## Weekly/Daily Hot Keywords", ""])
        for trend in payload["daily_hot_keywords"][:12]:
            label = trend.get("keyword")
            category = trend.get("category")
            timeframe = trend.get("timeframe")
            lines.append(f"- {label} ({category}, {timeframe})")
        lines.append("")

    for section in ["Top Maritime Hot News", "Vietnam Port & Logistics Impact"]:
        section_items = [item for item in payload["items"] if item.get("section") == section]
        if not section_items:
            continue
        lines.extend([f"## {section}", ""])
        for index, item in enumerate(section_items, start=1):
            lines.extend(render_markdown_item(index, item))

    if payload.get("general_hot"):
        lines.extend(["## Tin nong ngoai nganh", ""])
        for item in payload["general_hot"][:2]:
            lines.append(f"- {item.get('keyword')} ({item.get('timeframe')})")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def render_markdown_item(index, item):
    hot_keywords = ", ".join(item.get("hot_keywords") or []) or "N/A"
    return [
        f"### {index}. {item['title']}",
        "",
        f"- Published: {item.get('published_at') or 'Unknown'}",
        f"- Category: {item.get('category') or 'Unknown'}",
        f"- Importance: {item.get('importance_score') or 'N/A'}",
        f"- Hotness: {item.get('hotness_score') or 'N/A'}",
        f"- Hot keywords: {hot_keywords}",
        f"- Why hot: {item.get('why_hot') or 'N/A'}",
        f"- Source: {item['source_name']}",
        f"- URL: {item['original_url']}",
        "",
        item["summary"],
        "",
        item["impact_note"],
        "",
    ]


def render_markdown_legacy(payload):
    lines = []
    for index, item in enumerate(payload["items"], start=1):
        lines.extend(
            [
                f"## {index}. {item['title']}",
                "",
                f"- Category: {item.get('category') or 'Unknown'}",
                f"- Importance: {item.get('importance_score') or 'N/A'}",
                f"- Source: {item['source_name']}",
                f"- URL: {item['original_url']}",
                "",
                item["summary"],
                "",
                item["impact_note"],
                "",
            ]
        )
    return lines


def _bounded(value, max_length):
    text = " ".join(str(value or "").split())
    return text[:max_length].strip()


def _looks_like_full_article(value):
    word_count = len((value or "").split())
    return word_count > 180
