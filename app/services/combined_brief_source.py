import csv
import hashlib
import html
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from app.config import ROOT_DIR, ensure_runtime_seed
from app.services.brief_writer import build_brief_item, validate_publish_items
from app.services.storage import (
    DEFAULT_DB_PATH,
    get_brief_candidate_diagnostics,
    get_brief_candidates,
    list_published_item_keys,
)
from app.services.backup_source_collector import DEFAULT_BACKUP_FEED_MASTER, collect_backup_news
from app.services.source_policy import normalize_filter_flag, vietnam_source_reason
from app.services.source_master import load_sources


DEFAULT_COMBINED_BRIEF_PATH = ROOT_DIR / "output" / "briefs" / "combined_brief.json"
DEFAULT_SOURCE_MASTER = ensure_runtime_seed("NEWS_SOURCE_MASTER.csv")
REQUEST_TIMEOUT = 30
SHEET_RUN_MARKER_COLUMN_INDEX = 11
SHEET_COMPLETED_AT_COLUMN_INDEX = 12
SHEET_RUN_ID_COLUMN_INDEX = 13
SHEET_STATUS_COLUMN_INDEX = 14
SHEET_ROW_COUNT_COLUMN_INDEX = 15
SHEET_ERROR_CODE_COLUMN_INDEX = 16
SHEET_DATA_COLUMN_COUNT = 11
SHEET_RUN_ID_PATTERN = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2}):(?P<slot>morning|evening)$")
ISO_DATE_TIME_PREFIX_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]")
SHEET_PROTOCOL_STATUSES = {"RUNNING", "COMPLETED", "FAILED"}
VIETNAM_TIMEZONE = timezone(timedelta(hours=7))
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
    exclude_vietnam=False,
    backup_feed_master=None,
    sheet_snapshot=None,
    expected_run_id=None,
    allow_backup=True,
):
    session = session or requests.Session()
    source_mode = (source_mode or "combined").strip().lower()
    use_app = source_mode in {"app", "combined"}
    use_sheet = source_mode in {"sheet", "combined"}

    app_diagnostics = get_brief_candidate_diagnostics(db_path=db_path, brief_type="morning") if use_app else {}
    sheet_diagnostics = sheet_lookup(sheet_url) if use_sheet else {}
    app_items = load_app_items(limit=app_limit, db_path=db_path) if use_app else []
    effective_sheet_limit = None if source_mode == "sheet" else sheet_limit
    sheet_error = ""
    try:
        if use_sheet and sheet_snapshot is not None:
            sheet_data = dict(sheet_snapshot)
        elif use_sheet:
            sheet_data = load_sheet_data(sheet_url, limit=effective_sheet_limit, session=session)
        else:
            sheet_data = {}
    except Exception as exc:
        sheet_error = str(exc)
        sheet_data = empty_sheet_snapshot()

    sheet_evaluation = {}
    if use_sheet and sheet_data.get("protocol_version") == "v1":
        sheet_evaluation = evaluate_sheet_snapshot(
            sheet_data,
            expected_run_id=expected_run_id or sheet_data.get("run_id"),
        )
        sheet_data["evaluation"] = sheet_evaluation
        if sheet_evaluation["state"] != "ready":
            sheet_data["items"] = []
    sheet_items = sheet_data.get("items", []) if use_sheet else []
    if effective_sheet_limit is not None:
        sheet_items = sheet_items[: max(1, int(effective_sheet_limit))]
    if normalize_filter_flag(exclude_vietnam):
        sheet_items = [item for item in sheet_items if not vietnam_source_reason(item)]
        app_items = [item for item in app_items if not vietnam_source_reason(item)]
    backup_items = []
    backup_results = []
    fallback_reason = ""
    protocol_blocks_automatic_backup = (
        sheet_data.get("protocol_version") == "v1"
        and sheet_evaluation.get("state") in {"ready", "waiting", "invalid"}
    )
    if (
        allow_backup
        and use_sheet
        and source_mode in {"sheet", "combined"}
        and not sheet_items
        and not protocol_blocks_automatic_backup
    ):
        fallback_reason = sheet_error or "sheet_empty"
        try:
            backup_results = collect_backup_news(
                feed_master=backup_feed_master or DEFAULT_BACKUP_FEED_MASTER,
                official_sources=load_sources(DEFAULT_SOURCE_MASTER)[0],
                limit_per_source=effective_sheet_limit or 10,
                db_path=db_path,
                session=session,
                exclude_vietnam=exclude_vietnam,
            )
            for result in backup_results:
                backup_items.extend(result.get("items") or [])
        except Exception as exc:
            fallback_reason = f"{fallback_reason}; backup_error={exc}"
    normalized_backup_items = [_backup_item_to_brief(item) for item in backup_items]
    raw_items = app_items + sheet_items + normalized_backup_items
    filtered_items, stats = filter_publishable_items(raw_items, db_path=db_path)
    stats["source_mode"] = source_mode
    stats["fallback_reason"] = fallback_reason
    stats["backup_results"] = backup_results
    backup_attempted = bool(fallback_reason)
    backup_ok_sources = sum(1 for result in backup_results if result.get("status", "ok") == "ok")
    backup_failed_sources = sum(1 for result in backup_results if result.get("status", "ok") != "ok")
    stats["backup_status"] = (
        "not_used" if not backup_attempted else "ok" if backup_ok_sources else "failed"
    )
    stats["backup_ok_sources"] = backup_ok_sources
    stats["backup_failed_sources"] = backup_failed_sources
    if use_app:
        stats["app_db"] = app_diagnostics
    if use_sheet:
        sheet_diagnostics["loaded_items"] = len(sheet_items)
        sheet_diagnostics["run_marker"] = sheet_data.get("run_marker", "")
        sheet_diagnostics["run_label"] = sheet_data.get("run_label", "")
        sheet_diagnostics["protocol_version"] = sheet_data.get("protocol_version", "")
        sheet_diagnostics["started_at"] = sheet_data.get("started_at", "")
        sheet_diagnostics["completed_at"] = sheet_data.get("completed_at", "")
        sheet_diagnostics["run_id"] = sheet_data.get("run_id", "")
        sheet_diagnostics["status"] = sheet_data.get("status", "")
        sheet_diagnostics["row_count"] = sheet_data.get("row_count")
        sheet_diagnostics["error_code"] = sheet_data.get("error_code", "")
        sheet_diagnostics["evaluation"] = sheet_evaluation
        stats["sheet_source"] = sheet_diagnostics
    effective_card_limit = None if source_mode == "sheet" else card_limit
    selected_items = filtered_items if effective_card_limit is None else filtered_items[: max(1, int(effective_card_limit))]
    stats["output_total"] = len(selected_items)

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
    write_json_atomic(path, payload)
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


