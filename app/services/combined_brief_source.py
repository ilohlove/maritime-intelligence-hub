import csv
import hashlib
import html
import json
import os
import re
import tempfile
from difflib import SequenceMatcher
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from app.config import ROOT_DIR, ensure_runtime_seed
from app.services.ai_processor import AIEnrichmentError, get_ai_provider, summarize_article_strict
from app.services.brief_writer import build_brief_item, validate_publish_items
from app.services.storage import (
    DEFAULT_DB_PATH,
    get_brief_candidate_diagnostics,
    get_brief_candidates,
    list_published_item_keys,
    upsert_summary,
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
TIME_ONLY_PATTERN = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")
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
    run_id="",
    selected_lane="",
    production=False,
    execution_mode="",
    preview_only=None,
    trigger="",
    backup_limit_per_source=None,
):
    session = session or requests.Session()
    source_mode = (source_mode or "combined").strip().lower()
    use_app = source_mode in {"app", "combined"}
    use_sheet = source_mode in {"sheet", "combined"}
    primary_sheet = bool(production and selected_lane == "primary")

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
    if use_sheet and (sheet_data.get("marker_mode") or sheet_data.get("started_at")):
        sheet_evaluation = (
            dict(sheet_data.get("evaluation") or {})
            if primary_sheet and sheet_data.get("evaluation")
            else evaluate_sheet_snapshot(
                sheet_data,
                expected_run_id=expected_run_id or sheet_data.get("run_id"),
            )
        )
        sheet_data["evaluation"] = sheet_evaluation
        if sheet_evaluation["state"] != "ready" and primary_sheet:
            sheet_data["items"] = []
    sheet_items = sheet_data.get("items", []) if use_sheet else []
    if effective_sheet_limit is not None:
        sheet_items = sheet_items[: max(1, int(effective_sheet_limit))]
    if normalize_filter_flag(exclude_vietnam):
        if not primary_sheet:
            sheet_items = [item for item in sheet_items if not vietnam_source_reason(item)]
        app_items = [item for item in app_items if not vietnam_source_reason(item)]
    backup_items = []
    backup_results = []
    fallback_reason = ""
    protocol_blocks_automatic_backup = bool(
        sheet_data.get("marker_mode")
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
                limit_per_source=backup_limit_per_source or effective_sheet_limit or 10,
                db_path=db_path,
                session=session,
                exclude_vietnam=exclude_vietnam,
            )
            for result in backup_results:
                backup_items.extend(result.get("items") or [])
        except Exception as exc:
            fallback_reason = f"{fallback_reason}; backup_error={exc}"
    normalized_backup_items = [_backup_item_to_brief(item) for item in backup_items]
    stale_backup_removed = 0
    if production and selected_lane == "backup":
        normalized_backup_items, stale_backup_removed = filter_recent_backup_items(normalized_backup_items)
    raw_items = app_items + sheet_items + normalized_backup_items
    if primary_sheet:
        filtered_items, stats = filter_primary_sheet_items(sheet_items, db_path=db_path)
    else:
        filtered_items, stats = filter_publishable_items(raw_items, db_path=db_path)
    if source_mode == "app" and not production:
        for item in filtered_items:
            item["editorial_score"] = calculate_editorial_score(item)
        filtered_items = sorted(
            filtered_items,
            key=lambda item: (int(item.get("editorial_score") or 0), str(item.get("published_at") or "")),
            reverse=True,
        )
    if primary_sheet:
        for item in filtered_items:
            item["quality_status"] = "accepted"
            item["quality_errors"] = []
            item["ai_provider"] = item.get("ai_provider") or "sheet"
            item["editorial_score"] = calculate_editorial_score(item)
        stats.update(
            {
                "enriched_total": 0,
                "quality_rejected": 0,
                "quality_rejections": [],
                "quality_gate_ready": bool(filtered_items),
            }
        )
    elif production:
        filtered_items, quality_stats = enrich_brief_items(
            filtered_items,
            db_path=db_path,
            production=True,
        )
        stats.update(quality_stats)
        filtered_items, semantic_removed = dedupe_similar_items(filtered_items)
        stats["duplicate_removed"] += semantic_removed
        stats["selected_total"] = len(filtered_items)
        if selected_lane == "backup":
            filtered_items = rank_backup_items(filtered_items)
        elif selected_lane == "primary":
            filtered_items = sorted(
                filtered_items,
                key=lambda item: int(item.get("source_rank") or item.get("row_index") or 999999),
            )
        stats["selected_total"] = len(filtered_items)
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
    stats["backup_stale_removed"] = stale_backup_removed
    effective_execution_mode = execution_mode or ("scheduled" if production else "preview")
    stats["execution_mode"] = effective_execution_mode
    stats["trigger"] = trigger or ""
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
        sheet_diagnostics["marker_mode"] = sheet_data.get("marker_mode", "")
        sheet_diagnostics["content_hash"] = sheet_data.get("content_hash", "")
        sheet_diagnostics["snapshot_hash"] = sheet_data.get("snapshot_hash", "")
        sheet_diagnostics["row_errors"] = list(sheet_data.get("row_errors") or [])
        sheet_diagnostics["evaluation"] = sheet_evaluation
        stats["sheet_source"] = sheet_diagnostics
    effective_card_limit = None if source_mode == "sheet" else card_limit
    selected_items = filtered_items if effective_card_limit is None else filtered_items[: max(1, int(effective_card_limit))]
    stats["output_total"] = len(selected_items)
    for item in selected_items:
        item["why_important"] = item.get("impact_note") or ""

    payload = {
        "brief_type": "combined",
        "scan_label": "combined",
        "title": "Maritime Intelligence Hub - Combined Brief",
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "source_mode": source_mode,
        "run_id": run_id or "",
        "selected_lane": selected_lane or "",
        "preview_only": (bool(preview_only) if preview_only is not None else not production),
        "execution_mode": effective_execution_mode,
        "trigger": trigger or "",
        "stats": stats,
        "publish_safety": validate_publish_items(selected_items, strict=production),
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


def filter_recent_backup_items(items, *, now=None, max_age_hours=48):
    """Keep only backup items with a usable publication time in the daily window."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    kept = []
    removed = 0
    for item in items or []:
        value = str(item.get("published_at") or "").strip().replace("Z", "+00:00")
        try:
            published = datetime.fromisoformat(value)
            if published.tzinfo is None or published.utcoffset() is None:
                raise ValueError
            age_hours = (current - published.astimezone(timezone.utc)).total_seconds() / 3600
        except (TypeError, ValueError):
            removed += 1
            continue
        if age_hours < -1 or age_hours > max_age_hours:
            removed += 1
            continue
        kept.append(item)
    return kept, removed


def enrich_brief_items(items, db_path=DEFAULT_DB_PATH, production=False):
    """Ensure every production item has Vietnamese editorial fields."""
    result = []
    rejected = []
    needs_ai = [item for item in items if not is_valid_vietnamese_item(item)]
    provider = None
    provider_error = ""
    if needs_ai:
        try:
            provider = get_ai_provider(allow_mock=not production)
        except Exception as exc:
            provider_error = str(exc)

    for item in items:
        candidate = dict(item)
        if is_valid_vietnamese_item(candidate):
            candidate["quality_status"] = "accepted"
            candidate.setdefault("quality_errors", [])
            candidate["ai_provider"] = candidate.get("ai_provider") or "sheet"
            candidate["editorial_score"] = calculate_editorial_score(candidate)
            result.append(candidate)
            continue

        try:
            if provider is None:
                raise AIEnrichmentError(provider_error or "No AI provider available")
            article = {
                "id": candidate.get("id") or candidate.get("article_id") or 0,
                "source_name": candidate.get("source_name") or "Backup RSS",
                "category": candidate.get("category") or "Maritime",
                "title": candidate.get("title") or "",
                "url": candidate.get("original_url") or candidate.get("url") or "",
                "description": candidate.get("description") or candidate.get("summary") or "",
                "content_excerpt": candidate.get("content_excerpt") or "",
                "importance_score": candidate.get("importance_score"),
            }
            summary = summarize_article_strict(provider, article)
            candidate.update(
                {
                    "title": summary["headline"],
                    "summary": summary["summary"],
                    "impact_note": summary["impact_note"],
                    "category": summary.get("category") or candidate.get("category"),
                    "importance_score": summary.get("importance_score") or candidate.get("importance_score"),
                    "commercial_relevance": summary.get("commercial_relevance"),
                    "operational_impact": summary.get("operational_impact"),
                    "vietnam_relevance": summary.get("vietnam_relevance"),
                    "source_name": summary.get("source_name") or candidate.get("source_name"),
                    "original_url": summary.get("original_url") or candidate.get("original_url"),
                    "ai_provider": summary.get("ai_provider"),
                    "model_name": summary.get("model_name"),
                    "fallback_errors": summary.get("fallback_errors") or [],
                    "quality_status": "accepted",
                    "quality_errors": [],
                }
            )
            candidate["canonical_url"] = canonicalize_url(candidate.get("original_url"))
            candidate["title_hash"] = title_hash(candidate.get("title"))
            candidate["item_key"] = item_key(candidate)
            if not is_valid_vietnamese_item(candidate):
                raise ValueError("Vietnamese quality gate rejected AI output")
            candidate["editorial_score"] = calculate_editorial_score(candidate)
            if db_path and isinstance(candidate.get("id"), int) and candidate.get("id"):
                upsert_summary(
                    {
                        "article_id": candidate["id"],
                        "headline": candidate["title"],
                        "summary": candidate["summary"],
                        "impact_note": candidate["impact_note"],
                        "category": candidate.get("category"),
                        "importance_score": candidate.get("importance_score"),
                        "source_name": candidate.get("source_name"),
                        "original_url": candidate.get("original_url"),
                        "prompt_version": summary.get("prompt_version") or "strict-production-v1",
                        "model_name": summary.get("model_name") or "unknown",
                        "token_usage": summary.get("token_usage") or 0,
                        "ai_provider": summary.get("ai_provider"),
                        "image_prompt": summary.get("image_prompt"),
                        "fallback_errors": summary.get("fallback_errors") or [],
                    },
                    db_path=db_path,
                )
            result.append(candidate)
        except Exception as exc:
            candidate["quality_status"] = "rejected"
            candidate["quality_errors"] = [str(exc)[:240]]
            rejected.append(candidate)

    return result, {
        "enriched_total": len(needs_ai),
        "quality_rejected": len(rejected),
        "quality_rejections": [
            {"title": item.get("title"), "errors": item.get("quality_errors") or []}
            for item in rejected[:20]
        ],
        "quality_gate_ready": bool(result),
    }


def is_valid_vietnamese_item(item):
    required = (item.get("title"), item.get("summary"), item.get("impact_note"))
    if any(not str(value or "").strip() for value in required):
        return False
    text = " ".join(str(value) for value in required)
    if any(marker in text for marker in ("Ãƒ", "Ã‚", "Ã¢â", "Ä‘á")):
        return False
    if not all(has_vietnamese_marks(value) for value in required):
        return False
    title_words = re.findall(r"[A-Za-zÀ-ỹĐđ]+", str(item.get("title") or ""))
    if len(title_words) < 3:
        return False
    summary = str(item.get("summary") or "")
    summary_sentences = _split_summary_sentences(summary)
    if not 1 <= len(summary_sentences) <= 4:
        return False
    if len(re.findall(r"[A-Za-zÀ-ỹĐđ]+", summary)) < 12:
        return False
    if len(re.findall(r"[A-Za-zÀ-ỹĐđ]+", str(item.get("impact_note") or ""))) < 8:
        return False
    for sentence in summary_sentences:
        words = re.findall(r"[A-Za-zÀ-ỹĐđ]+", sentence)
        if len(words) >= 7 and not has_vietnamese_marks(sentence):
            return False
    combined = " ".join(str(value).lower() for value in required)
    english_hits = len(re.findall(r"\b(the|and|to|of|is|was|for|on|from|with|as|an)\b", combined))
    vietnamese_hits = len(
        re.findall(r"\b(các|và|là|của|cho|đã|từ|trong|với|khi|tại|sau|trên|được|đang)\b", combined)
    )
    if english_hits >= 3 and english_hits > max(1, vietnamese_hits * 2):
        return False
    return len(str(item.get("summary") or "")) <= 900 and len(str(item.get("impact_note") or "")) <= 600


def _split_summary_sentences(value):
    """Split prose without treating dots inside numeric thousands/decimals as stops."""
    protected = re.sub(r"(?<=\d)\.(?=\d)", "\u0000", str(value or ""))
    return [
        sentence.replace("\u0000", ".").strip()
        for sentence in re.split(r"[.!?]+", protected)
        if sentence.replace("\u0000", ".").strip()
    ]


def validate_brief_payload(
    payload,
    *,
    expected_run_id="",
    expected_lane="",
    require_publishable=False,
):
    """Validate a rendered brief before it can be rendered or delivered."""
    payload = payload or {}
    errors = []
    if expected_run_id and str(payload.get("run_id") or "") != str(expected_run_id):
        errors.append("run_id mismatch")
    if expected_lane and str(payload.get("selected_lane") or "") != str(expected_lane):
        errors.append("selected_lane mismatch")
    if require_publishable and bool(payload.get("preview_only")):
        errors.append("preview_only payload cannot be published")
    items = payload.get("items") or []
    for index, item in enumerate(items, start=1):
        if not item.get("source_name"):
            errors.append(f"Item {index}: missing source_name")
        if not _valid_http_url(item.get("original_url")):
            errors.append(f"Item {index}: invalid original_url")
        if item.get("quality_status") != "accepted":
            errors.append(f"Item {index}: quality status is not accepted")
        if not is_valid_vietnamese_item(item):
            errors.append(f"Item {index}: Vietnamese content quality gate failed")
    safety = validate_publish_items(items, strict=True)
    if not safety.get("ready"):
        errors.extend(safety.get("errors") or [])
    return {"ready": not errors, "errors": errors}


def calculate_editorial_score(item):
    if item.get("source_type") == "sheet":
        return max(1, 1000 - int(item.get("source_rank") or item.get("row_index") or 999))
    text = " ".join(str(item.get(key) or "").lower() for key in ("title", "summary", "impact_note", "category"))
    commercial_terms = ("tàu thương mại", "vận tải", "cảng", "logistics", "chuỗi cung ứng", "giá cước", "bảo hiểm", "xuất khẩu", "nhập khẩu", "container", "lịch tàu")
    impact_terms = ("an toàn", "tấn công", "mắc cạn", "đình trệ", "gián đoạn", "quy định", "chi phí", "thiệt hại", "đóng cửa")
    regional_terms = ("việt nam", "đông nam á", "biển đông", "cái mép", "hải phòng", "singapore", "malaysia", "indonesia")
    commercial = item.get("commercial_relevance")
    impact = item.get("operational_impact")
    regional = item.get("vietnam_relevance")
    commercial = min(4, int(commercial)) if commercial is not None else min(4, sum(term in text for term in commercial_terms))
    impact = min(3, int(impact)) if impact is not None else min(3, sum(term in text for term in impact_terms))
    regional = min(2, int(regional)) if regional is not None else min(2, sum(term in text for term in regional_terms))
    quality = min(10, max(1, int(item.get("content_quality_score") or 8)))
    recency = _editorial_recency_score(item.get("published_at"))
    off_scope = any(term in text for term in ("tàu chiến", "tàu ngầm hạt nhân", "săn cá voi", "tàu du lịch")) and not commercial
    return max(0, commercial * 10 + impact * 8 + recency + quality + regional * 5 - (40 if off_scope else 0))


def _editorial_recency_score(value):
    try:
        text = str(value or "").replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600
    except (TypeError, ValueError):
        return 0
    if age_hours <= 12:
        return 15
    if age_hours <= 24:
        return 10
    if age_hours <= 48:
        return 5
    return 0


def rank_backup_items(items, limit=12):
    ranked = sorted(
        items,
        key=lambda item: (int(item.get("editorial_score") or 0), str(item.get("published_at") or "")),
        reverse=True,
    )
    selected = []
    source_counts = {}
    category_counts = {}
    for item in ranked:
        source = item.get("source_name") or "unknown"
        category = item.get("category") or "Maritime"
        if int(item.get("editorial_score") or 0) < 41:
            continue
        if source_counts.get(source, 0) >= 4 or category_counts.get(category, 0) >= 4:
            continue
        selected.append(item)
        source_counts[source] = source_counts.get(source, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def dedupe_similar_items(items):
    selected = []
    removed = 0
    for item in items:
        normalized = normalize_title(item.get("title"))
        duplicate = next(
            (
                existing
                for existing in selected
                if normalized
                and _similar_story_title(normalized, normalize_title(existing.get("title")))
            ),
            None,
        )
        if duplicate is None:
            selected.append(item)
            continue
        if winner_score(item) > winner_score(duplicate):
            selected[selected.index(duplicate)] = item
        removed += 1
    return selected, removed


def _similar_story_title(left, right):
    if SequenceMatcher(None, left, right).ratio() >= 0.8:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    shared = left_tokens & right_tokens
    anchors = {
        "yemen", "houthi", "houthis", "hormuz", "red", "sea", "black", "ukraine",
        "russia", "iran", "israel", "gaza", "suez", "bab", "mandeb", "gaslog",
    }
    contradictions = (
        ("worsens", "improves"),
        ("increase", "decrease"),
        ("rises", "falls"),
        ("deny", "confirm"),
    )
    if any((left_word in left_tokens and right_word in right_tokens) or (right_word in left_tokens and left_word in right_tokens) for left_word, right_word in contradictions):
        return False
    return len(shared) >= 4 and bool(shared & anchors)


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
        "marker_mode": "",
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
        "row_errors": [],
        "content_hash": "",
        "snapshot_hash": "",
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
    """Parse article rows (A:K), authoritative L/M markers, and diagnostic N:Q."""
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
    row_errors = []
    for index, row in enumerate(rows, start=1):
        item = sheet_row_to_item(row, index)
        errors = _sheet_row_validation_errors(row, item, index)
        if errors:
            row_errors.extend(errors)
        if item:
            items.append(item)

    marker_mode = _sheet_marker_mode(started_at, completed_at)
    protocol_version = "v1" if marker_mode == "iso" else "legacy" if marker_mode == "legacy_time" else ""
    run_label = _snapshot_run_label(run_id, started_at)
    row_count = int(row_count_raw) if re.fullmatch(r"\d+", row_count_raw) else None
    snapshot = {
        "protocol_version": protocol_version,
        "marker_mode": marker_mode,
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
        "row_errors": row_errors,
        "content_hash": _raw_sheet_content_hash(raw_rows),
        "snapshot_hash": _raw_sheet_snapshot_hash(raw_rows),
        "rows": rows,
        "items": items,
    }
    return snapshot


def evaluate_sheet_snapshot(
    snapshot,
    expected_run_id=None,
    *,
    expected_started_at=None,
    hard_deadline=None,
):
    """Evaluate L1/M1 and every Sheet row; N1:Q1 are diagnostics only."""
    snapshot = snapshot or {}
    run_id = str(snapshot.get("run_id") or "").strip()
    status = str(snapshot.get("status") or "").strip().upper()
    expected = str(expected_run_id or "").strip()
    marker_mode = str(snapshot.get("marker_mode") or "").strip() or _sheet_marker_mode(
        snapshot.get("started_at"), snapshot.get("completed_at")
    )
    base = {
        "state": "invalid",
        "reason": "snapshot_invalid",
        "valid": False,
        "ready": False,
        "terminal": False,
        "run_id": run_id,
        "expected_run_id": expected,
        "status": status,
        "marker_mode": marker_mode,
        "content_hash": sheet_content_fingerprint(snapshot),
        "snapshot_hash": sheet_snapshot_fingerprint(snapshot),
        "row_count": int(snapshot.get("data_row_count") or 0),
        "errors": [],
        "diagnostics": [],
    }

    if marker_mode not in {"iso", "legacy_time"}:
        base["reason"] = "invalid_l1_m1_markers"
        base["errors"] = ["L1/M1 must both use timezone-aware ISO timestamps or HH:MM."]
        return base

    run_match = SHEET_RUN_ID_PATTERN.fullmatch(run_id)
    expected_match = SHEET_RUN_ID_PATTERN.fullmatch(expected)
    if run_id and not run_match:
        base["diagnostics"].append("N1 run_id is malformed; ignored for lane selection.")
    elif not run_id:
        base["diagnostics"].append("N1 run_id is empty; ignored for lane selection.")
    elif expected and run_id != expected:
        base["diagnostics"].append("N1 run_id differs from the expected run; L1/M1 remain authoritative.")
    if expected and not expected_match:
        base["reason"] = "invalid_expected_run_id"
        base["errors"].append("Expected run_id must use YYYY-MM-DD:morning|evening.")
        return base

    errors = list(base.get("errors") or [])
    expected_start = _expected_sheet_start(expected_match, expected_started_at, errors)
    started = _parse_sheet_marker(
        snapshot.get("started_at"), marker_mode, "L1 started_at", errors, expected_start, required=True
    )
    completed = _parse_sheet_marker(
        snapshot.get("completed_at"), marker_mode, "M1 completed_at", errors, expected_start, required=False
    )

    if started and expected_start:
        local_started = started.astimezone(VIETNAM_TIMEZONE)
        local_expected = expected_start.astimezone(VIETNAM_TIMEZONE)
        if local_started.date() != local_expected.date() or (
            local_started.hour,
            local_started.minute,
        ) != (local_expected.hour, local_expected.minute):
            base.update(
                state="waiting",
                reason="l1_slot_mismatch",
                valid=True,
                errors=[
                    f"L1 is {local_started.strftime('%Y-%m-%d %H:%M')}; "
                    f"expected {local_expected.strftime('%Y-%m-%d %H:%M')}."
                ],
            )
            return base
    if started and completed and completed < started:
        errors.append("M1 completed_at cannot be earlier than L1 started_at.")

    _append_protocol_diagnostics(snapshot, completed is not None, base["diagnostics"])

    if hard_deadline and completed:
        deadline = _coerce_aware_datetime(hard_deadline, errors, "hard deadline")
        if deadline and completed.astimezone(timezone.utc) > deadline.astimezone(timezone.utc):
            errors.append("M1 completed_at is later than the hard deadline.")

    if completed:
        data_count = int(snapshot.get("data_row_count", len(snapshot.get("rows") or [])))
        usable_count = int(snapshot.get("usable_row_count", len(snapshot.get("items") or [])))
        row_errors = list(snapshot.get("row_errors") or [])
        if not row_errors:
            for index, item in enumerate(snapshot.get("items") or [], start=1):
                if not str(item.get("source_name") or "").strip():
                    row_errors.append(f"Row {item.get('row_index') or index}: missing source.")
                if not _valid_http_url(item.get("original_url")):
                    row_errors.append(f"Row {item.get('row_index') or index}: invalid URL.")
                if not is_valid_vietnamese_item(item):
                    row_errors.append(
                        f"Row {item.get('row_index') or index}: Vietnamese title, summary, or impact does not pass the quality gate."
                    )
        if data_count <= 0:
            errors.append("Sheet contains no article rows.")
        if usable_count != data_count:
            errors.append(f"Only {usable_count} of {data_count} Sheet rows are valid.")
        errors.extend(str(value) for value in row_errors[:20])

    if errors:
        base["errors"] = errors
        base["reason"] = "completed_snapshot_invalid" if completed else "marker_invalid"
        return base

    base["valid"] = True
    base["started_at_utc"] = started.astimezone(timezone.utc).isoformat() if started else ""
    base["completed_at_utc"] = completed.astimezone(timezone.utc).isoformat() if completed else ""
    if completed:
        base.update(state="ready", reason="completed", ready=True, terminal=True)
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


def _sheet_marker_mode(started_at, completed_at):
    started_kind = _marker_kind(started_at)
    completed_kind = _marker_kind(completed_at)
    if started_kind == "iso" and completed_kind in {"", "iso"}:
        return "iso"
    if started_kind == "time" and completed_kind in {"", "time"}:
        return "legacy_time"
    return ""


def _marker_kind(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if ISO_DATE_TIME_PREFIX_PATTERN.match(text):
        return "iso"
    return "time" if TIME_ONLY_PATTERN.fullmatch(text) else "invalid"


def _raw_sheet_snapshot_hash(raw_rows):
    if not raw_rows:
        return ""
    header = raw_rows[0]
    payload = {
        "a_to_k": _raw_sheet_content_matrix(raw_rows),
        "l_to_m": [
            _normalized_hash_cell(_cell(header, SHEET_RUN_MARKER_COLUMN_INDEX)),
            _normalized_hash_cell(_cell(header, SHEET_COMPLETED_AT_COLUMN_INDEX)),
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _raw_sheet_content_hash(raw_rows):
    if not raw_rows:
        return ""
    encoded = json.dumps(
        _raw_sheet_content_matrix(raw_rows),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _raw_sheet_content_matrix(raw_rows):
    matrix = [
        [_normalized_hash_cell(_cell(raw_rows[0], index)) for index in range(SHEET_DATA_COLUMN_COUNT)]
    ]
    for values in raw_rows[1:]:
        data_values = [_normalized_hash_cell(_cell(values, index)) for index in range(SHEET_DATA_COLUMN_COUNT)]
        if any(data_values):
            matrix.append(data_values)
    return matrix


def sheet_snapshot_fingerprint(snapshot):
    if not snapshot:
        return ""
    existing = str((snapshot or {}).get("snapshot_hash") or "").strip()
    if existing:
        return existing
    snapshot = snapshot or {}
    rows = snapshot.get("rows") or snapshot.get("items") or []
    payload = {
        "a_to_k": rows,
        "l_to_m": [
            _normalized_hash_cell(snapshot.get("started_at")),
            _normalized_hash_cell(snapshot.get("completed_at")),
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def sheet_content_fingerprint(snapshot):
    if not snapshot:
        return ""
    existing = str(snapshot.get("content_hash") or "").strip()
    if existing:
        return existing
    rows = snapshot.get("rows") or snapshot.get("items") or []
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalized_hash_cell(value):
    return " ".join(str(value or "").replace("\u00a0", " ").split())


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


def _expected_sheet_start(expected_match, expected_started_at, errors):
    if expected_started_at is not None:
        parsed = _coerce_aware_datetime(expected_started_at, errors, "expected started_at")
        return parsed.astimezone(VIETNAM_TIMEZONE) if parsed else None
    if not expected_match:
        return None
    date_value = datetime.fromisoformat(expected_match.group("date"))
    hour, minute = (7, 15) if expected_match.group("slot") == "morning" else (19, 15)
    return date_value.replace(hour=hour, minute=minute, tzinfo=VIETNAM_TIMEZONE)


def _parse_sheet_marker(value, marker_mode, field_name, errors, expected_start, required=False):
    text = str(value or "").strip()
    if not text:
        if required:
            errors.append(f"{field_name} is required.")
        return None
    if marker_mode == "iso":
        return _parse_aware_iso_timestamp(text, field_name, errors, required=required)
    match = TIME_ONLY_PATTERN.fullmatch(text)
    if not match:
        errors.append(f"{field_name} must use HH:MM.")
        return None
    hour, minute = int(match.group("hour")), int(match.group("minute"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        errors.append(f"{field_name} must use a valid HH:MM time.")
        return None
    anchor = expected_start or datetime.now(VIETNAM_TIMEZONE)
    return anchor.astimezone(VIETNAM_TIMEZONE).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def _coerce_aware_datetime(value, errors, field_name):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            errors.append(f"{field_name} must be an ISO datetime.")
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=VIETNAM_TIMEZONE)
    return parsed


def _append_protocol_diagnostics(snapshot, completed, diagnostics):
    run_id = str(snapshot.get("run_id") or "").strip()
    status = str(snapshot.get("status") or "").strip().upper()
    row_count_raw = snapshot.get("row_count_raw")
    error_code = str(snapshot.get("error_code") or "").strip()
    expected_status = "COMPLETED" if completed else "RUNNING"
    if not run_id:
        diagnostics.append("N1 is empty.")
    if status != expected_status:
        diagnostics.append(f"O1 is {status or 'empty'}; L1/M1 indicate {expected_status}.")
    if str(row_count_raw or "").strip():
        expected_rows = int(snapshot.get("data_row_count") or 0)
        if not re.fullmatch(r"\d+", str(row_count_raw).strip()) or int(row_count_raw) != expected_rows:
            diagnostics.append("P1 row_count does not match A:K; ignored.")
    elif completed:
        diagnostics.append("P1 is empty; ignored.")
    if error_code:
        diagnostics.append("Q1 contains an error code; ignored for lane selection.")


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
    title = first_value(row, "Vietnamese translation")
    summary = first_value(row, "Main summary (Vietnamese)")
    impact_note = first_value(row, "Why it matters (Vietnamese)")
    original_url = normalize_source_url(first_value(row, "Source URL"))
    source_name = first_value(row, "Source")
    if not title or not _valid_http_url(original_url):
        return None

    item = {
        "title": title,
        "source_title": first_value(row, "Headline"),
        "published_at": first_value(row, "Date"),
        "summary": summary,
        "source_summary": first_value(row, "Main summary"),
        "impact_note": impact_note,
        "source_impact_note": first_value(row, "Why it matters"),
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


def _sheet_row_validation_errors(row, item, index):
    required = {
        "Vietnamese title": first_value(row, "Vietnamese translation"),
        "Vietnamese summary": first_value(row, "Main summary (Vietnamese)"),
        "Vietnamese impact": first_value(row, "Why it matters (Vietnamese)"),
        "source": first_value(row, "Source"),
        "URL": normalize_source_url(first_value(row, "Source URL")),
    }
    errors = [f"Row {index}: missing {name}." for name, value in required.items() if not str(value or "").strip()]
    if required["URL"] and not _valid_http_url(required["URL"]):
        errors.append(f"Row {index}: invalid URL.")
    if not errors and (not item or not is_valid_vietnamese_item(item)):
        errors.append(f"Row {index}: Vietnamese title, summary, or impact does not pass the quality gate.")
    return errors


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


def filter_primary_sheet_items(items, db_path=DEFAULT_DB_PATH):
    """Keep Sheet order and only suppress already-published rows or repeated URLs."""
    published = published_lookup(db_path=db_path)
    selected = []
    skipped = []
    seen_urls = set()
    for item in items or []:
        row_index = item.get("row_index")
        if (
            item.get("item_key") in published["keys"]
            or (
                item.get("canonical_url")
                and item.get("canonical_url") in published["urls"]
            )
        ):
            skipped.append(
                {
                    "row_index": row_index,
                    "reason": "already_published_exact",
                    "title": item.get("title") or "",
                    "url": item.get("original_url") or "",
                }
            )
            continue
        canonical_url = item.get("canonical_url") or canonicalize_url(item.get("original_url"))
        if canonical_url and canonical_url in seen_urls:
            skipped.append(
                {
                    "row_index": row_index,
                    "reason": "duplicate_url_in_sheet",
                    "title": item.get("title") or "",
                    "url": item.get("original_url") or "",
                }
            )
            continue
        if canonical_url:
            seen_urls.add(canonical_url)
        selected.append(item)

    already_published = sum(1 for item in skipped if item["reason"] == "already_published_exact")
    duplicate_removed = len(skipped) - already_published
    return selected, {
        "app_total": 0,
        "sheet_total": len(items or []),
        "backup_total": 0,
        "raw_total": len(items or []),
        "already_published": already_published,
        "duplicate_removed": duplicate_removed,
        "eligible_total": len(selected),
        "selected_total": len(selected),
        "duplicate_groups": [],
        "sheet_skipped_rows": skipped,
    }


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
        f"Run ID: {stats.get('run_id', '') or 'preview'}",
        f"Selected lane: {stats.get('selected_lane', '') or 'preview'}",
        f"App items: {stats.get('app_total', 0)}",
        f"Sheet items: {stats.get('sheet_total', 0)}",
        f"Raw items: {stats.get('raw_total', 0)}",
        f"Already published removed: {stats.get('already_published', 0)}",
        f"Duplicate removed: {stats.get('duplicate_removed', 0)}",
        f"Eligible after published filter: {stats.get('eligible_total', 0)}",
        f"Selected for output: {stats.get('selected_total', 0)}",
        f"AI enriched: {stats.get('enriched_total', 0)}",
        f"Quality rejected: {stats.get('quality_rejected', 0)}",
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
    if stats.get("sheet_skipped_rows"):
        lines.append("")
        lines.append("Skipped Sheet rows:")
        for item in stats["sheet_skipped_rows"]:
            lines.append(
                f"- Row {item.get('row_index')}: {item.get('reason')} — {item.get('title') or item.get('url')}"
            )
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
