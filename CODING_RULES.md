# CODING_RULES.md

## Scope Rules

- Build the core pipeline before building a complex GUI.
- Keep modules small and testable.
- Do not add non-MVP features without explicit approval.

## Source Rules

- Do not hard-code news sources in code.
- Read sources from the source master or validated config.
- Ignore disabled/future sources unless explicitly requested.

## Fetch Rules

- Prefer RSS.
- Use HTML fetch only for approved sources.
- Do not use Playwright unless necessary.
- One source failure must not stop the full run.

## AI Rules

- Deduplicate before AI processing.
- Do not send low-value articles to AI.
- Cache AI output by article hash.
- Store prompt/model metadata and token usage.

## Legal Rules

- Do not republish full original articles.
- Always store source name and original URL.
- Respect copyright risk settings.

## Quality Rules

- Add logging for every pipeline stage.
- Add self-tests for each module.
- Make each module rerunnable without creating duplicate data.
