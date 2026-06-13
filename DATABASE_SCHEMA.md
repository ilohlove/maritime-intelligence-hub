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

### briefs

Stores generated Morning, Evening, and Weekly briefs.

### brief_items

Links selected articles to briefs with ordering and editorial notes.

### fetch_logs

Stores each source fetch attempt, status, error, and duration.

### dedup_matches

Stores duplicate relationships and matching reason.

## MVP Database Choice

Use PostgreSQL for the real service. SQLite may be used only for quick local prototype work if it does not change the schema design.
