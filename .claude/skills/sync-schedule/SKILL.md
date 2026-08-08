---
name: sync-schedule
description: Sync a schedule spreadsheet (xlsx/xls/csv — e.g. a Pinterest content calendar, a 30-day launch plan, any day-by-day task sheet) into the Daily Schedule Builder app's task store, so it shows up in the weekly calendar. Use this whenever the user attaches or references a schedule spreadsheet and asks to import, sync, save, update, or "put this in the scheduler." Also use it when they say a schedule was "rewritten," "changed," or "some events got removed" — this skill reconciles automatically (adds new rows, updates changed ones, deletes rows that vanished from the file) rather than just appending duplicates.
---

# Sync a schedule spreadsheet into the scheduler app

This skill runs `scripts/sync_schedule.py`, which reads a spreadsheet, figures out which
rows are tasks, and reconciles them into the same database (or local CSV) the Daily
Schedule Builder Streamlit app (`app.py`) reads from — so the sync is visible next time
the app loads, no manual upload through the browser required.

## Why this exists instead of just uploading through the app's UI

The app's own file-upload tab only *appends* rows — every re-upload of an edited
schedule would create duplicates. This skill instead treats each spreadsheet, identified
by its filename, as representing the complete current state of that schedule: rows that
are new get added, rows that changed get updated in place, and rows that were deleted
from the spreadsheet get deleted from the app too. Nothing outside that one filename is
ever touched — other schedules and hand-added tasks are untouched.

## Running it

```bash
python .claude/skills/sync-schedule/scripts/sync_schedule.py "<path-to-spreadsheet>" [--sheet "Sheet Name"]
```

Run it from the repo root (`/Volumes/ChiselledBox/codebase`), or use an absolute path to
the script — it locates `schedule_core.py` relative to its own position, not the cwd.

**Sheet selection**: if the workbook has multiple sheets, the script auto-picks the first
one containing both a task-like column (e.g. "Task", "Action Items", "Description") and a
date-like column. Pass `--sheet` to override if it picks the wrong one (e.g. the workbook
also has reference/tracker sheets that aren't the actual schedule).

## Sourcing DATABASE_URL

The script uses the exact same persistence as the app: if `DATABASE_URL` is set in the
environment, it writes to that Postgres database; otherwise it falls back to the local
`tasks_data.csv` file. Check for it before running:

```bash
echo ${DATABASE_URL:+is set}
```

- If it's set, just run the script — it'll pick it up automatically.
- If it's unset and the user wants this synced to the *live* app (not just local testing),
  ask them for the connection string rather than guessing or reusing one from memory —
  connection strings expire, get rotated, or may not be what you last saw. Export it
  inline for the single command only:
  ```bash
  DATABASE_URL='...' python .claude/skills/sync-schedule/scripts/sync_schedule.py "<path>"
  ```
  Never write the value into a file that could be committed, never print it back, and
  don't persist it in the shell's history-worthy state beyond that one command.
- If the user only wants a local dry run against the CSV fallback, just run it without
  the env var.

## What "the same schedule" means

Two spreadsheets are treated as the same schedule if they have the **same filename**
(basename — the directory doesn't matter). If the user renamed the file since the last
sync, this skill has no way to know it's the same schedule — it'll be treated as a brand
new one, additive to whatever's already there. If that comes up, tell the user rather
than silently guessing.

## Row matching within a schedule

Rows are matched across re-syncs by a natural key: the script looks for a column that
looks like an ordinal or index (day, index, row, #, no., num, number, order — case
insensitive), and uses its value. If no such column exists, it falls back to the row's
position in the sheet (0-based) — which works fine as long as rows aren't reordered
between syncs, but means inserting a row in the middle of an unordered sheet can shift
the matching. Mention this to the user if their sheet lacks an explicit ordinal column
and they're doing structural edits (not just changing text/dates in place).

## First sync of a file previously uploaded through the app's UI

If rows from this filename already exist in the database from a manual upload through
the app (rather than a previous run of this skill), their ids won't match this skill's
deterministic scheme — the first sync will report those rows as a full delete+add pair
even though the visible content is identical. This is expected and harmless (task text,
dates, and categories carry over correctly); it's just the id scheme converging. Every
sync after that first one behaves as a normal, clean reconcile.

## After running

The script prints a summary — counts of added/updated/deleted/unchanged, plus a
one-line description of every changed row. Relay this to the user rather than just
saying "done," especially the deleted rows — those are the ones worth double-checking
since they came from an inference (natural key match), not an explicit instruction.

If the app is deployed (e.g. on Render) and the sync targeted its `DATABASE_URL`, the
change is live immediately — no redeploy needed, since it's a database write, not a code
change.
