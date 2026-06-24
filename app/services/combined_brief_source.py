import csv
import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from app.config import ROOT_DIR
from app.services.brief_writer import build_brief_item, validate_publish_items
from app.services.storage import (
    DEFAULT_DB_PATH,
    get_brief_candidate_diagnostics,
    get_brief_candidates,
    list_published_item_keys,
)


DEFAULT_COMBINED_BRIEF_PATH = ROOT_DIR / "output" / "briefs" / "combined_brief.json"
REQUEST_TIMEOUT = 30
FUZZY_TITLE_THRESHOLD = 0.88
SHEET_RUN_MARKER_COLUMN_INDEX = 11
HTML_HREF_PATTERN = re.compile(r"""href\s*=\s*["'](?P<url>https?://[^"']+)["']""", re.IGNORECASE)
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]*\]\((?P<url>https?://[^)\s]+)\)")
PLAIN_URL_PATTERN = re.compile(r"https?://[^\s<>()\]\[\"']+")


@dataclass
class CombinedSourceResult:
    payload: dict
    brief_path: Path
    stats: dict


def build_combined_brief(
    source_mode="combined",
    sheet_url="",
    sheet_limit=None,
    app_limit=None,
    card_limit=None,
    brief_path=DEFAULT_COMBINED_BRIEF_PATH,
    db_path=DEFAULT_DB_PATH,
    session=None,
):
    session = session or requests.Session()
    source_mode = (source_mode or "combined").strip().lower()
    use_app = source_mode in {"app", "combined"}
    use_sheet = source_mode in {"sheet", "combined"}

    app_diagnostics = get_brief_candidate_diagnostics(db_path=db_path, brief_type="morning") if use_app else {}
    sheet_diagnostics = sheet_lookup(sheet_url) if use_sheet else {}
    app_items = load_app_items(limit=app_limit, db_path=db_path) if use_app else []
    effective_sheet_limit = None if source_mode == "sheet" else sheet_limit
    sheet_data = load_sheet_data(sheet_url, limit=effective_sheet_limit, session=session) if use_sheet else {}
    sheet_items = sheet_data.get("items", []) if use_sheet else []
    raw_items = app_items + sheet_items
    if source_mode == "sheet":
        filtered_items, stats = select_unpublished_sheet_items(raw_items, db_path=db_path)
    else:
        filtered_items, stats = filter_publishable_items(raw_items, db_path=db_path)
    stats["source_mode"] = source_mode
    if use_app:
        stats["app_db"] = app_diagnostics
    if use_sheet:
        sheet_diagnostics["loaded_items"] = len(sheet_items)
        sheet_diagnostics["run_marker"] = sheet_data.get("run_marker", "")
        sheet_diagnostics["run_label"] = sheet_data.get("run_label", "")
        stats["sheet_source"] = sheet_diagnostics
    effective_card_limit = None if source_mode == "sheet" else card_limit
    selected_items = filtered_items if effective_card_limit is None else filtered_items[: max(1, int(effective_card_limit))]

    payload = {
        "brief_type": "combined",
        "scan_label": "combined",
        "title": "Maritime Intelligence Hub - Combined Brief",
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "source_mode": source_mode,
        "stats": stats,
        "publish_safety": validate_publish_items(selected_items),
        "items": selected_items,
    }

    path = Path(brief_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return CombinedSourceResult(payload=payload, brief_path=path, stats=stats)


def preview_combined_sources(source_mode="combined", sheet_url="", sheet_limit=None, app_limit=None, db_path=DEFAULT_DB_PATH):
    result = build_combined_brief(
        source_mode=source_mode,
        sheet_url=sheet_url,
        sheet_limit=sheet_limit,
        app_limit=app_limit,
        card_limit=None,
        db_path=db_path,
    )
    return format_combined_stats(result.stats, result.brief_path)


def load_app_items(limit=None, db_path=DEFAULT_DB_PATH):
    candidates = get_brief_candidates(db_path=db_path, limit=limit or 1000000, brief_type="morning")
    items = []
    for index, row in enumerate(candidates, start=1):
        item = build_brief_item(row)
        item["source_type"] = "app"
        item["source_rank"] = index
        item["row_index"] = index
        item["canonical_url"] = canonicalize_url(item.get("original_url"))
        item["title_hash"] = title_hash(item.get("title"))
        item["item_key"] = item_key(item)
        items.append(item)
    return items if limit is None else items[: max(1, int(limit))]


def load_sheet_items(sheet_url, limit=None, session=None):
    return load_sheet_data(sheet_url, limit=limit, session=session).get("items", [])


def load_sheet_data(sheet_url, limit=None, session=None):
    if not str(sheet_url or "").strip():
        return {"items": [], "run_marker": "", "run_label": ""}
    session = session or requests.Session()
    csv_text = fetch_sheet_csv_text(sheet_url, session=session)
    run_marker, rows = parse_sheet_csv(csv_text)
    items = []
    for index, row in enumerate(rows, start=1):
        item = sheet_row_to_item(row, index)
        if not item:
            continue
        items.append(item)
        if limit is not None and len(items) >= max(1, int(limit)):
            break
    return {
        "items": items,
        "run_marker": run_marker,
        "run_label": sheet_run_label(run_marker) if str(run_marker or "").strip() else "",
    }


def fetch_sheet_csv_text(sheet_url, session=None):
    session = session or requests.Session()
    csv_url = sheet_csv_export_url(sheet_url)
    response = session.get(csv_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.content.decode("utf-8-sig")


def parse_sheet_csv(csv_text):
    text = csv_text or ""
    raw_rows = list(csv.reader(StringIO(text)))
    run_marker = ""
    if raw_rows and len(raw_rows[0]) > SHEET_RUN_MARKER_COLUMN_INDEX:
        run_marker = str(raw_rows[0][SHEET_RUN_MARKER_COLUMN_INDEX] or "").strip()
    rows = list(csv.DictReader(StringIO(text)))
    return run_marker, rows


def get_sheet_run_status(sheet_url, session=None):
    csv_text = fetch_sheet_csv_text(sheet_url, session=session)
    run_marker, _rows = parse_sheet_csv(csv_text)
    run_label = sheet_run_label(run_marker)
    return {"run_marker": run_marker, "run_label": run_label}


def sheet_run_label(value):
    text = str(value or "").strip()
    match = re.search(r"(?P<hour>\d{1,2})\s*(?::|h|H)\s*(?P<minute>\d{1,2})", text)
    if not match:
        raise ValueError("Sheet L1 must contain a valid HH:MM run time.")
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Sheet L1 must contain a valid HH:MM run time.")
    return "morning" if hour < 12 else "evening"


def sheet_lookup(sheet_url):
    text = str(sheet_url or "").strip()
    return {
        "sheet_url": text,
        "csv_url": sheet_csv_export_url(text) if text else "",
        "loaded_items": 0,
    }


def sheet_row_to_item(row, index):
    title = first_value(row, "Vietnamese translation", "Headline")
    summary = first_value(row, "Main summary (Vietnamese)", "Main summary")
    impact_note = first_value(row, "Why it matters (Vietnamese)", "Why it matters")
    original_url = normalize_source_url(first_value(row, "Source URL"))
    source_name = first_value(row, "Source")
    if not title or not original_url:
        return None

    item = {
        "title": title,
        "published_at": first_value(row, "Date"),
        "summary": summary,
        "impact_note": impact_note,
        "category": first_value(row, "Topic"),
        "importance_score": None,
        "hotness_score": None,
        "hot_keywords": [],
        "why_hot": "",
        "source_name": source_name or "Google Sheets",
        "original_url": original_url,
        "section": first_value(row, "Section") or "Top Maritime Hot News",
        "source_type": "sheet",
        "source_rank": index,
        "row_index": index,
    }
    item["canonical_url"] = canonicalize_url(item["original_url"])
    item["title_hash"] = title_hash(item["title"])
    item["item_key"] = item_key(item)
    return item


def filter_publishable_items(items, db_path=DEFAULT_DB_PATH):
    published = published_lookup(db_path=db_path)
    stats = {
        "app_total": sum(1 for item in items if item.get("source_type") == "app"),
        "sheet_total": sum(1 for item in items if item.get("source_type") == "sheet"),
        "raw_total": len(items),
        "already_published": 0,
        "duplicate_removed": 0,
        "eligible_total": 0,
        "selected_total": 0,
        "duplicate_groups": [],
    }

    fresh = []
    for item in items:
        if is_published(item, published):
            stats["already_published"] += 1
            continue
        fresh.append(item)

    groups = dedupe_groups(fresh)
    selected = []
    for group in groups:
        winner = choose_duplicate_winner(group)
        selected.append(winner)
        removed = len(group) - 1
        if removed:
            stats["duplicate_removed"] += removed
            stats["duplicate_groups"].append(
                {
                    "winner": winner.get("title"),
                    "removed": [item.get("title") for item in group if item is not winner],
                }
            )

    stats["eligible_total"] = len(fresh)
    stats["selected_total"] = len(selected)
    return selected, stats


def select_unpublished_sheet_items(items, db_path=DEFAULT_DB_PATH):
    published = published_lookup(db_path=db_path)
    selected = []
    already_published = 0
    for item in items:
        if is_published(item, published):
            already_published += 1
            continue
        selected.append(item)

    stats = {
        "app_total": sum(1 for item in items if item.get("source_type") == "app"),
        "sheet_total": sum(1 for item in items if item.get("source_type") == "sheet"),
        "raw_total": len(items),
        "already_published": already_published,
        "duplicate_removed": 0,
        "eligible_total": len(selected),
        "selected_total": len(selected),
        "duplicate_groups": [],
    }
    return selected, stats


def dedupe_groups(items):
    groups = []
    for item in items:
        matched = None
        for group in groups:
            if is_duplicate(item, group[0]) or any(is_duplicate(item, other) for other in group[1:]):
                matched = group
                break
        if matched is None:
            groups.append([item])
        else:
            matched.append(item)
    return groups


def is_duplicate(left, right):
    left_url = left.get("canonical_url")
    right_url = right.get("canonical_url")
    if left_url and right_url and left_url == right_url:
        return True
    left_hash = left.get("title_hash")
    right_hash = right.get("title_hash")
    if left_hash and right_hash and left_hash == right_hash:
        return True
    left_title = normalize_title(left.get("title"))
    right_title = normalize_title(right.get("title"))
    if left_title and right_title:
        return SequenceMatcher(None, left_title, right_title).ratio() >= FUZZY_TITLE_THRESHOLD
    return False


def choose_duplicate_winner(group):
    return sorted(group, key=winner_score, reverse=True)[0]


def winner_score(item):
    complete_vi = has_vietnamese_marks(item.get("title")) + has_vietnamese_marks(item.get("summary")) + has_vietnamese_marks(item.get("impact_note"))
    source_bonus = 3 if item.get("source_type") == "sheet" else 0
    completeness = sum(1 for key in ["title", "summary", "impact_note", "source_name", "original_url"] if item.get(key))
    hotness = int(item.get("hotness_score") or item.get("importance_score") or 0)
    rank_penalty = int(item.get("source_rank") or 9999)
    return (source_bonus, complete_vi, completeness, hotness, -rank_penalty)


def published_lookup(db_path=DEFAULT_DB_PATH):
    rows = list_published_item_keys(db_path=db_path)
    return {
        "keys": {row.get("item_key") for row in rows if row.get("item_key")},
        "urls": {row.get("canonical_url") for row in rows if row.get("canonical_url")},
        "title_hashes": {row.get("title_hash") for row in rows if row.get("title_hash")},
    }


def is_published(item, lookup):
    return (
        item.get("item_key") in lookup["keys"]
        or (item.get("canonical_url") and item.get("canonical_url") in lookup["urls"])
        or (item.get("title_hash") and item.get("title_hash") in lookup["title_hashes"])
    )


def sheet_csv_export_url(sheet_url):
    text = str(sheet_url or "").strip()
    match = re.search(r"/spreadsheets/d/([^/]+)", text)
    if not match:
        raise ValueError("Google Sheets URL is invalid.")
    spreadsheet_id = match.group(1)
    parsed = urlparse(text)
    query = dict(parse_qsl(parsed.query))
    gid = query.get("gid")
    if not gid and parsed.fragment.startswith("gid="):
        gid = parsed.fragment.split("=", 1)[1]
    gid = gid or "0"
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"


def canonicalize_url(value):
    text = normalize_source_url(value)
    if not text:
        return ""
    parsed = urlparse(text)
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    path = parsed.path.rstrip("/") or parsed.path
    return urlunparse(
        (
            parsed.scheme.lower() or "https",
            parsed.netloc.lower(),
            path,
            "",
            urlencode(query, doseq=True),
            "",
        )
    )


def normalize_source_url(value):
    text = str(value or "").strip()
    if not text:
        return ""

    html_match = HTML_HREF_PATTERN.search(text)
    if html_match:
        return html.unescape(html_match.group("url")).strip()

    markdown_match = MARKDOWN_LINK_PATTERN.search(text)
    if markdown_match:
        return markdown_match.group("url").strip()

    plain_match = PLAIN_URL_PATTERN.search(text)
    if plain_match:
        return plain_match.group(0).rstrip(".,;")

    return text


def normalize_title(value):
    text = str(value or "").lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def title_hash(value):
    normalized = normalize_title(value)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def item_key(item):
    canonical = item.get("canonical_url") or ""
    if canonical:
        return "url:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return "title:" + (item.get("title_hash") or title_hash(item.get("title")))


def first_value(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return " ".join(str(value).split())
    return ""


def has_vietnamese_marks(value):
    return 1 if re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", str(value or "").lower()) else 0


def format_combined_stats(stats, brief_path=None):
    lines = [
        "Combined source check",
        f"Source mode: {stats.get('source_mode', 'combined')}",
        f"App items: {stats.get('app_total', 0)}",
        f"Sheet items: {stats.get('sheet_total', 0)}",
        f"Raw items: {stats.get('raw_total', 0)}",
        f"Already published removed: {stats.get('already_published', 0)}",
        f"Duplicate removed: {stats.get('duplicate_removed', 0)}",
        f"Eligible after published filter: {stats.get('eligible_total', 0)}",
        f"Selected after dedupe: {stats.get('selected_total', 0)}",
    ]
    app_db = stats.get("app_db") or {}
    if app_db:
        lines.extend(
            [
                "",
                "App database",
                f"DB path: {app_db.get('db_path', '')}",
                f"Articles: {app_db.get('articles_total', 0)}",
                f"AI summaries: {app_db.get('summaries_total', 0)}",
                f"Summarized new articles: {app_db.get('summarized_new_total', 0)}",
                f"Summarized articles with published_at: {app_db.get('summarized_with_published_at_total', 0)}",
                f"Fresh brief candidates: {app_db.get('candidate_window_total', 0)}",
                f"Brief cutoff: {app_db.get('cutoff', '')}",
                f"Published item records: {app_db.get('published_items_total', 0)}",
            ]
        )
    sheet_source = stats.get("sheet_source") or {}
    if sheet_source:
        lines.extend(
            [
                "",
                "Google Sheet source",
                f"Sheet URL: {sheet_source.get('sheet_url', '')}",
                f"CSV export URL: {sheet_source.get('csv_url', '')}",
                f"Loaded sheet items: {sheet_source.get('loaded_items', 0)}",
                f"Sheet L1: {sheet_source.get('run_marker', '')}",
                f"Sheet run label: {sheet_source.get('run_label', '')}",
            ]
        )
    if brief_path:
        lines.append(f"Brief JSON: {brief_path}")
    if stats.get("duplicate_groups"):
        lines.append("")
        lines.append("Duplicate groups:")
        for group in stats["duplicate_groups"][:10]:
            lines.append(f"- Keep: {group.get('winner')}")
            for title in group.get("removed") or []:
                lines.append(f"  Remove: {title}")
    return "\n".join(lines)


def format_empty_combined_message(stats, brief_path=None):
    source_mode = str(stats.get("source_mode") or "combined").lower()
    app_db = stats.get("app_db") or {}
    reason = "No new articles after published and duplicate filters."

    if source_mode == "app":
        if int(app_db.get("articles_total") or 0) == 0:
            reason = "App database is empty. Run scan + AI summary first."
        elif int(app_db.get("summaries_total") or 0) == 0:
            reason = "No AI summaries yet. Run AI summarization first."
        elif int(app_db.get("candidate_window_total") or 0) == 0:
            reason = "No fresh summarized articles in the current brief window."
        elif int(stats.get("already_published") or 0) or int(stats.get("duplicate_removed") or 0):
            reason = (
                "No new articles remain after published and duplicate filters "
                f"(published removed: {stats.get('already_published', 0)}, "
                f"duplicate removed: {stats.get('duplicate_removed', 0)})."
            )
    elif source_mode == "sheet":
        sheet_source = stats.get("sheet_source") or {}
        if not sheet_source.get("sheet_url"):
            reason = "Google Sheet URL is empty. Select Sheet mode and paste the Google Sheet link."
        elif int(sheet_source.get("loaded_items") or 0) == 0:
            reason = "No usable rows loaded from the Google Sheet link."

    return "\n\n".join([reason, format_combined_stats(stats, brief_path)])
