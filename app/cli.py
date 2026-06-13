import argparse
import sys

from app.logger import logger
from app.services.evernote_summarizer import summarize_article_id_with_evernote, summarize_candidates_with_evernote
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
from app.services.visual_brief_renderer import generate_image_cards


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

    if args.command == "run-pipeline":
        result = _run_scan(args)
        return 0 if result else 1

    if args.command == "self-test":
        import unittest

        suite = unittest.defaultTestLoader.discover("tests")
        outcome = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if outcome.wasSuccessful() else 1

    return 1


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
