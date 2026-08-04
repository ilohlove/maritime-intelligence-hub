import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

from app.config import ROOT_DIR, validate_runtime_seeds
from app.logger import logger
from app.services.business_logic import (
    build_publish_plan,
    claim_delivery_cards,
    delivery_claim_blockers,
    due_scheduled_slot,
    FacebookGroupDeliveryGuard,
    finish_delivery_cards,
    maintain_news_run_lease,
    maintain_terminal_news_run_lease,
    news_run_directory_name,
    scheduled_datetime,
    select_scheduled_lane,
    update_scheduled_run,
)
from app.services.combined_brief_source import (
    DEFAULT_COMBINED_BRIEF_PATH,
    build_combined_brief,
    format_empty_combined_message,
    format_combined_stats,
    validate_brief_payload,
    write_json_atomic,
)
from app.services.evernote_summarizer import summarize_article_id_with_evernote, summarize_candidates_with_evernote
from app.services.facebook_publisher import publish_photo_post, validate_cards_publish_safety
from app.services.facebook_group_publisher import publish_to_groups, validate_group_config
from app.services.pipeline import (
    DEFAULT_SOURCE_MASTER,
    build_fetch_plan,
    fetch_html,
    fetch_rss,
    generate_readiness_brief,
    html_dry_run,
    refresh_trends,
    run_pipeline,
    score_articles,
    summarize_articles,
    sync_source_master,
    validate_sources,
    write_brief,
)
from app.services.runtime_settings import (
    facebook_brief_label_text,
    load_ai_env,
    load_runtime_settings,
    render_facebook_intro_text,
)
from app.services.storage import (
    DEFAULT_DB_PATH,
    TERMINAL_RUN_STATES,
    claim_terminal_news_run_lease,
    get_news_run,
    list_news_runs,
    list_publish_deliveries,
    mark_items_published,
    release_terminal_news_run_lease,
    resolve_publish_delivery,
)
from app.services.test_runner import run_program_news_test
from app.services.telegram_publisher import send_message, send_photos
from app.services.visual_brief_renderer import generate_image_cards, load_image_cards_result


