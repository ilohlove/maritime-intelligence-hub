# DATA_SOURCE_MASTER.md

## Purpose

Define how Maritime Intelligence Hub manages news sources before any crawler or AI code depends on them.

## Source Priority

- `P1`: MVP sources; fetch first.
- `P2`: add after P1 is stable.
- `P3`: future or specialist sources.

## Fetch Order

1. RSS feed.
2. HTTP HTML fetch with requests and parser rules.
3. Scrapy only when source complexity justifies it.
4. Playwright only for sources that require browser rendering.

## Required Source Decisions

Each active source should define:

- source owner/name;
- website;
- country;
- language;
- type;
- category;
- priority;
- RSS availability;
- RSS URL if available;
- crawl method;
- frequency;
- audience;
- quality score;
- business value score;
- crawl difficulty;
- copyright risk;
- AI summary permission;
- status.

## Change Rules

- Do not add a source directly in code.
- Add or update source data in the source master first.
- Validate source data before running fetch jobs.
- Treat missing legal notes as a release blocker before enabling publishing.
- Keep disabled/future sources in the master for planning, but do not fetch them by default.

## Backup feed master

`BACKUP_FEED_MASTER.csv` stores non-Sheet feed definitions and provider
metadata. It is intentionally separate from the source master so Google News
queries and RSSHub routes can be enabled without hard-coding them in Python.
The combined brief tries Sheet first and activates this lane only when Sheet
data is unavailable or empty.

The optional “Không lấy tin tức từ nguồn Việt Nam” policy filters by source
country, Vietnamese domains, and resolved publisher URL before article text or
AI processing.
