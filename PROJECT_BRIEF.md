# PROJECT_BRIEF.md

## Project Name

Maritime Intelligence Hub

## Project Slug

maritime-intelligence-hub

## Purpose

Build an AI-assisted maritime news intelligence system that becomes the fastest practical source for curated maritime updates for Vietnamese maritime, shipping, port, logistics, and import-export communities.

The first phase prioritizes a reliable source master, crawler/fetch pipeline, deduplication, AI summarization, scoring, and brief generation. A complex GUI is intentionally deferred until the core pipeline is proven.

## Main Features

- Source master import and validation.
- RSS-first collection for selected maritime sources.
- HTML crawler fallback for approved sources without RSS.
- Metadata storage with source URL retention.
- Duplicate detection before AI processing.
- Category classification and importance scoring.
- AI-generated Vietnamese summaries and maritime impact notes.
- Morning Brief and Evening Brief text output.
- Publishing-ready Markdown/JSON output for Telegram, social, and future website/API use.

## GUI Requirements

- No complex GUI is required for the first implementation phase.
- The initial build may run through CLI, scheduled jobs, or a lightweight service.
- A future admin UI should act as a newsroom control panel for source management, article review, brief editing, publish actions, and logs.

## Business Rules

- Keep app-specific logic in `app/services/business_logic.py`.
- Keep framework files stable unless a real project needs a framework change.
- Never release while required metadata is incomplete.
- Do not hard-code news sources in code; read sources from source master/config.
- Fetch RSS before using HTML crawler.
- Deduplicate before AI processing to control cost.
- Do not republish full original articles.
- Always store and display the original source URL.
- Keep legal/copyright rules explicit before enabling any publisher.

## Input Data

- `NEWS_SOURCE_MASTER.csv`
- Future normalized source workbook or CSV exports.
- RSS feeds and allowed HTML pages from approved sources.

## Output Data

- Article metadata.
- Cleaned excerpts or minimal content needed for summarization.
- AI summaries and impact notes.
- Importance scores.
- Morning Brief, Evening Brief, and later Weekly Brief in Markdown/JSON.

## Project Notes

- Current date for project initialization: 2026-06-09.
- `latest_json_url` is intentionally empty until the GitHub repository/raw URL is confirmed.
- Do not prepare a release until metadata, security checks, and release workflow requirements are satisfied.
