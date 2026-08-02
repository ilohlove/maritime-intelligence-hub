# Maritime Intelligence Hub

AI-assisted maritime news intelligence for Vietnamese maritime, shipping, port, logistics, and import-export communities.

## Mission

Become the fastest practical place to update maritime news with AI while respecting source attribution and copyright.

The first phase focuses on a dependable backend pipeline instead of a complex GUI:

- maintain a structured source master;
- collect P1 maritime news sources first;
- prefer RSS, then approved HTML crawling;
- deduplicate before AI processing;
- classify, score, and summarize important news;
- generate Morning Brief and Evening Brief outputs;
- prepare Telegram, social, and future website publishing.

## First Phase Scope

Build the core intelligence pipeline:

1. Import and validate source master data.
2. Fetch RSS feeds where available.
3. Crawl approved HTML sources when RSS is unavailable.
4. Store article metadata and original source URLs.
5. Detect duplicates before AI processing.
6. Score importance using rules before calling AI.
7. Generate Vietnamese AI summaries and maritime impact notes.
8. Export briefs as Markdown and JSON.

## Deferred Scope

The first phase does not include:

- complex GUI;
- public dashboard;
- login or multi-user permissions;
- payment or subscription;
- mobile app;
- AI chat;
- AIS tracking;
- knowledge graph;
- forecasting.

## Source Master

The current seed file is `NEWS_SOURCE_MASTER.csv`.

Future source management should use a normalized workbook or CSV set that includes:

- source metadata;
- category master;
- audience master;
- RSS/feed details;
- crawl rules;
- legal/copyright notes;
- roadmap and status tracking.

## Legal Position

The system must not republish full original articles. It may store metadata, title, source URL, publication date, short excerpt where reasonable, and AI-generated summaries with source attribution.

## Development

Install the pinned Python dependencies and the Playwright browser once:

