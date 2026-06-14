# Changelog

## v1.0.4 - 2026-06-14

- Improve App mode empty-article diagnostics with explicit database, AI summary, freshness, published, and duplicate filter messages.
- Show App mode database path and candidate counts in combined source output.
- Use the active App mode database consistently when checking already-published items.

## v1.0.0 - 2026-06-13

- Initialize Maritime Intelligence Hub as a real project from the desktop app template.
- Define planning-first scope for source import, crawler pipeline, AI processing, and brief generation.
- Defer complex GUI work until the core pipeline is validated.
- Add CLI source validation, P1 fetch planning, readiness brief generation, and source master self-tests.
- Add SQLite-backed MVP pipeline with RSS live fetch, HTML dry-run, scoring, mock AI summaries, and Markdown/JSON brief outputs.
- Add approved HTML metadata fetch, configurable AI provider with mock fallback, trend-aware hotness scoring, Google Trends CSV/RSS ingestion, and sectioned hot maritime brief output.
- Add visual brief cards, Telegram publishing controls, scheduled GUI operation, timezone selection, and bundled Playwright Chromium build support.