def run_cli(argv=None):
    _configure_console_encoding()
    parser = argparse.ArgumentParser(
        prog="maritime-intelligence-hub",
        description="Maritime Intelligence Hub command line tools",
    )
    subparsers = parser.add_subparsers(dest="command")

    validate_parser = subparsers.add_parser("validate-sources")
    validate_parser.add_argument("--source-master", default=str(DEFAULT_SOURCE_MASTER))

    plan_parser = subparsers.add_parser("plan-sources")
    plan_parser.add_argument("--source-master", default=str(DEFAULT_SOURCE_MASTER))
    plan_parser.add_argument("--priority", default="P1", choices=["P1", "P2", "P3"])

    brief_parser = subparsers.add_parser("readiness-brief")
    brief_parser.add_argument("--source-master", default=str(DEFAULT_SOURCE_MASTER))
    brief_parser.add_argument("--priority", default="P1", choices=["P1", "P2", "P3"])

    sync_parser = subparsers.add_parser("sync-sources")
    sync_parser.add_argument("--source-master", default=str(DEFAULT_SOURCE_MASTER))

    fetch_parser = subparsers.add_parser("fetch-rss")
    fetch_parser.add_argument("--source-master", default=str(DEFAULT_SOURCE_MASTER))
    fetch_parser.add_argument("--priority", default="P1", choices=["P1", "P2", "P3"])
    fetch_parser.add_argument("--limit", type=int, default=10)

    html_parser = subparsers.add_parser("html-dry-run")
    html_parser.add_argument("--source-master", default=str(DEFAULT_SOURCE_MASTER))
    html_parser.add_argument("--priority", default="P1", choices=["P1", "P2", "P3"])

    fetch_html_parser = subparsers.add_parser("fetch-html")
    fetch_html_parser.add_argument("--source-master", default=str(DEFAULT_SOURCE_MASTER))
    fetch_html_parser.add_argument("--priority", default="P1", choices=["P1", "P2", "P3"])
    fetch_html_parser.add_argument("--limit", type=int, default=5)

    trends_parser = subparsers.add_parser("refresh-trends")
    trends_parser.add_argument("--csv")
    trends_parser.add_argument("--timeframe", default="24h", choices=["24h", "48h", "7d", "seed"])
    trends_parser.add_argument("--fetch-google", action="store_true")

    subparsers.add_parser("score-articles")

    summarize_parser = subparsers.add_parser("summarize-articles")
    summarize_parser.add_argument("--min-score", type=int, default=6)
    summarize_parser.add_argument("--force", action="store_true")
    summarize_parser.add_argument("--limit", type=int)

    evernote_parser = subparsers.add_parser("summarize-evernote")
    evernote_parser.add_argument("--article-id", type=int)
    evernote_parser.add_argument("--limit", type=int, default=3)
    evernote_parser.add_argument("--min-score", type=int, default=8)
    evernote_parser.add_argument("--dry-run", action="store_true")
    evernote_parser.add_argument("--no-save", action="store_true")

    generate_parser = subparsers.add_parser("generate-brief")
    generate_parser.add_argument("--type", required=True, choices=["morning", "evening", "weekly"])

    image_cards_parser = subparsers.add_parser("generate-image-cards")
    image_cards_parser.add_argument("--type", required=True, choices=["morning", "evening", "weekly"])
    image_cards_parser.add_argument("--limit", type=int, default=12)
    image_cards_parser.add_argument("--output-dir")
    image_cards_parser.add_argument("--force-refresh-images", action="store_true")
    image_cards_parser.add_argument("--open-preview", action="store_true")

    facebook_parser = subparsers.add_parser("post-facebook")
    facebook_parser.add_argument("--dry-run", action="store_true")

    pipeline_parser = subparsers.add_parser("run-pipeline")
    pipeline_parser.add_argument("--source-master", default=str(DEFAULT_SOURCE_MASTER))
    pipeline_parser.add_argument("--priority", default="P1", choices=["P1", "P2", "P3"])
    pipeline_parser.add_argument("--limit", type=int, default=10)
    pipeline_parser.add_argument("--label", default="morning", choices=["morning", "evening"])
    pipeline_parser.add_argument("--brief-limit", type=int, default=12)
    pipeline_parser.add_argument("--min-score", type=int, default=6)
    pipeline_parser.add_argument("--force-summary", action="store_true")
    pipeline_parser.add_argument("--retry-attempts", type=int, default=1)

    scan_parser = subparsers.add_parser("run-scan")
    scan_parser.add_argument("--source-master", default=str(DEFAULT_SOURCE_MASTER))
    scan_parser.add_argument("--priority", default="P1", choices=["P1", "P2", "P3"])
    scan_parser.add_argument("--limit", type=int, default=10)
    scan_parser.add_argument("--label", default="morning", choices=["morning", "evening"])
    scan_parser.add_argument("--brief-limit", type=int, default=12)
    scan_parser.add_argument("--min-score", type=int, default=6)
    scan_parser.add_argument("--force-summary", action="store_true")
    scan_parser.add_argument("--retry-attempts", type=int, default=1)

    scheduled_parser = subparsers.add_parser("run-scheduled")
    scheduled_parser.add_argument("--slot", default="auto", choices=["auto", "morning", "evening"])
    scheduled_parser.add_argument("--dry-run", action="store_true")

    test_news_parser = subparsers.add_parser(
        "test-news",
        help="Fetch configured program news outside the schedule and render an isolated preview",
    )
    test_news_parser.add_argument("--limit-per-source", type=int, default=10)
    test_news_parser.add_argument("--card-limit", type=int, default=12)
    test_news_parser.add_argument("--open-preview", action="store_true")

    status_parser = subparsers.add_parser("news-status")
    status_parser.add_argument("--run-id")
    status_parser.add_argument("--limit", type=int, default=20)

    resolve_parser = subparsers.add_parser("resolve-delivery")
    resolve_parser.add_argument("--id", type=int, required=True)
    resolve_parser.add_argument("--resolution", required=True, choices=["succeeded", "retry"])
    resolve_parser.add_argument("--reviewer", required=True)
    resolve_parser.add_argument("--note", required=True)

    publish_run_parser = subparsers.add_parser("publish-run")
    publish_run_parser.add_argument("--run-id", required=True)

    subparsers.add_parser("self-test")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "validate-sources":
        result, report = validate_sources(args.source_master)
        print(report)
        logger.info("Source validation finished: ok=%s rows=%s", result.ok, result.row_count)
        return 0 if result.ok else 1

    if args.command == "plan-sources":
        _, report = build_fetch_plan(args.source_master, priority=args.priority)
        print(report)
        logger.info("Source fetch plan generated for priority %s", args.priority)
        return 0

    if args.command == "readiness-brief":
        result, brief_path, output = generate_readiness_brief(
            args.source_master,
            priority=args.priority,
        )
        print(output)
        if not result.ok:
            logger.warning("Readiness brief skipped because source validation failed")
            return 1
        print("")
        print(f"Saved: {brief_path}")
        logger.info("Readiness brief saved to %s", brief_path)
        return 0

    if args.command == "sync-sources":
        result, count = sync_source_master(args.source_master)
        if not result.ok:
            print(format_errors(result.errors))
            return 1
        print(f"Synced sources: {count}")
        return 0

    if args.command == "fetch-rss":
        result = fetch_rss(
            priority=args.priority,
            limit=args.limit,
            source_master=args.source_master,
        )
        if not result["ok"]:
            print(format_errors(result["validation"].errors))
            return 1
        print(f"Synced sources: {result['synced_sources']}")
        for item in result["results"]:
            print(
                f"{item['source_id']} | {item['source_name']} | {item['status']} | "
                f"fetched={item['fetched']} inserted={item['inserted']} | {item['message']}"
            )
        return 0

    if args.command == "score-articles":
        scored = score_articles()
        print(f"Scored articles: {len(scored)}")
        for item in scored[:20]:
            print(f"- {item['article_id']} | {item['score']} | {item['title']}")
        return 0

    if args.command == "html-dry-run":
        result = html_dry_run(priority=args.priority, source_master=args.source_master)
        if not result["ok"]:
            print(format_errors(result["validation"].errors))
            return 1
        print(f"HTML dry-run sources: {len(result['results'])}")
        for item in result["results"]:
            print(f"{item['source_id']} | {item['source_name']} | {item['status']} | {item['message']}")
        return 0

    if args.command == "fetch-html":
        result = fetch_html(
            priority=args.priority,
            limit=args.limit,
            source_master=args.source_master,
        )
        if not result["ok"]:
            print(format_errors(result["validation"].errors))
            return 1
        print(f"Synced sources: {result['synced_sources']}")
        for item in result["results"]:
            print(
                f"{item['source_id']} | {item['source_name']} | {item['status']} | "
                f"fetched={item['fetched']} inserted={item['inserted']} | {item['message']}"
            )
        return 0

    if args.command == "refresh-trends":
        result = refresh_trends(
            csv_path=args.csv,
            timeframe=args.timeframe,
            fetch_google=args.fetch_google,
        )
        print(f"Seeded trends: {result['seeded']}")
        print(f"Imported trends: {result['imported']}")
        print(f"Fetched Google trends: {result['fetched']}")
        if result["fetch_error"]:
            print(f"Google Trends fetch warning: {result['fetch_error']}")
        return 0

    if args.command == "summarize-articles":
        summaries = summarize_articles(min_score=args.min_score, force=args.force, limit=args.limit)
        print(f"Generated AI summaries: {len(summaries)}")
        return 0

    if args.command == "summarize-evernote":
        if args.article_id:
            results = [
                summarize_article_id_with_evernote(
                    args.article_id,
                    dry_run=args.dry_run,
                    save=not args.no_save,
                )
            ]
        else:
            results = summarize_candidates_with_evernote(
                min_score=args.min_score,
                limit=args.limit,
                dry_run=args.dry_run,
                save=not args.no_save,
            )

        for result in results:
            print(f"{result.article_id} | {result.status} | {result.message}")
            if result.prompt and args.dry_run:
                print(result.prompt[:1200])
        return 0 if all(result.ok for result in results) else 1

    if args.command == "generate-brief":
        result = write_brief(args.type)
        print(f"Generated {args.type} brief with {result['items']} items")
        print(f"Markdown: {result['markdown_path']}")
        print(f"JSON: {result['json_path']}")
        return 0

    if args.command == "run-scan":
        result = _run_scan(args)
        return 0 if result else 1

    if args.command == "run-scheduled":
        return _run_scheduled(args)
    if args.command == "test-news":
        return _run_program_news_test(args)

    if args.command == "news-status":
        return _news_status(args)

    if args.command == "resolve-delivery":
        return _resolve_delivery(args)

    if args.command == "publish-run":
        return _publish_run(args)

    if args.command == "generate-image-cards":
        result = generate_image_cards(
            args.type,
            limit=args.limit,
            output_dir=args.output_dir,
            force_refresh_images=args.force_refresh_images,
            open_preview=args.open_preview,
        )
        print(f"Generated {result['items']} image cards for {args.type} brief")
        print(f"Output: {result['output_dir']}")
        print(f"Manifest: {result['manifest_path']}")
        print(f"Preview: {result['preview_path']}")
        return 0

    if args.command == "post-facebook":
        return _post_facebook_from_cli(dry_run=args.dry_run)

    if args.command == "run-pipeline":
        result = _run_scan(args)
        return 0 if result else 1

    if args.command == "self-test":
        import unittest

        suite = unittest.defaultTestLoader.discover("tests")
        outcome = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if outcome.wasSuccessful() else 1

    return 1