```bat
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Current template command:

```bat
python -m app.main
```

Current CLI commands:

```bat
python -m app.main validate-sources
python -m app.main plan-sources --priority P1
python -m app.main readiness-brief --priority P1
python -m app.main sync-sources
python -m app.main fetch-rss --priority P1 --limit 10
python -m app.main html-dry-run --priority P1
python -m app.main fetch-html --priority P1 --limit 5
python -m app.main refresh-trends
python -m app.main refresh-trends --fetch-google
python -m app.main refresh-trends --csv path\to\google-trends.csv --timeframe 24h
python -m app.main score-articles
python -m app.main summarize-articles
python -m app.main generate-brief --type morning
python -m app.main run-scan --priority P1 --label morning
python -m app.main run-scan --priority P1 --label evening
python -m app.main run-pipeline --priority P1 --label morning
python -m app.main run-scheduled --slot auto --dry-run
python -m app.main self-test
```

The existing desktop shell remains available from the template:

```bat
python -m app.main --gui
```

### Facebook Groups publishing

The **Hoàn thành** tab can publish the latest rendered card set to multiple Facebook Groups with Playwright.

1. Open **Groups**, add each group name and `facebook.com/groups/...` URL, then optionally add a group-specific caption.
2. Choose **Đăng nhập / Re-auth** and complete Facebook login or 2FA manually in the Google Chrome window. The app never stores a Facebook password.
3. Run **Dry-run** to verify the saved session and posting permission without creating a post.
4. Enable **Đăng Facebook Groups** to include the groups in the normal in-app schedule.

The browser profile is stored under `%LOCALAPPDATA%\Maritime Intelligence Hub\browser_profiles\facebook_chrome`, outside the repository. Google Chrome is preferred, with Microsoft Edge and Playwright Chromium as fallbacks. Safe defaults limit automatic publishing to two groups per brief and four real attempts per Vietnam calendar day, with a random 15–30 minute interval. Extra targets remain queued. **Post 1 Queued Group** handles one queued target per click and still respects the daily limit; dry-run does not consume quota.

Every enabled group requires its own non-duplicate caption, and every rendered caption includes the source name and original URL for each card. The app does not rewrite captions to evade moderation. A pending-admin-approval result is recorded as delivered so the same batch is not submitted twice. Login checkpoints, CAPTCHA, rate-limit warnings, and temporary-block signals stop the active queue and require manual review or re-authentication.

Use **Queue Manager** to review queued, failed, and login-blocked deliveries from any brief. It supports selecting one item for a quota-checked publish attempt, cancelling an item, and viewing schedule, expiry, priority, and stop reason. Queue items expire after 12 hours by default. Smaller priority numbers run first; groups with the same priority rotate by least-recent successful delivery so the same first groups are not selected for every brief. If the app closes during a scheduled delay, **Tự tiếp tục queue** resumes only that due item after restart; safety-stopped and unscheduled overflow items still require manual review.

The project should prove the data pipeline before investing in a larger interface.

## MVP Runtime Outputs

Runtime data is intentionally ignored by git:

- SQLite database: `data/mih.db`
- Scheduled run artifacts: `output/runs/YYYY-MM-DD_morning/` and `output/runs/YYYY-MM-DD_evening/`
- Latest/legacy brief files: `output/briefs/*.md` and `output/briefs/*.json`
- Readiness brief: `output/source_readiness_brief.md`

The MVP uses live RSS collection where feeds can be discovered, approved HTML metadata collection, trend-aware hotness scoring, configurable AI summarization with a mock fallback, and file-based publishing outputs.

The normal operating model is a scheduled scan at `07:15` and `19:15` in `Asia/Bangkok`. Each scan has one stable ID such as `2026-08-01:morning`. SQLite ownership prevents two app processes or a restart from fetching, rendering, or publishing that slot twice.

### Reliable primary/backup orchestration

The Google Sheet AI agent is the primary lane. The app waits up to 30 minutes and polls every 60 seconds. It selects backup only after primary reports `FAILED` or reaches the deadline; `M1` must also prove completion occurred by that deadline. An empty completed primary run ends as `NO_NEW_CONTENT`. Once selected, a lane cannot be replaced or mixed with the other lane.

The Sheet keeps article data in `A:K` and publishes this control record in row 1:

| Cell | Value |
| --- | --- |
| `L1` | `started_at` ISO-8601 with timezone |
| `M1` | `completed_at` ISO-8601 with timezone; blank while running |
| `N1` | `run_id` (`YYYY-MM-DD:morning|evening`) |
| `O1` | `RUNNING`, `COMPLETED`, or `FAILED` |
| `P1` | completed article-row count |
| `Q1` | short error code on failure |

The agent writes `RUNNING` and clears completion fields first, replaces all article rows, then writes `COMPLETED`, `M1`, and `P1` last. The app accepts only a matching, internally consistent snapshot, so yesterday's rows cannot be rendered while today's agent is still running.

Use `AI_AGENT_SHEET_PROTOCOL.md` as the authoritative instruction block when updating the external scheduled AI agent.

Runtime defaults live in `config/runtime_settings.json` and are normalized by the loader:

```json
{
  "scan": {
    "times": ["07:15", "19:15"],
    "timezone": "Asia/Bangkok"
  },
  "orchestration": {
    "lane_policy": "primary_then_backup",
    "primary_timeout_minutes": 30,
    "poll_interval_seconds": 60,
    "catch_up_window_minutes": 120,
    "lease_seconds": 300,
    "heartbeat_seconds": 30
  }
}
```

The legacy default pair `07:30,19:30` is migrated automatically. Other valid custom schedules are preserved.

### Headless scheduler

Use the coordinator, not an always-open GUI, for production scheduling:

```bat
python -m app.main run-scheduled --slot auto
python -m app.main run-scheduled --slot morning --dry-run
python -m app.main run-scheduled --slot evening --dry-run
python -m app.main news-status --run-id 2026-08-01:morning
```

`--slot auto` resolves the most recent due slot within the 120-minute catch-up window. Explicit `morning` or `evening` selects an already-started Vietnam-calendar-day slot; a future slot is not claimed. `--dry-run` executes selection and output preparation without external publishing and remains part of the frozen run plan after a restart. Repeating a command for the same slot reuses the same `run_id` and cannot create a second delivery. Exit code `0` means success, no new content, no due slot, or work already owned/completed; exit code `1` means the selected or previously terminal run failed.

For Windows Task Scheduler, create triggers at 07:15 and 19:15, use `python` as the program, `-m app.main run-scheduled --slot auto` as arguments, and the repository directory as **Start in**. For an installed build, run `BV-maritime-intelligence-hub.exe run-scheduled --slot auto`. Keep only one configured task; the database lease is a second layer of protection, not a reason to schedule duplicate workers.

Run artifacts are isolated under `output/runs/YYYY-MM-DD_morning/` or `output/runs/YYYY-MM-DD_evening/` and finalized atomically. This filesystem-safe directory key replaces the `:` in `run_id` with `_`; manifests and database rows keep the canonical ID unchanged. The brief freezes non-secret channel destinations, captions, and dry-run behavior. A process recovered from `PUBLISHING` reuses this exact brief and manifest instead of filtering or rendering again; a missing or invalid frozen manifest fails closed. `Generate Test` output from Sheet/combined mode is marked preview-only and cannot be sent by the real publish buttons.

If `news-status` reports `needs_review`, verify the remote channel first. Then record the audited decision without editing SQLite manually:

```bat
python -m app.main resolve-delivery --id 42 --resolution succeeded --reviewer operator-name --note "Confirmed remote post"
python -m app.main resolve-delivery --id 42 --resolution retry --reviewer operator-name --note "No remote post found"
python -m app.main publish-run --run-id 2026-08-01:morning
```

`succeeded` also updates the global published ledger. `retry` only makes that exact channel/destination delivery claimable again; it never sends automatically. After choosing `retry`, run `publish-run` to reuse the frozen brief, manifest, and original non-secret publish plan. Only one `publish-run` process can hold the terminal-run retry lease; Facebook Group failures enter explicit manual-retry mode, and reconciliation remains failed while any planned Group is unresolved. Already successful destinations remain skipped, and the final reconciliation appears in `news-status`.

Trend scoring uses curated maritime keywords by default. Google Trends Vietnam can be added through RSS fetch or CSV import, then articles are ranked by recency, source quality, maritime relevance, trend keyword match, and Vietnam/logistics impact.

## Self-Test

```bat
python -m unittest discover -s tests
```

## Build

`build.bat release` builds the app; `build.bat first` also builds the updater. The build stops if Python, tkinter, PyInstaller, Requests, BeautifulSoup, Trafilatura, Playwright, or either source-master CSV is missing. It stages the installed Chromium and relocatable Tcl/Tk runtime, then uses the writable application `temp` directory for one-file extraction. Both `NEWS_SOURCE_MASTER.csv` and `BACKUP_FEED_MASTER.csv` are bundled and seeded beside a frozen app when needed.

## Release

Before any release, read `RELEASE_WORKFLOW.md`.

Do not release until:

- project metadata has no placeholders;
- `latest_json_url` points to the real raw `latest.json`;
- security checks are completed;
- required self-tests pass.

## Backup news lane

The coordinator uses `BACKUP_FEED_MASTER.csv` only after explicit primary failure or the 30-minute deadline. A completed primary snapshot with no new publishable items does not activate backup. Enable backup feeds by setting `Enabled=Yes`. The lane supports official maritime RSS, Google News RSS queries, and an optional self-hosted RSSHub endpoint. Set `JINA_READER_URL` for article extraction; Trafilatura and BeautifulSoup are local fallbacks.

AI can use `AI_PROVIDER=chain` with Gemini, Groq, and OpenRouter keys. The
“Không lấy tin tức từ nguồn Việt Nam” checkbox excludes Vietnamese sources
before fetching and before AI processing.
