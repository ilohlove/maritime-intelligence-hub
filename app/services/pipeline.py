from pathlib import Path
import time

from app.config import ROOT_DIR
from app.services.ai_processor import summarize_pending_articles
from app.services.brief_generator import generate_source_readiness_brief
from app.services.brief_writer import generate_brief, generate_scan_brief
from app.services.html_collector import fetch_html_sources, run_html_dry_run
from app.services.rss_collector import fetch_rss_sources
from app.services.scoring import score_pending_articles
from app.services.source_master import (
    format_fetch_plan,
    format_validation_report,
    get_fetch_plan,
    load_sources,
    validate_source_master,
)
from app.services.storage import DEFAULT_DB_PATH, init_db, list_active_sources, sync_sources
from app.services.trend_collector import fetch_google_trends_rss, import_trends_csv, seed_default_trends


DEFAULT_SOURCE_MASTER = ROOT_DIR / "NEWS_SOURCE_MASTER.csv"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "output"


def validate_sources(source_master=DEFAULT_SOURCE_MASTER):
    result = validate_source_master(source_master)
    return result, format_validation_report(result)


def build_fetch_plan(source_master=DEFAULT_SOURCE_MASTER, priority="P1"):
    plan = get_fetch_plan(source_master, priority=priority)
    return plan, format_fetch_plan(plan)


def sync_source_master(source_master=DEFAULT_SOURCE_MASTER, db_path=DEFAULT_DB_PATH):
    result = validate_source_master(source_master)
    if not result.ok:
        return result, 0

    rows, _ = load_sources(source_master)
    synced_count = sync_sources(rows, db_path=db_path)
    return result, synced_count


def fetch_rss(priority="P1", limit=10, source_master=DEFAULT_SOURCE_MASTER, db_path=DEFAULT_DB_PATH):
    result, synced_count = sync_source_master(source_master, db_path=db_path)
    if not result.ok:
        return {"ok": False, "validation": result, "synced_sources": synced_count, "results": []}

    sources = list_active_sources(db_path=db_path, priority=priority, include_partial=True)
    fetch_results = fetch_rss_sources(sources, limit_per_source=limit, db_path=db_path)
    return {
        "ok": True,
        "validation": result,
        "synced_sources": synced_count,
        "results": fetch_results,
    }


def html_dry_run(priority="P1", source_master=DEFAULT_SOURCE_MASTER, db_path=DEFAULT_DB_PATH):
    result, synced_count = sync_source_master(source_master, db_path=db_path)
    if not result.ok:
        return {"ok": False, "validation": result, "synced_sources": synced_count, "results": []}

    plan = get_fetch_plan(source_master, priority=priority)
    dry_results = run_html_dry_run(plan["html"], db_path=db_path)
    return {
        "ok": True,
        "validation": result,
        "synced_sources": synced_count,
        "results": dry_results,
    }


def fetch_html(priority="P1", limit=5, source_master=DEFAULT_SOURCE_MASTER, db_path=DEFAULT_DB_PATH):
    result, synced_count = sync_source_master(source_master, db_path=db_path)
    if not result.ok:
        return {"ok": False, "validation": result, "synced_sources": synced_count, "results": []}

    plan = get_fetch_plan(source_master, priority=priority)
    fetch_results = fetch_html_sources(plan["html"], limit_per_source=limit, db_path=db_path)
    return {
        "ok": True,
        "validation": result,
        "synced_sources": synced_count,
        "results": fetch_results,
    }


