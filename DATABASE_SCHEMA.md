# DATABASE_SCHEMA.md

## Runtime database

The current single-machine deployment uses `data/mih.db` (SQLite). Every connection enables foreign keys, WAL mode, and a 5-second busy timeout. Run and delivery claims use `BEGIN IMMEDIATE` so two processes cannot own the same scheduled work.

PostgreSQL is a future migration only when collection or publishing runs on multiple machines. The coordinator contract and identifiers must remain unchanged so that migration does not alter business behavior.

## Intelligence tables

- `sources`: source master data, crawl configuration, quality scores, status, and legal notes.
- `articles`: source-linked metadata, canonical URL, normalized title/title hash, timestamps, extraction metadata, scores, and processing status.
- `article_summaries`: AI headline, Vietnamese summary, impact note, category, score, provider/model, prompt version, and fallback diagnostics.
- `briefs`: generated Morning, Evening, and Weekly brief payloads.
- `fetch_logs`: isolated source fetch results and errors.
- `trend_keywords`: curated or fetched trend signals used during scoring.
- `published_items`: legacy cross-run item ledger used to prevent reposting already-published content.

## Scheduled run ownership

### `news_runs`

One row owns one logical slot. `run_id` is the primary key and has the form `YYYY-MM-DD:morning` or `YYYY-MM-DD:evening` in `Asia/Bangkok`.

Important fields are `lane`, `state`, `owner`, `deadline`, `lease_expires_at`, `heartbeat_at`, `error_code`, `stats_json`, and lifecycle timestamps. `lane` remains null while waiting and becomes exactly `primary` or `backup` once selected; it must not switch afterward.

Allowed states are:

```text
WAIT_PRIMARY
  -> PRIMARY_SELECTED | BACKUP_SELECTED
  -> RENDERING
  -> PUBLISHING
  -> SUCCEEDED | NO_NEW_CONTENT | FAILED
```

`NO_NEW_CONTENT` is a successful terminal decision: the chosen lane completed, but nothing remains after validation/deduplication. It must not trigger backup. A worker renews the default 300-second lease every 30 seconds. Another worker may reclaim only an expired active run. An operator-approved `publish-run` retry takes a separate exclusive lease on the terminal row and appends its reconciliation result to `stats_json` without rewriting the historical terminal state.

### `publish_deliveries`

One row represents one item sent to one destination on one channel. The unique key `(run_id, item_key, channel, destination)` makes delivery creation idempotent. Status is `sending`, `succeeded`, `failed`, or `needs_review`; attempt, lease, payload, result, and error fields support channel-independent recovery.

The worker must claim the row before calling Telegram, a Facebook Page, or a Facebook Group. Scheduled claims renew and verify the owning run lease immediately before delivery. A stale `sending` row becomes `needs_review`, because the external service may have accepted the request before the process stopped; it is never resent automatically without confirmation. `resolve-delivery` records reviewer, note, timestamp, and either confirmed success or permission to retry. Confirmed success also writes `published_items` so a later run cannot repost it; `publish-run` performs an explicitly approved retry from the frozen run output under an exclusive terminal-run lease.

### Facebook Group delivery tables

- `facebook_group_deliveries`: one group delivery per content batch, with queue, expiry, priority, attempt, moderation state, owner, lease, and quota reservation token. Unique `(batch_id, group_id)` plus an atomic `sending` claim prevents two GUI/CLI processes from opening the same group post. An expired in-flight claim becomes `needs_review`.
- `facebook_group_attempts`: append-only attempt/quota log. A `reserved` row is created in the same `BEGIN IMMEDIATE` transaction as the delivery claim, then finalized or released, so concurrent batches cannot exceed the Vietnam-calendar-day limit. Cancel after operator review records `cancelled/confirmed_no_post`, clears the delivery token, and releases quota; dry-runs and login-only failures do not consume quota.

## Migration policy

`init_db()` creates missing tables and indexes without deleting existing rows. Additive column migrations run under a SQLite write lock, re-check schema after contention, and retry the WAL transition within `busy_timeout`. Existing `published_items` and Facebook ledgers are retained; a release must test concurrent opening of an existing database as well as creating a clean database.
