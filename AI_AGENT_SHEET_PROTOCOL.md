# AI Agent Google Sheet Protocol v1

The scheduled AI agent must publish one complete, identifiable snapshot for each Vietnam-time slot. Article columns remain `A:K`.

## Run identity

- Morning: `YYYY-MM-DD:morning`, scheduled at `07:15 Asia/Bangkok`.
- Evening: `YYYY-MM-DD:evening`, scheduled at `19:15 Asia/Bangkok`.
- Timestamps must be full ISO-8601 values with a timezone, for example `2026-08-01T07:15:12+07:00`.

## Required write sequence

1. Before collecting, write `L1=started_at`, clear `M1`, write `N1=run_id`, `O1=RUNNING`, and clear `P1/Q1`.
2. Collect and validate the complete article set off-sheet.
3. Replace the old article rows in `A2:K` with the complete new set. Do not append to the previous run.
4. Count every non-empty written article row. Every row must contain a usable title and an absolute `http://` or `https://` Source URL.
5. As the final write, set `M1=completed_at`, `O1=COMPLETED`, `P1=row_count`, and keep `Q1` empty.
6. If the run cannot complete, set `O1=FAILED`, keep `M1/P1` empty, and put a short non-sensitive error code in `Q1`.

## Safety rules

- Never set `COMPLETED` before all `A:K` rows are final.
- Never reuse yesterday's `run_id`.
- Never leave `M1` or `P1` populated when starting a new run.
- Do not place API keys, tokens, stack traces, or private content in `Q1`.
- A successful run with zero articles uses `O1=COMPLETED` and `P1=0`; the app records `NO_NEW_CONTENT` and does not activate backup.

## Example completed morning run

| Cell | Value |
| --- | --- |
| `L1` | `2026-08-01T07:15:12+07:00` |
| `M1` | `2026-08-01T07:21:34+07:00` |
| `N1` | `2026-08-01:morning` |
| `O1` | `COMPLETED` |
| `P1` | `6` |
| `Q1` | empty |

The Python consumer rejects stale, partial, malformed, or mismatched snapshots and activates backup only after an explicit `FAILED` status or the configured deadline.