def score_articles(db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    return score_pending_articles(db_path=db_path)


def refresh_trends(csv_path=None, timeframe="24h", fetch_google=False, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    seeded = seed_default_trends(db_path=db_path)
    imported = import_trends_csv(csv_path, timeframe=timeframe, db_path=db_path) if csv_path else 0
    fetched = 0
    fetch_error = None
    if fetch_google:
        try:
            fetched = fetch_google_trends_rss(timeframe=timeframe, db_path=db_path)
        except Exception as exc:
            fetch_error = str(exc)
    return {
        "seeded": seeded,
        "imported": imported,
        "fetched": fetched,
        "fetch_error": fetch_error,
    }


def summarize_articles(db_path=DEFAULT_DB_PATH, min_score=6, force=False, limit=None):
    init_db(db_path)
    return summarize_pending_articles(db_path=db_path, min_score=min_score, force=force, limit=limit)


def write_brief(brief_type, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    return generate_brief(brief_type, db_path=db_path)


def write_scan_brief(scan_label="morning", db_path=DEFAULT_DB_PATH, limit=12):
    init_db(db_path)
    return generate_scan_brief(scan_label=scan_label, db_path=db_path, limit=limit)


def run_pipeline(
    priority="P1",
    limit=10,
    source_master=DEFAULT_SOURCE_MASTER,
    db_path=DEFAULT_DB_PATH,
    scan_label="morning",
    brief_limit=12,
    min_score=6,
    force_summary=False,
    retry_attempts=1,
):
    retry_attempts = max(1, int(retry_attempts or 1))
    steps = []

    trend_result = _run_step(
        "refresh_trends",
        lambda: refresh_trends(db_path=db_path),
        retry_attempts=retry_attempts,
        steps=steps,
    )
    fetch_result = _run_step(
        "fetch_rss",
        lambda: fetch_rss(priority=priority, limit=limit, source_master=source_master, db_path=db_path),
        retry_attempts=retry_attempts,
        steps=steps,
    )
    if not fetch_result["ok"]:
        return {
            "ok": False,
            "fetch": fetch_result,
            "html_fetch": {"results": []},
            "scored": [],
            "summaries": [],
            "brief": None,
            "trends": trend_result,
            "steps": steps,
        }

    html_result = _run_step(
        "fetch_html",
        lambda: fetch_html(priority=priority, limit=limit, source_master=source_master, db_path=db_path),
        retry_attempts=retry_attempts,
        steps=steps,
    )
    scored = _run_step(
        "score_articles",
        lambda: score_articles(db_path=db_path),
        retry_attempts=retry_attempts,
        steps=steps,
    )
    summaries = _run_step(
        "summarize_articles",
        lambda: summarize_articles(db_path=db_path, min_score=min_score, force=force_summary, limit=brief_limit),
        retry_attempts=retry_attempts,
        steps=steps,
    )
    brief = _run_step(
        "write_scan_brief",
        lambda: write_scan_brief(scan_label=scan_label, db_path=db_path, limit=brief_limit),
        retry_attempts=retry_attempts,
        steps=steps,
    )

    return {
        "ok": True,
        "fetch": fetch_result,
        "html_fetch": html_result,
        "scored": scored,
        "summaries": summaries,
        "brief": brief,
        "trends": trend_result,
        "steps": steps,
    }


def _run_step(name, action, retry_attempts=1, steps=None):
    steps = steps if steps is not None else []
    last_error = None
    for attempt in range(1, retry_attempts + 1):
        try:
            result = action()
            steps.append({"name": name, "attempt": attempt, "status": "ok"})
            return result
        except Exception as exc:
            last_error = exc
            steps.append({"name": name, "attempt": attempt, "status": "error", "message": str(exc)})
            if attempt < retry_attempts:
                time.sleep(min(30, 2 * attempt))
    raise last_error


def generate_readiness_brief(
    source_master=DEFAULT_SOURCE_MASTER,
    output_dir=DEFAULT_OUTPUT_DIR,
    priority="P1",
):
    result = validate_source_master(source_master)
    if not result.ok:
        return result, None, format_validation_report(result)

    plan = get_fetch_plan(source_master, priority=priority)
    brief = generate_source_readiness_brief(plan)

    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    brief_path = output_path / "source_readiness_brief.md"
    brief_path.write_text(brief, encoding="utf-8")

    return result, brief_path, brief