def _news_status(args):
    if args.run_id:
        record = get_news_run(args.run_id, db_path=DEFAULT_DB_PATH)
        runs = [record] if record else []
    else:
        runs = list_news_runs(db_path=DEFAULT_DB_PATH, limit=max(1, int(args.limit or 20)))
    deliveries = list_publish_deliveries(db_path=DEFAULT_DB_PATH, run_id=args.run_id)
    if not runs:
        print("No news runs found.")
    for run in runs:
        stats = run.get("stats") or {}
        reconciliation = stats.get("publish_reconciliation") or {}
        print(
            f"{run['run_id']} | state={run['state']} | lane={run.get('lane') or '-'} | "
            f"owner={run.get('owner') or '-'} | deadline={run.get('deadline') or '-'} | "
            f"reconciliation={reconciliation.get('status') or '-'} | "
            f"marker={stats.get('marker_mode') or '-'} | "
            f"L1={stats.get('sheet_l1') or '-'} | M1={stats.get('sheet_m1') or '-'} | "
            f"target={stats.get('primary_target_deadline') or '-'} | "
            f"hard={stats.get('primary_hard_deadline') or '-'} | "
            f"verify={stats.get('sheet_verification_deadline') or '-'} | "
            f"stable={stats.get('sheet_stable', False)} | rows={stats.get('sheet_row_count', 0)} | "
            f"fallback={stats.get('fallback_reason') or '-'}"
        )
    for delivery in deliveries:
        print(
            f"delivery={delivery['id']} | run={delivery['run_id']} | status={delivery['status']} | "
            f"channel={delivery['channel']} | destination={delivery['destination']} | "
            f"attempts={delivery['attempt_count']}"
        )
    has_attention = any(
        run.get("state") == "FAILED"
        and ((run.get("stats") or {}).get("publish_reconciliation") or {}).get("status") != "succeeded"
        for run in runs
    ) or any(
        delivery.get("status") == "needs_review" for delivery in deliveries
    )
    return 1 if has_attention else 0


