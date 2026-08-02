# CRAWLER_ARCHITECTURE.md

## Purpose

Define a deterministic collection architecture in which the AI-produced Google Sheet is primary and local feeds are a mutually exclusive backup.

## Scheduled coordinator

The canonical slots are `07:15` and `19:15` in `Asia/Bangkok`. Each slot maps to one `run_id`: `YYYY-MM-DD:morning|evening`. The coordinator claims that ID in SQLite before fetching, rendering, or publishing, so restarts and concurrent processes resolve to the same logical run.

The run follows this state sequence:

```text
WAIT_PRIMARY -> PRIMARY_SELECTED | BACKUP_SELECTED
             -> RENDERING -> PUBLISHING
             -> SUCCEEDED | NO_NEW_CONTENT | FAILED
```

While in `WAIT_PRIMARY`, poll the primary snapshot every 60 seconds until its explicit failure or the 30-minute deadline. Network errors, missing markers, and `RUNNING` are retryable waiting conditions. A completed snapshot wins only when `M1` is at or before the persisted run deadline. Once a lane is selected, it owns the entire run; primary data arriving after `BACKUP_SELECTED` is logged and ignored for that `run_id`.

Catch-up is limited to 120 minutes after a scheduled slot. A later invocation does not create a second run for the same slot.

## Primary Google Sheet protocol

Article data stays in columns `A:K`. Control cells in row 1 are:

| Cell | Field | Contract |
| --- | --- | --- |
| `L1` | `started_at` | ISO-8601 timestamp with timezone |
| `M1` | `completed_at` | ISO-8601 timestamp with timezone; empty while running |
| `N1` | `run_id` | Exact expected slot ID |
| `O1` | `status` | `RUNNING`, `COMPLETED`, or `FAILED` |
| `P1` | `row_count` | Non-negative integer written on completion |
| `Q1` | `error_code` | Short failure code; otherwise empty |

The AI agent first writes `RUNNING` and clears `M1`, `P1`, and `Q1`. It then replaces the article rows and writes `COMPLETED`, `M1`, and `P1` last. On failure it writes `FAILED` and `Q1`.

The application downloads control cells and rows in one CSV snapshot. A primary result is usable only when `run_id` matches, status is `COMPLETED`, both timestamps are valid and ordered, and `P1` equals the usable data-row count. Rows from an earlier date or a different run are never rendered.

## Collection Strategy

The selected lane runs by itself:

1. Primary: consume the validated Google Sheet snapshot only.
2. Backup: read enabled sources from `BACKUP_FEED_MASTER.csv`, preferring approved official RSS and then Google News RSS or configured RSSHub routes.
3. Extract article text with Jina Reader, then Trafilatura, then BeautifulSoup.
4. Use Playwright only when JavaScript rendering is unavoidable.

Do not pre-fetch backup sources while primary is waiting, and do not merge lanes into one brief. After lane selection, canonicalize URLs and deduplicate once by canonical URL and normalized-title hash before AI processing.

## MVP Source Order

Start with active P1 sources.

RSS-ready P1 sources:

- Safety4Sea.
- Splash247.
- Maritime Executive.
- gCaptain.
- Marine Insight.
- Hai Quan Online.

Partial RSS P1 sources:

- BIMCO.
- Seatrade Maritime.

HTML P1 sources:

- IMO.
- ICS.
- Maersk News.
- MSC Newsroom.
- CMA CGM News.
- Hapag-Lloyd News.
- Vinamarine.
- Vietnam Register.
- Saigon Newport.
- VIMC.

## Reliability Rules

- One failed source must not stop the full run.
- Log source ID, URL, status code, error message, and retry count.
- Log `run_id`, Sheet status, selected lane, deadline, row counts, duplicate/published counts, duration, and each delivery result.
- Store fetch time and retain the original source URL.
- Respect configured frequency.
- Respect disabled/future status.
- Respect legal notes and robots decisions before publishing.
- Write brief JSON, Markdown, and render artifacts under `output/runs/{filesystem_run_key}/`, where `2026-08-01:morning` maps to Windows-safe `2026-08-01_morning`; write to a temporary file and finalize with `os.replace`.
- If primary completes with zero usable/new items, finish as `NO_NEW_CONTENT`; do not select backup and do not poll forever.
- If every configured backup source fails or no backup plan can run, finish as `FAILED`, not `NO_NEW_CONTENT`.
- Freeze the non-secret publish plan in the run brief before network delivery. Recovery from `PUBLISHING` must reuse that brief and manifest.

## Backup lane

Backup starts exactly once only when primary reports `FAILED` or does not become valid by the deadline. Feed failures are isolated and recorded; no full article is republished. A late primary completion cannot replace the claimed backup lane. All-source failure is a run failure; a successful fetch with no eligible new articles is `NO_NEW_CONTENT`.
