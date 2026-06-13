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

## Current Acceptance Commands

```bat
python -m unittest discover -s tests
python -m compileall app tests
python -m app.main validate-sources
python -m app.main run-pipeline --priority P1
```
