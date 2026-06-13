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