def _backup_item_to_brief(item):
    normalized = dict(item)
    normalized["original_url"] = normalized.get("original_url") or normalized.get("url") or ""
    normalized["source_name"] = normalized.get("source_name") or normalized.get("name") or "Backup RSS"
    normalized["summary"] = normalized.get("summary") or normalized.get("description") or normalized.get("content_excerpt") or ""
    normalized["impact_note"] = normalized.get("impact_note") or ""
    normalized["source_type"] = "backup"
    normalized["canonical_url"] = canonicalize_url(normalized["original_url"])
    normalized["title_hash"] = title_hash(normalized.get("title"))
    normalized["item_key"] = item_key(normalized)
    return normalized


def load_sheet_items(sheet_url, limit=None, session=None):
    return load_sheet_data(sheet_url, limit=limit, session=session).get("items", [])


def load_sheet_data(sheet_url, limit=None, session=None):
    snapshot = load_sheet_snapshot(sheet_url, session=session)
    if snapshot["protocol_version"] == "legacy" and snapshot["run_marker"] and not snapshot["run_label"]:
        sheet_run_label(snapshot["run_marker"])
    if limit is not None:
        snapshot["items"] = snapshot["items"][: max(1, int(limit))]
    return snapshot


def empty_sheet_snapshot():
    return {
        "protocol_version": "",
        "started_at": "",
        "completed_at": "",
        "run_id": "",
        "status": "",
        "row_count": None,
        "row_count_raw": "",
        "error_code": "",
        "run_marker": "",
        "run_label": "",
        "data_row_count": 0,
        "usable_row_count": 0,
        "invalid_row_count": 0,
        "rows": [],
        "items": [],
    }


