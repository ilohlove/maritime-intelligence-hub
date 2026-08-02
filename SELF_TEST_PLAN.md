# SELF_TEST_PLAN.md

## Purpose

Define required checks before each module is considered done.

## Data Tests

- Import source master.
- Validate required columns.
- Detect duplicate IDs.
- Detect duplicate source names.
- Detect duplicate URLs.
- Validate allowed values.
- Validate score ranges.

## Fetch Tests

- Fetch one RSS source. Status: covered with parser/unit tests and CLI command.
- Fetch one HTML source. Status: dry-run only in this MVP.
- Handle one failing source without crashing. Status: covered.
- Log fetch errors clearly. Status: covered by fetch log stage.
- Respect source status. Status: covered through active source selection.

## Dedup Tests

- Deduplicate same URL.
- Deduplicate highly similar title.
- Rerun pipeline without creating duplicate articles.

## AI Tests

- Classify category. Status: source/category based MVP.
- Score importance. Status: covered.
- Generate Vietnamese summary. Status: covered with mock AI.
- Generate source-linked brief item. Status: covered.
- Skip AI when article is duplicate or below threshold. Status: implemented by summary candidate query.

## Brief Tests

- Generate Morning Brief.
- Generate Evening Brief.
- Export Markdown.
- Export JSON.
- Include source name and original URL for every item.

## Operational Tests

- Run pipeline repeatedly.
- Simulate network error.
- Simulate malformed source row.
- Confirm logs are readable.
- Confirm no publisher runs without approved output.

## Coordinator Tests

- Load legacy runtime settings with `07:30,19:30`; verify migration to `07:15,19:15`, `Asia/Bangkok`, and the default timeout/poll/catch-up/lease/heartbeat values.
- Reproduce the incident snapshot: `L1` has a start time, `M1` is empty, and rows belong to the prior day. Verify the coordinator waits, never renders stale rows, and claims backup once only after the deadline.
- Verify valid primary completion before the deadline selects primary. Verify primary completion after `BACKUP_SELECTED` is ignored for that run.
- Start two workers for the same `run_id`; only one may fetch, render, and publish. Restarting within the same slot must reuse the existing row.
- Verify primary `COMPLETED` with zero new items or all items already published ends in `NO_NEW_CONTENT`, without fallback or another polling loop.
- Verify canonical URL/title-hash deduplication across backup feeds happens once after lane selection.
- Simulate a worker crash during publish. A stale `sending` delivery must become `needs_review`; Telegram and Facebook retries remain independent.
- Resume a `PUBLISHING` run from its frozen brief/manifest and publish plan; do not re-filter globally published items or change destination/dry-run behavior.
- Delete or corrupt the frozen card manifest and verify resume/publish-run fails closed without rendering a replacement.
- Resolve `needs_review` as confirmed success and as retry, verifying the audit record and global published ledger.
- Start two `publish-run` processes for one terminal run; only one terminal retry lease may win, and a successful retry must reconcile `news-status`.
- Race Facebook Group publishers for both the same delivery and two batches competing for the last daily slot; only one may enter browser publish/reserve quota, and an expired `sending` claim must require review.
- Confirm an already-succeeded Group delivery closes its queue row without browser I/O; cancel a stale reviewed claim and verify its quota reservation is audited and released.
- Open a legacy SQLite schema concurrently from two processes and verify additive owner/lease/quota columns migrate once without lock or duplicate-column failure.
- Verify lease expiry/reclaim, 30-second heartbeat renewal, and SQLite contention with WAL/busy timeout.
- Verify run output uses the Windows-safe `output/runs/YYYY-MM-DD_morning/` or `output/runs/YYYY-MM-DD_evening/` mapping and a reader cannot observe a partially written JSON, Markdown, or image file.

## Sheet Protocol Tests

- Accept only one snapshot whose `N1` matches the expected `run_id`, `O1=COMPLETED`, `L1/M1` are ordered ISO-8601 timestamps with timezone, and `P1` matches the usable `A:K` row count.
- Treat `RUNNING`, blank control cells, transient HTTP errors, wrong run IDs, and incomplete row counts as retryable until the deadline.
- Treat `FAILED` plus `Q1` as an immediate backup trigger and preserve the error code in run diagnostics.
- Reject data rows whose Source URL is not an absolute HTTP/HTTPS URL, even when `P1` matches the physical row count.

## Current Acceptance Commands

```bat
python -m unittest discover -s tests
python -m compileall app tests
python -m app.main validate-sources
python -m app.main run-pipeline --priority P1
python -m app.main run-scheduled --slot auto --dry-run
```

Before a release, also build in a clean environment. Confirm the executable starts with bundled `NEWS_SOURCE_MASTER.csv` and `BACKUP_FEED_MASTER.csv`, and that imports for Requests, BeautifulSoup, Trafilatura, and Playwright pass.

## Backup lane checks

- Verify the Vietnam-source checkbox excludes `Country=Vietnam`, `.vn`, and
  resolved Vietnamese publishers.
- Verify explicit Sheet failure or primary timeout activates backup feeds once and records a reason.
- Verify a completed empty primary run does not activate backup.
- Verify Jina -> Trafilatura -> BeautifulSoup extraction order.
- Verify Gemini -> Groq -> OpenRouter fallback and provider metadata.
