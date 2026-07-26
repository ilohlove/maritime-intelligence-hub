# DATABASE_SCHEMA.md

## Purpose

Plan the database before implementation.

## Core Tables

### sources

Stores source master data, status, crawl settings, quality scores, and legal notes.

### articles

Stores article metadata including source ID, title, URL, published time, fetched time, language, and dedup hash.

### article_contents

Stores cleaned excerpt or limited content required for AI processing.

### article_summaries

Stores AI output, category, importance score, impact note, model, prompt version, and token usage.
It also stores the selected provider, optional image prompt, and fallback errors.

### briefs

Stores generated Morning, Evening, and Weekly briefs.

### brief_items

Links selected articles to briefs with ordering and editorial notes.

### fetch_logs

Stores each source fetch attempt, status, error, and duration.

### dedup_matches

Stores duplicate relationships and matching reason.

### facebook_group_deliveries

Stores one delivery per Facebook Group and content batch. The unique `(batch_id, group_id)` pair prevents duplicate submissions. Status values include `queued`, `published`, `pending`, `failed`, `needs_login`, `expired`, and `cancelled`. Scheduling time, expiry, group priority, attempt count, safety stop reason, saved publish payload, and an optional post URL support quota enforcement, rotation, selected retry, and queue recovery.

### facebook_group_attempts

Append-only log of real publish attempts used to enforce the Vietnam-calendar-day quota correctly, including repeated manual retries of the same delivery. Dry-runs, queued rows, and login-only failures do not consume quota.

## MVP Database Choice

Use PostgreSQL for the real service. SQLite may be used only for quick local prototype work if it does not change the schema design.
