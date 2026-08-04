"""Manual, isolated program-news test runs."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import ROOT_DIR
from app.services.combined_brief_source import (
    DEFAULT_BACKUP_FEED_MASTER,
    build_combined_brief,
    format_empty_combined_message,
    validate_brief_payload,
    write_json_atomic,
)
from app.services.visual_brief_renderer import generate_image_cards


DEFAULT_PROGRAM_TEST_ROOT = ROOT_DIR / "output" / "previews" / "program_tests"


def run_program_news_test(
    *,
    limit_per_source=10,
    card_limit=12,
    exclude_vietnam=False,
    output_root=DEFAULT_PROGRAM_TEST_ROOT,
    backup_feed_master=DEFAULT_BACKUP_FEED_MASTER,
    style_settings=None,
    open_preview=False,
):
    """Fetch and render a strict-quality preview without scheduled-run side effects."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    test_id = f"test-{stamp}-{uuid4().hex[:8]}"
    preview_dir = Path(output_root) / test_id
    brief_path = preview_dir / "combined_brief.json"
    db_path = preview_dir / "test.db"

    source_result = build_combined_brief(
        source_mode="sheet",
        sheet_url="",
        card_limit=None,
        brief_path=brief_path,
        db_path=db_path,
        exclude_vietnam=exclude_vietnam,
        backup_feed_master=backup_feed_master,
        allow_backup=True,
        backup_limit_per_source=max(1, int(limit_per_source or 10)),
        run_id=test_id,
        selected_lane="backup",
        production=True,
        execution_mode="test",
        preview_only=True,
        trigger="test",
    )
    source_result.payload.update(
        {
            "test_id": test_id,
            "execution_mode": "test",
            "preview_only": True,
            "trigger": "test",
        }
    )
    source_result.stats.update(
        {
            "test_id": test_id,
            "run_id": test_id,
            "selected_lane": "backup",
            "execution_mode": "test",
            "preview_only": True,
        }
    )
    source_result.payload["stats"] = source_result.stats

    validation = validate_brief_payload(source_result.payload)
    if source_result.payload.get("items") and not validation["ready"]:
        raise RuntimeError("Program test quality gate failed: " + "; ".join(validation["errors"][:8]))
    write_json_atomic(source_result.brief_path, source_result.payload)

    if not source_result.payload.get("items"):
        stats = source_result.stats
        if stats.get("raw_total", 0) and stats.get("quality_rejected", 0):
            raise RuntimeError(format_empty_combined_message(stats, source_result.brief_path))
        return {
            "test_id": test_id,
            "status": "NO_NEW_CONTENT",
            "brief_path": source_result.brief_path,
            "source_stats": source_result.stats,
            "cards_result": None,
            "db_path": db_path,
        }

    cards_result = generate_image_cards(
        "combined",
        limit=max(1, int(card_limit or 12)),
        output_dir=preview_dir / "visual",
        source_brief_path=source_result.brief_path,
        style_settings=style_settings,
        open_preview=open_preview,
    )
    return {
        "test_id": test_id,
        "status": "SUCCEEDED",
        "brief_path": source_result.brief_path,
        "source_stats": source_result.stats,
        "cards_result": cards_result,
        "db_path": db_path,
    }
