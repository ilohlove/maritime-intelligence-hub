# MVP_ROADMAP.md

## Sprint 0 - Planning

- Normalize source master. Status: done.
- Confirm category and audience masters. Status: done for MVP baseline.
- Confirm legal/copyright rules. Status: done for metadata/link-first MVP.
- Confirm MVP output format. Status: done for Markdown/JSON.
- Confirm database choice. Status: SQLite for MVP.

## Sprint 1 - Data Foundation

- Import source master. Status: implemented.
- Validate source schema. Status: implemented.
- Store sources. Status: implemented with SQLite.
- Add logging. Status: implemented for CLI/fetch stages.
- Add self-tests. Status: implemented.

## Sprint 2 - RSS Collector

- Fetch RSS-ready P1 sources. Status: implemented with live RSS/autodiscovery.
- Store metadata. Status: implemented.
- Deduplicate by URL and title. Status: implemented.
- Handle source failure without crashing. Status: implemented.

## Sprint 3 - HTML Collector

- Add approved P1 HTML sources. Status: dry-run implemented.
- Extract title, date, URL, and usable text/excerpt. Status: deferred until source-specific parser approval.
- Add retry and parser diagnostics. Status: deferred for live HTML crawler.

## Sprint 4 - AI Processing

- Classify category. Status: source/category based MVP.
- Score importance. Status: implemented with rules.
- Generate Vietnamese summaries. Status: implemented with mock AI interface.
- Generate maritime impact notes. Status: implemented with mock AI interface.
- Track token usage. Status: implemented as 0 for mock mode.

## Sprint 5 - Brief Generator

- Generate Morning Brief. Status: implemented.
- Generate Evening Brief. Status: implemented.
- Generate Weekly Brief. Status: implemented.
- Export Markdown. Status: implemented.
- Export JSON. Status: implemented.

## Sprint 6 - Publishing Preparation

- Prepare Telegram-ready format. Status: file output only.
- Prepare website/API-ready JSON. Status: implemented.
- Add publish safety checks. Status: source URL required in outputs.
- Add operational self-test checklist. Status: implemented.
