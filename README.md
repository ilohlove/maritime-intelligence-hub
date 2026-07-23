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
python -m app.main self-test
```

The existing desktop shell remains available from the template:

```bat
python -m app.main --gui
```

### Facebook Groups publishing

The **Hoàn thành** tab can publish the latest rendered card set to multiple Facebook Groups with Playwright.

1. Open **Groups**, add each group name and `facebook.com/groups/...` URL, then optionally add a group-specific caption.
2. Choose **Đăng nhập / Re-auth** and complete Facebook login or 2FA manually in the Edge window. The app never stores a Facebook password.
3. Run **Dry-run** to verify the saved session and posting permission without creating a post.
4. Enable **Đăng Facebook Groups** to include the groups in the normal in-app schedule.

The browser profile is stored under `%LOCALAPPDATA%\Maritime Intelligence Hub\browser_profiles\facebook`, outside the repository. Safe defaults limit automatic publishing to two groups per brief and four real attempts per Vietnam calendar day, with a random 15–30 minute interval. Extra targets remain queued. **Post 1 Queued Group** handles one queued target per click and still respects the daily limit; dry-run does not consume quota.

Every enabled group requires its own non-duplicate caption, and every rendered caption includes the source name and original URL for each card. The app does not rewrite captions to evade moderation. A pending-admin-approval result is recorded as delivered so the same batch is not submitted twice. Login checkpoints, CAPTCHA, rate-limit warnings, and temporary-block signals stop the active queue and require manual review or re-authentication.

Use **Queue Manager** to review queued, failed, and login-blocked deliveries from any brief. It supports selecting one item for a quota-checked publish attempt, cancelling an item, and viewing schedule, expiry, priority, and stop reason. Queue items expire after 12 hours by default. Smaller priority numbers run first; groups with the same priority rotate by least-recent successful delivery so the same first groups are not selected for every brief. If the app closes during a scheduled delay, **Tự tiếp tục queue** resumes only that due item after restart; safety-stopped and unscheduled overflow items still require manual review.

The project should prove the data pipeline before investing in a larger interface.

## MVP Runtime Outputs

Runtime data is intentionally ignored by git:

- SQLite database: `data/mih.db`
- Brief files: `output/briefs/*.md` and `output/briefs/*.json`
- Readiness brief: `output/source_readiness_brief.md`

The MVP uses live RSS collection where feeds can be discovered, approved HTML metadata collection, trend-aware hotness scoring, configurable AI summarization with a mock fallback, and file-based publishing outputs.

The normal operating model is a scheduled scan, usually twice per day at 07:15 and 19:15. Each scan fetches the latest available items, deduplicates them against previous runs, scores and summarizes new candidates, then writes one current brief for that scan. The latest generated brief is also copied to `output/briefs/latest_brief.md` and `output/briefs/latest_brief.json`.

Trend scoring uses curated maritime keywords by default. Google Trends Vietnam can be added through RSS fetch or CSV import, then articles are ranked by recency, source quality, maritime relevance, trend keyword match, and Vietnam/logistics impact.

## Self-Test

```bat
python -m unittest discover -s tests
```

## Release

Before any release, read `RELEASE_WORKFLOW.md`.

Do not release until:

- project metadata has no placeholders;
- `latest_json_url` points to the real raw `latest.json`;
- security checks are completed;
- required self-tests pass.