def load_sheet_snapshot(sheet_url, session=None):
    """Fetch and parse protocol markers and article rows from one CSV response."""
    if not str(sheet_url or "").strip():
        return empty_sheet_snapshot()
    csv_text = fetch_sheet_csv_text(sheet_url, session=session)
    return parse_sheet_snapshot(csv_text)


def fetch_sheet_snapshot(sheet_url, session=None, expected_run_id=None):
    """Compatibility loader that optionally attaches a coordinator evaluation."""
    snapshot = load_sheet_snapshot(sheet_url, session=session)
    if expected_run_id is not None:
        snapshot["evaluation"] = evaluate_sheet_snapshot(snapshot, expected_run_id)
    return snapshot


def fetch_sheet_csv_text(sheet_url, session=None):
    session = session or requests.Session()
    csv_url = sheet_csv_export_url(sheet_url)
    response = session.get(csv_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.content.decode("utf-8-sig")


def parse_sheet_csv(csv_text):
    snapshot = parse_sheet_snapshot(csv_text)
    return snapshot["run_marker"], snapshot["rows"]


def parse_sheet_snapshot(csv_text):
    """Parse article rows (A:K) and protocol-v1 metadata (L:Q) atomically."""
    raw_rows = list(csv.reader(StringIO(csv_text or "")))
    if not raw_rows:
        return empty_sheet_snapshot()

    header = raw_rows[0]
    marker_values = [
        _cell(header, SHEET_RUN_MARKER_COLUMN_INDEX),
        _cell(header, SHEET_COMPLETED_AT_COLUMN_INDEX),
        _cell(header, SHEET_RUN_ID_COLUMN_INDEX),
        _cell(header, SHEET_STATUS_COLUMN_INDEX),
        _cell(header, SHEET_ROW_COUNT_COLUMN_INDEX),
        _cell(header, SHEET_ERROR_CODE_COLUMN_INDEX),
    ]
    started_at, completed_at, run_id, status, row_count_raw, error_code = marker_values
    rows = _sheet_data_rows(raw_rows)
    items = []
    for index, row in enumerate(rows, start=1):
        item = sheet_row_to_item(row, index)
        if item:
            items.append(item)

    protocol_version = _sheet_protocol_version(marker_values)
    run_label = _snapshot_run_label(run_id, started_at)
    row_count = int(row_count_raw) if re.fullmatch(r"\d+", row_count_raw) else None
    snapshot = {
        "protocol_version": protocol_version,
        "started_at": started_at,
        "completed_at": completed_at,
        "run_id": run_id,
        "status": status.upper(),
        "row_count": row_count,
        "row_count_raw": row_count_raw,
        "error_code": error_code,
        # Legacy names remain available to existing GUI and diagnostics code.
        "run_marker": started_at,
        "run_label": run_label,
        "data_row_count": len(rows),
        "usable_row_count": len(items),
        "invalid_row_count": len(rows) - len(items),
        "rows": rows,
        "items": items,
    }
    return snapshot


def evaluate_sheet_snapshot(snapshot, expected_run_id=None):
    """Return coordinator readiness without trusting stale or partial Sheet data."""
    snapshot = snapshot or {}
    run_id = str(snapshot.get("run_id") or "").strip()
    status = str(snapshot.get("status") or "").strip().upper()
    expected = str(expected_run_id or "").strip()
    base = {
        "state": "invalid",
        "reason": "snapshot_invalid",
        "valid": False,
        "ready": False,
        "terminal": False,
        "run_id": run_id,
        "expected_run_id": expected,
        "status": status,
        "errors": [],
    }

    if snapshot.get("protocol_version") != "v1":
        base["reason"] = "protocol_not_v1"
        base["errors"] = ["Sheet snapshot does not contain protocol-v1 markers in L1:Q1."]
        return base

    run_match = SHEET_RUN_ID_PATTERN.fullmatch(run_id)
    if not run_match:
        base["reason"] = "invalid_run_id"
        base["errors"] = ["N1 must use run_id YYYY-MM-DD:morning|evening."]
        return base
    if expected and not SHEET_RUN_ID_PATTERN.fullmatch(expected):
        base["reason"] = "invalid_expected_run_id"
        base["errors"] = ["Expected run_id must use YYYY-MM-DD:morning|evening."]
        return base
    if expected and run_id != expected:
        base.update(state="waiting", reason="run_id_mismatch", valid=True)
        return base

    errors = []
    started = _parse_aware_iso_timestamp(snapshot.get("started_at"), "L1 started_at", errors, required=True)
    completed = _parse_aware_iso_timestamp(
        snapshot.get("completed_at"),
        "M1 completed_at",
        errors,
        required=status == "COMPLETED",
    )
    if status not in SHEET_PROTOCOL_STATUSES:
        errors.append("O1 status must be RUNNING, COMPLETED, or FAILED.")
    if started:
        vietnam_started = started.astimezone(VIETNAM_TIMEZONE)
        if vietnam_started.date().isoformat() != run_match.group("date"):
            errors.append("N1 run_id date must match the Vietnam date in L1 started_at.")
        expected_slot = "morning" if vietnam_started.hour < 12 else "evening"
        if expected_slot != run_match.group("slot"):
            errors.append("N1 run_id slot must match the Vietnam time in L1 started_at.")
    if started and completed and completed < started:
        errors.append("M1 completed_at cannot be earlier than L1 started_at.")

    row_count_raw = snapshot.get("row_count_raw")
    if row_count_raw is None:
        row_count_raw = snapshot.get("row_count")
    row_count_text = str(row_count_raw if row_count_raw is not None else "").strip()
    row_count = int(row_count_text) if re.fullmatch(r"\d+", row_count_text) else None
    error_code = str(snapshot.get("error_code") or "").strip()

    if status == "RUNNING":
        if str(snapshot.get("completed_at") or "").strip():
            errors.append("M1 completed_at must be empty while O1 is RUNNING.")
        if row_count_text:
            errors.append("P1 row_count must be empty while O1 is RUNNING.")
        if error_code:
            errors.append("Q1 error_code must be empty while O1 is RUNNING.")
    elif status == "COMPLETED":
        if row_count is None:
            errors.append("P1 row_count must be a non-negative integer when O1 is COMPLETED.")
        else:
            data_count = int(snapshot.get("data_row_count", len(snapshot.get("rows") or [])))
            usable_count = int(snapshot.get("usable_row_count", len(snapshot.get("items") or [])))
            if row_count != data_count:
                errors.append(f"P1 row_count is {row_count}, but the snapshot contains {data_count} data rows.")
            if row_count != usable_count:
                errors.append(f"P1 row_count is {row_count}, but only {usable_count} rows are usable.")
        if error_code:
            errors.append("Q1 error_code must be empty when O1 is COMPLETED.")
    elif status == "FAILED" and not error_code:
        errors.append("Q1 error_code is required when O1 is FAILED.")

    if errors:
        base["errors"] = errors
        return base

    base["valid"] = True
    if status == "COMPLETED":
        base.update(state="ready", reason="completed", ready=True, terminal=True)
    elif status == "FAILED":
        base.update(state="failed", reason=error_code, terminal=True)
    else:
        base.update(state="waiting", reason="run_in_progress")
    return base


def validate_sheet_snapshot(snapshot, expected_run_id=None):
    """Alias retained for callers that use validation-oriented naming."""
    return evaluate_sheet_snapshot(snapshot, expected_run_id)


def _cell(row, index):
    return str(row[index] if index < len(row) else "").strip()


def _sheet_data_rows(raw_rows):
    headers = [_cell(raw_rows[0], index) for index in range(min(SHEET_DATA_COLUMN_COUNT, len(raw_rows[0])))]
    rows = []
    for values in raw_rows[1:]:
        data_values = [_cell(values, index) for index in range(SHEET_DATA_COLUMN_COUNT)]
        if not any(data_values):
            continue
        rows.append({header: data_values[index] for index, header in enumerate(headers) if header})
    return rows


def _sheet_protocol_version(marker_values):
    started_at, completed_at, run_id, status, row_count, error_code = marker_values
    if any((completed_at, run_id, status, row_count, error_code)) or ISO_DATE_TIME_PREFIX_PATTERN.match(started_at):
        return "v1"
    return "legacy" if started_at else ""


def _snapshot_run_label(run_id, started_at):
    match = SHEET_RUN_ID_PATTERN.fullmatch(str(run_id or "").strip())
    if match:
        return match.group("slot")
    try:
        return sheet_run_label(started_at) if str(started_at or "").strip() else ""
    except ValueError:
        return ""


def _parse_aware_iso_timestamp(value, field_name, errors, required=False):
    text = str(value or "").strip()
    if not text:
        if required:
            errors.append(f"{field_name} is required and must be ISO-8601 with a timezone.")
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field_name} must be ISO-8601 with a timezone.")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(f"{field_name} must include a timezone offset.")
        return None
    return parsed