def _resolve_delivery(args):
    resolved = resolve_publish_delivery(
        args.id,
        args.resolution,
        args.reviewer,
        args.note,
        db_path=DEFAULT_DB_PATH,
    )
    if not resolved:
        print(f"Delivery {args.id} was not found or is not in needs_review.")
        return 1
    print(
        f"Delivery {resolved['id']} resolved as {args.resolution}; "
        f"new status={resolved['status']} retryable={bool(resolved['retryable'])}."
    )
    return 0


def _publish_run(args):
    validate_runtime_seeds()
    record = get_news_run(args.run_id, db_path=DEFAULT_DB_PATH)
    if not record:
        print(f"News run not found: {args.run_id}")
        return 1
    if record.get("state") not in TERMINAL_RUN_STATES:
        print(
            f"Run {args.run_id} is still {record.get('state')}; "
            "use run-scheduled so its lease remains fenced."
        )
        return 1
    try:
        _date, slot = str(args.run_id).split(":", 1)
        if slot not in {"morning", "evening"}:
            raise ValueError
    except ValueError:
        print("run_id must use YYYY-MM-DD:morning|evening.")
        return 1

    settings = load_runtime_settings()
    orchestration = settings.get("orchestration") or {}
    lease_seconds = int(orchestration.get("lease_seconds") or 300)
    heartbeat_seconds = int(orchestration.get("heartbeat_seconds") or 30)
    owner = f"publish-run:{os.getpid()}:{uuid.uuid4().hex[:12]}"
    claim = claim_terminal_news_run_lease(
        args.run_id,
        owner,
        db_path=DEFAULT_DB_PATH,
        lease_seconds=lease_seconds,
    )
    if not claim or not claim.get("acquired"):
        print(
            f"Run {args.run_id} is already being reconciled by another process "
            f"({(claim or {}).get('claim_reason') or 'unavailable'})."
        )
        return 1

    run_dir = ROOT_DIR / "output" / "runs" / news_run_directory_name(args.run_id)
    decision = {
        "run_id": args.run_id,
        "owner": owner,
        "lane": record.get("lane"),
        "action": "retry",
        "lease_seconds": lease_seconds,
        "heartbeat_seconds": heartbeat_seconds,
    }
    ok = False
    lines = []
    error_message = ""
    try:
        with maintain_terminal_news_run_lease(decision, db_path=DEFAULT_DB_PATH):
            stats, cards_result, publish_plan = _resume_scheduled_output(
                run_dir,
                args.run_id,
                settings.get("visual") or {},
            )
            ok, lines = _publish_scheduled_cards(
                cards_result["cards"],
                slot,
                decision,
                settings,
                publish_plan=publish_plan,
                fence_run=False,
                terminal_fence=True,
            )
    except Exception as exc:
        logger.exception("Manual publish-run failed")
        error_message = str(exc)
        lines.append(f"Manual publish-run failed: {exc}")
    finally:
        released = release_terminal_news_run_lease(
            args.run_id,
            owner,
            db_path=DEFAULT_DB_PATH,
            reconciliation={
                "status": "succeeded" if ok else "failed",
                "error": error_message or ("" if ok else "one_or_more_publish_actions_failed"),
            },
        )
        if released is None:
            ok = False
            lines.append("Publish retry lease was lost; reconciliation requires review.")

    if "stats" in locals():
        print(format_combined_stats(stats, run_dir / "combined_brief.json"))
    for line in lines:
        print(line)
    return 0 if ok else 1


def _run_scheduled(args):
    validate_runtime_seeds()
    settings = load_runtime_settings()
    scan = settings.get("scan") or {}
    visual = settings.get("visual") or {}
    orchestration = settings.get("orchestration") or {}
    now_vietnam = datetime.now(timezone(timedelta(hours=7)))
    trigger = "cli_auto" if args.slot == "auto" else "cli_manual"
    if args.slot == "auto":
        if not bool(scan.get("auto_run_enabled", False)):
            print("Auto run is disabled; no scheduled run was claimed.")
            return 0
        slot, scheduled = due_scheduled_slot(
            now_vietnam,
            scan.get("times"),
            (settings.get("orchestration") or {}).get("catch_up_window_minutes", 120),
        )
        if not slot:
            print("No scheduled news run is due inside the catch-up window.")
            return 0
    else:
        slot = args.slot
        scheduled = scheduled_datetime(slot, now=now_vietnam, schedule_times=scan.get("times"))
        if scheduled > now_vietnam:
            print(
                f"The {slot} slot has not started yet. "
                f"Scheduled time: {scheduled.isoformat()}."
            )
            return 0
    decision = None
    try:
        decision = select_scheduled_lane(
            slot,
            visual.get("sheet_url", ""),
            scheduled_at=scheduled,
            schedule_times=scan.get("times"),
            orchestration=orchestration,
            db_path=DEFAULT_DB_PATH,
            wait_callback=lambda status: print(
                f"{status.get('state') or 'WAIT_SHEET'} {status['run_id']}: {status['reason']} "
                f"({int(status['wait_seconds'])}s)"
            ),
        )
        if decision.get("action") == "terminal":
            reconciliation = (
                ((decision.get("record") or {}).get("stats") or {}).get("publish_reconciliation") or {}
            )
            reconciled = reconciliation.get("status") == "succeeded"
            suffix = " (publishing reconciled)" if reconciled else ""
            print(f"Run {decision['run_id']} already finished: {decision.get('state')}{suffix}")
            return 1 if decision.get("state") == "FAILED" and not reconciled else 0
        if decision.get("action") != "selected":
            print(f"Run {decision['run_id']} is already owned by another process: {decision.get('reason')}")
            return 0

        with maintain_news_run_lease(decision, db_path=DEFAULT_DB_PATH):
            run_dir = ROOT_DIR / "output" / "runs" / news_run_directory_name(decision["run_id"])
            brief_path = run_dir / "combined_brief.json"
            if decision.get("state") == "PUBLISHING":
                source_stats, cards_result, publish_plan = _resume_scheduled_output(
                    run_dir,
                    decision["run_id"],
                    visual,
                )
            else:
                update_scheduled_run(decision, "RENDERING", db_path=DEFAULT_DB_PATH)
                publish_env = load_ai_env()
                publish_plan = build_publish_plan(
                    settings.get("publish") or {},
                    facebook_page_id=publish_env.get("FACEBOOK_PAGE_ID", ""),
                    dry_run=args.dry_run,
                )
                primary = decision["lane"] == "primary"
                source_result = build_combined_brief(
                    source_mode="sheet",
                    sheet_url=visual.get("sheet_url", "") if primary else "",
                    sheet_limit=None,
                    app_limit=None,
                    card_limit=None,
                    brief_path=brief_path,
                    db_path=DEFAULT_DB_PATH,
                    exclude_vietnam=visual.get("exclude_vietnam_sources", False),
                    sheet_snapshot=decision.get("snapshot") if primary else None,
                    expected_run_id=decision["run_id"] if primary else None,
                    allow_backup=not primary,
                    run_id=decision["run_id"],
                    selected_lane=decision["lane"],
                    production=True,
                    execution_mode="scheduled",
                    preview_only=False,
                    trigger=trigger,
                )
                source_result.stats.update(
                    {
                        "run_id": decision["run_id"],
                        "selected_lane": decision["lane"],
                        "selection_reason": decision.get("reason") or "",
                    }
                )
                source_result.payload.update(
                    {
                        "run_id": decision["run_id"],
                        "scan_label": slot,
                        "selected_lane": decision["lane"],
                        "execution_mode": "scheduled",
                        "trigger": trigger,
                        "stats": source_result.stats,
                        "publish_plan": publish_plan,
                    }
                )
                validation = validate_brief_payload(
                    source_result.payload,
                    expected_run_id=decision["run_id"],
                    expected_lane=decision["lane"],
                    require_publishable=True,
                )
                if source_result.payload.get("items") and not validation["ready"]:
                    raise RuntimeError("Production brief quality gate failed: " + "; ".join(validation["errors"][:8]))
                if not source_result.payload.get("items") and source_result.stats.get("quality_rejected", 0):
                    raise RuntimeError(format_empty_combined_message(source_result.stats, source_result.brief_path))
                write_json_atomic(source_result.brief_path, source_result.payload)
                if not source_result.payload.get("items"):
                    if decision.get("lane") == "backup" and source_result.stats.get("backup_status") == "failed":
                        raise RuntimeError(
                            format_empty_combined_message(source_result.stats, source_result.brief_path)
                        )
                    update_scheduled_run(
                        decision,
                        "NO_NEW_CONTENT",
                        stats=source_result.stats,
                        db_path=DEFAULT_DB_PATH,
                    )
                    print(f"Run {decision['run_id']}: NO_NEW_CONTENT")
                    print(format_combined_stats(source_result.stats, source_result.brief_path))
                    return 0

                source_stats = source_result.stats
                cards_result = generate_image_cards(
                    "combined",
                    limit=None,
                    output_dir=run_dir / "visual",
                    source_brief_path=source_result.brief_path,
                    style_settings=visual,
                )
            update_scheduled_run(decision, "PUBLISHING", stats=source_stats, db_path=DEFAULT_DB_PATH)
            publish_ok, publish_lines = _publish_scheduled_cards(
                cards_result["cards"],
                slot,
                decision,
                settings,
                publish_plan=publish_plan,
            )
            update_scheduled_run(
                decision,
                "SUCCEEDED" if publish_ok else "FAILED",
                stats={**source_stats, "publish_ok": publish_ok},
                error=None if publish_ok else "one_or_more_publish_actions_failed",
                db_path=DEFAULT_DB_PATH,
            )
            print(format_combined_stats(source_stats, brief_path))
            print(f"Rendered cards: {cards_result['items']} -> {cards_result['output_dir']}")
            for line in publish_lines:
                print(line)
            return 0 if publish_ok else 1
    except Exception as exc:
        if decision and decision.get("action") == "selected":
            update_scheduled_run(
                decision,
                "FAILED",
                error=str(exc),
                db_path=DEFAULT_DB_PATH,
                required=False,
            )
        logger.exception("Scheduled CLI run failed")
        print(f"Scheduled run failed: {exc}")
        return 1