def get_sheet_run_status(sheet_url, session=None):
    snapshot = load_sheet_snapshot(sheet_url, session=session)
    if snapshot["protocol_version"] == "legacy" and snapshot["run_marker"] and not snapshot["run_label"]:
        sheet_run_label(snapshot["run_marker"])
    return {
        "run_marker": snapshot["run_marker"],
        "run_label": snapshot["run_label"],
        "started_at": snapshot["started_at"],
        "completed_at": snapshot["completed_at"],
        "run_id": snapshot["run_id"],
        "status": snapshot["status"],
        "row_count": snapshot["row_count"],
        "error_code": snapshot["error_code"],
        "protocol_version": snapshot["protocol_version"],
    }


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
    if not title or not _valid_http_url(original_url):
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
        "backup_total": sum(1 for item in items if item.get("source_type") == "backup"),
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
    return filter_publishable_items(items, db_path=db_path)


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


def _valid_http_url(value):
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


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


def write_json_atomic(path, payload):
    """Write JSON beside its destination and atomically replace the old brief."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


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
        f"Backup items: {stats.get('backup_total', 0)}",
        (
            f"Backup status: {stats.get('backup_status', 'not_used')} "
            f"(ok={stats.get('backup_ok_sources', 0)}, failed={stats.get('backup_failed_sources', 0)})"
        ),
        f"Fallback reason: {stats.get('fallback_reason', '') or 'none'}",
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
        fallback_reason = stats.get("fallback_reason")
        if fallback_reason and int(stats.get("backup_total") or 0) == 0:
            reason = f"Google Sheet unavailable ({fallback_reason}) and the backup lane returned no usable articles."
        elif not sheet_source.get("sheet_url"):
            reason = "Google Sheet URL is empty. Select Sheet mode and paste the Google Sheet link."
        elif int(sheet_source.get("loaded_items") or 0) == 0:
            reason = "No usable rows loaded from the Google Sheet link."

    return "\n\n".join([reason, format_combined_stats(stats, brief_path)])