def _run_program_news_test(args):
    validate_runtime_seeds()
    settings = load_runtime_settings()
    scan = settings.get("scan") or {}
    visual = settings.get("visual") or {}
    try:
        result = run_program_news_test(
            limit_per_source=max(1, int(args.limit_per_source or scan.get("limit_per_source") or 10)),
            card_limit=max(1, int(args.card_limit or visual.get("card_limit") or 12)),
            exclude_vietnam=visual.get("exclude_vietnam_sources", False),
            style_settings=visual,
            open_preview=bool(args.open_preview),
        )
        print(f"Program test: {result['status']}")
        print(f"Test ID: {result['test_id']}")
        print(format_combined_stats(result["source_stats"], result["brief_path"]))
        if result.get("cards_result"):
            print(f"Rendered cards: {result['cards_result']['items']} -> {result['cards_result']['output_dir']}")
        else:
            print("No preview cards were generated.")
        return 0
    except Exception as exc:
        logger.exception("Program news test failed")
        print(f"Program news test failed: {exc}")
        return 1


def _resume_scheduled_output(run_dir, run_id, _visual_settings):
    brief_path = run_dir / "combined_brief.json"
    if not brief_path.exists():
        raise FileNotFoundError(f"Cannot resume publishing; run brief is missing: {brief_path}")
    try:
        payload = json.loads(brief_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cannot resume publishing; run brief is invalid: {brief_path}") from exc
    if str(payload.get("run_id") or "") != run_id:
        raise ValueError(f"Cannot resume publishing; run_id mismatch in {brief_path}")
    publish_plan = payload.get("publish_plan")
    if not isinstance(publish_plan, dict) or publish_plan.get("version") != 1:
        raise ValueError(f"Cannot resume publishing; publish plan is missing from {brief_path}")
    validation = validate_brief_payload(payload, expected_run_id=run_id, require_publishable=True)
    if not validation["ready"]:
        raise ValueError("Cannot resume publishing; frozen brief failed validation: " + "; ".join(validation["errors"][:8]))
    visual_dir = run_dir / "visual"
    try:
        cards_result = load_image_cards_result(visual_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(
            f"Cannot resume publishing; frozen card manifest is missing or invalid: {visual_dir}"
        ) from exc
    stats = dict(payload.get("stats") or {})
    stats["resumed_from_state"] = "PUBLISHING"
    return stats, cards_result, publish_plan


def _publish_scheduled_cards(
    cards,
    brief_label,
    decision,
    settings,
    dry_run=False,
    publish_plan=None,
    fence_run=True,
    terminal_fence=False,
):
    env = load_ai_env()
    publish = publish_plan or build_publish_plan(
        settings.get("publish") or {},
        facebook_page_id=env.get("FACEBOOK_PAGE_ID", ""),
        dry_run=dry_run,
    )
    if publish.get("dry_run"):
        return True, ["Dry-run: external publishing skipped."]

    lines = []
    ok = True
    run_id = decision["run_id"]
    owner = decision["owner"]
    chat_ids = list(publish.get("telegram_chat_ids") or [])
    if publish.get("send_telegram"):
        token = str(env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token or not chat_ids:
            ok = False
            lines.append("Telegram skipped: token or chat destination is missing.")
        else:
            intro = _render_telegram_intro_text(publish.get("telegram_intro_text") or "{date}", brief_label)
            for chat_id in chat_ids:
                claims = claim_delivery_cards(
                    run_id,
                    cards,
                    "telegram",
                    str(chat_id),
                    owner,
                    db_path=DEFAULT_DB_PATH,
                    fence_run=fence_run,
                    terminal_fence=terminal_fence,
                )
                selected = claims["claimed"]
                blockers = delivery_claim_blockers(claims)
                if blockers:
                    ok = False
                    lines.append(f"Telegram {chat_id}: delivery requires attention - {', '.join(blockers)}")
                if not selected:
                    reasons = sorted({str(item.get("reason") or "unknown") for item in claims["skipped"]})
                    lines.append(f"Telegram {chat_id}: skipped by delivery ledger - {', '.join(reasons)}")
                    continue
                try:
                    if intro:
                        send_message(token, chat_id, intro)
                    sent = send_photos(token, chat_id, selected)
                    finish_delivery_cards(
                        run_id,
                        selected,
                        "telegram",
                        str(chat_id),
                        owner,
                        succeeded=True,
                        result={"sent_photos": len(sent)},
                        db_path=DEFAULT_DB_PATH,
                    )
                    mark_items_published(selected, telegram_chat_id=str(chat_id), db_path=DEFAULT_DB_PATH)
                    lines.append(f"Telegram {chat_id}: sent {len(sent)} photos.")
                except Exception as exc:
                    finish_delivery_cards(
                        run_id,
                        selected,
                        "telegram",
                        str(chat_id),
                        owner,
                        succeeded=False,
                        error=exc,
                        db_path=DEFAULT_DB_PATH,
                    )
                    ok = False
                    lines.append(f"Telegram {chat_id}: failed - {exc}")

    if publish.get("post_facebook"):
        page_id = str(publish.get("facebook_page_id") or "").strip()
        token = str(env.get("FACEBOOK_PAGE_ACCESS_TOKEN") or "").strip()
        facebook_dry_run = bool(publish.get("facebook_dry_run", True))
        if not page_id or not token:
            ok = False
            lines.append("Facebook skipped: page id or access token is missing.")
        elif facebook_dry_run:
            lines.append("Facebook configuration is dry-run; external post skipped.")
        else:
            claims = claim_delivery_cards(
                run_id,
                cards,
                "facebook_page",
                page_id,
                owner,
                db_path=DEFAULT_DB_PATH,
                fence_run=fence_run,
                terminal_fence=terminal_fence,
            )
            selected = claims["claimed"]
            blockers = delivery_claim_blockers(claims)
            if blockers:
                ok = False
                lines.append(f"Facebook: delivery requires attention - {', '.join(blockers)}")
            if not selected:
                reasons = sorted({str(item.get("reason") or "unknown") for item in claims["skipped"]})
                lines.append(f"Facebook: skipped by delivery ledger - {', '.join(reasons)}")
            else:
                try:
                    message = render_facebook_intro_text(publish.get("facebook_intro_text"), brief_label)
                    result = publish_photo_post(page_id, token, selected, message, dry_run=False)
                    post_id = str(result.get("post_id") or "")
                    finish_delivery_cards(
                        run_id,
                        selected,
                        "facebook_page",
                        page_id,
                        owner,
                        succeeded=True,
                        result={"post_id": post_id},
                        db_path=DEFAULT_DB_PATH,
                    )
                    mark_items_published(
                        selected,
                        facebook_page_id=page_id,
                        facebook_post_id=post_id,
                        db_path=DEFAULT_DB_PATH,
                    )
                    lines.append(f"Facebook published: {post_id or 'unknown post id'}")
                except Exception as exc:
                    finish_delivery_cards(
                        run_id,
                        selected,
                        "facebook_page",
                        page_id,
                        owner,
                        succeeded=False,
                        error=exc,
                        db_path=DEFAULT_DB_PATH,
                    )
                    ok = False
                    lines.append(f"Facebook failed: {exc}")

    if publish.get("post_facebook_groups"):
        validation = validate_group_config(publish.get("facebook_groups") or [])
        if not validation["ready"]:
            ok = False
            lines.append("Facebook Groups skipped: invalid configuration.")
        else:
            group_guard = FacebookGroupDeliveryGuard(
                run_id,
                cards,
                owner,
                db_path=DEFAULT_DB_PATH,
                fence_run=fence_run,
                terminal_fence=terminal_fence,
            )
            try:
                result = publish_to_groups(
                    cards,
                    validation["groups"],
                    publish.get("facebook_intro_text"),
                    caption_renderer=lambda template, label: render_facebook_intro_text(template, brief_label=label),
                    brief_label=brief_label,
                    dry_run=bool(publish.get("facebook_group_dry_run", True)),
                    delay_min_seconds=int(publish.get("facebook_group_delay_min_seconds") or 900),
                    delay_max_seconds=int(publish.get("facebook_group_delay_max_seconds") or 1800),
                    max_groups_per_brief=int(publish.get("facebook_group_max_per_brief") or 2),
                    max_groups_per_day=int(publish.get("facebook_group_max_per_day") or 4),
                    queue_expiry_hours=int(publish.get("facebook_group_queue_expiry_hours") or 12),
                    manual=terminal_fence,
                    before_group_publish=group_guard.before_publish,
                    after_group_publish=group_guard.after_publish,
                )
                counts = result.get("counts") or {}
                unresolved_group_results = []
                if terminal_fence:
                    for item in result.get("results") or []:
                        status = str(item.get("status") or "").lower()
                        message = str(item.get("message") or "")
                        safely_skipped = status == "skipped" and message in {
                            "Already delivered",
                            "delivery ledger already succeeded",
                        }
                        if status in {"failed", "needs_login", "queued"} or (
                            status == "skipped" and not safely_skipped
                        ):
                            unresolved_group_results.append(
                                f"{item.get('group_name') or 'group'}:{status or 'unknown'}"
                            )
                group_ok = (
                    not counts.get("failed")
                    and not counts.get("needs_login")
                    and not group_guard.blockers
                    and not unresolved_group_results
                )
                ok = ok and group_ok
                if (
                    not bool(publish.get("facebook_group_dry_run", True))
                    and int(counts.get("published") or 0) + int(counts.get("pending") or 0) > 0
                ):
                    mark_items_published(cards, db_path=DEFAULT_DB_PATH)
                lines.append(
                    "Facebook Groups: "
                    f"published={counts.get('published', 0)} pending={counts.get('pending', 0)} "
                    f"failed={counts.get('failed', 0)} needs_login={counts.get('needs_login', 0)}"
                )
                if group_guard.blockers:
                    lines.append(
                        "Facebook Groups delivery requires attention: "
                        + ", ".join(sorted(set(group_guard.blockers)))
                    )
                if unresolved_group_results:
                    lines.append(
                        "Facebook Groups retry remains incomplete: "
                        + ", ".join(unresolved_group_results)
                    )
            except Exception as exc:
                ok = False
                lines.append(f"Facebook Groups failed: {exc}")

    if not any(
        [publish.get("send_telegram"), publish.get("post_facebook"), publish.get("post_facebook_groups")]
    ):
        lines.append("No external publish action is enabled.")
    return ok, lines


def _render_telegram_intro_text(template, brief_label):
    raw = str(template or "{date}")
    rendered = render_facebook_intro_text(raw, brief_label)
    if "{brief_label}" not in raw:
        label_text = facebook_brief_label_text(brief_label)
        if label_text not in rendered:
            rendered = f"{label_text}\n{rendered}"
    return rendered


def _run_scan(args):
    result = run_pipeline(
        priority=args.priority,
        limit=args.limit,
        source_master=args.source_master,
        scan_label=args.label,
        brief_limit=args.brief_limit,
        min_score=args.min_score,
        force_summary=args.force_summary,
        retry_attempts=args.retry_attempts,
    )
    if not result["ok"]:
        validation = result["fetch"].get("validation")
        print(format_errors(validation.errors if validation else ["Scan failed"]))
        return False
    inserted = sum(item["inserted"] for item in result["fetch"]["results"])
    print(f"Scan complete for {args.priority} / {args.label}")
    print(
        f"Trends: seeded={result['trends']['seeded']} "
        f"imported={result['trends']['imported']} fetched={result['trends']['fetched']}"
    )
    print(f"RSS inserted articles: {inserted}")
    html_inserted = sum(item["inserted"] for item in result["html_fetch"]["results"])
    print(f"HTML inserted articles: {html_inserted}")
    print(f"Scored articles: {len(result['scored'])}")
    print(f"AI summaries: {len(result['summaries'])}")
    retried = [step for step in result.get("steps", []) if step.get("attempt", 1) > 1]
    if retried:
        print(f"Retried steps: {len(retried)}")
    brief = result["brief"]
    print(
        f"{brief['scan_label']} brief: {brief['items']} items | "
        f"{brief['markdown_path']} | {brief['latest_markdown_path']}"
    )
    return True


def format_errors(errors):
    return "\n".join(f"- {error}" for error in errors)


def _post_facebook_from_cli(dry_run=False):
    settings = load_runtime_settings()
    env = load_ai_env()
    page_id = env.get("FACEBOOK_PAGE_ID", "").strip()
    token = env.get("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()
    if not page_id or not token:
        print("Facebook skipped: FACEBOOK_PAGE_ID or FACEBOOK_PAGE_ACCESS_TOKEN is empty.")
        return 1

    visual = settings.get("visual", {})
    source_mode = str(visual.get("source_mode") or "combined").strip().lower()
    if source_mode in {"app", "sheet", "combined"}:
        print("Facebook skipped: direct legacy publishing is disabled; use run-scheduled to claim a run and lane.")
        return 1
    result = build_combined_brief(
        source_mode=source_mode,
        sheet_url=visual.get("sheet_url", ""),
        sheet_limit=None if source_mode == "sheet" or visual.get("sheet_limit_max", True) else int(visual.get("sheet_limit") or 20),
        app_limit=None if visual.get("app_limit_max", True) else int(visual.get("app_limit") or 20),
        card_limit=None if source_mode == "sheet" or visual.get("card_limit_max", True) else int(visual.get("card_limit") or 12),
        brief_path=DEFAULT_COMBINED_BRIEF_PATH,
    )
    if not result.payload.get("items"):
        print(format_combined_stats(result.stats, result.brief_path))
        print("Facebook skipped: no image cards to publish.")
        return 1

    card_limit = None if source_mode == "sheet" or visual.get("card_limit_max", True) else int(visual.get("card_limit") or 12)
    cards_result = generate_image_cards(
        "combined",
        limit=card_limit,
        source_brief_path=result.brief_path,
        style_settings=visual,
    )
    cards = cards_result["cards"]
    safety = validate_cards_publish_safety(cards)
    if not safety["ready"]:
        print("Facebook skipped: publish safety failed.")
        print(format_errors(safety["errors"]))
        return 1

    publish = settings.get("publish", {})
    message = _render_facebook_intro_text(publish.get("facebook_intro_text"), result.payload.get("scan_label"))
    try:
        publish_result = publish_photo_post(page_id, token, cards, message, dry_run=dry_run)
    except Exception as exc:
        uploaded = getattr(exc, "uploaded_photo_ids", None)
        print(f"Facebook post failed: {exc}")
        if uploaded:
            print(f"Uploaded photo IDs before failure: {', '.join(uploaded)}")
        return 1

    print(format_combined_stats(result.stats, result.brief_path))
    print("")
    print(f"Generated image cards: {cards_result['items']}")
    print(f"Output: {cards_result['output_dir']}")
    if publish_result.get("dry_run"):
        print("Facebook dry-run: no post created")
        print(f"Photos: {len(publish_result['image_paths'])}")
        print(f"Photo descriptions planned: {len(publish_result.get('photo_descriptions') or [])}")
        return 0

    post_id = publish_result.get("post_id") or ""
    saved = mark_items_published(cards, facebook_page_id=page_id, facebook_post_id=post_id)
    print(f"Facebook published: {post_id or 'unknown post id'}")
    print(f"Photo descriptions with source links: {len(publish_result.get('photo_descriptions') or [])}")
    print(f"Published ledger updated: {saved} items")
    return 0


def _render_facebook_intro_text(template, brief_label=None):
    return render_facebook_intro_text(template, brief_label=brief_label)


def _configure_console_encoding():
    for stream in [sys.stdout, sys.stderr]:
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            continue


def main():
    raise SystemExit(run_cli(sys.argv[1:]))


if __name__ == "__main__":
    main()
