# Daily Schedule Builder — project notes

A Streamlit app for importing schedules (spreadsheet/PDF) and viewing them on an
adjustable-density weekly calendar. Built iteratively in one long session — this file
is a handoff doc so a future session (this chat or a new one) can pick up context fast.

## Live

- **App**: https://scheduler.bdl.nyc (custom domain, real HTTPS via Render)
- **Render URL** (fallback): https://scheduler-4ovu.onrender.com
- **GitHub**: https://github.com/brassman-design-lab/scheduler
- **Database**: Supabase Postgres (see "Secrets" below)

## Architecture

- **`app.py`** — the Streamlit UI. Three tabs: Weekly calendar, Upload files, Add a
  task manually.
- **`schedule_core.py`** — all non-UI logic: column detection, category guessing,
  date parsing, persistence. Deliberately has zero Streamlit dependency so it can be
  imported by tools outside the app (like the sync-schedule skill) without executing
  any UI code.
- **`.claude/skills/sync-schedule/`** — a Claude Code skill that reconciles a schedule
  spreadsheet into the task store: matches rows by filename + a natural key (day/index
  column, or row position), upserts changed rows, deletes rows removed from the source
  file. Preserves `Done` state across re-syncs since spreadsheets don't carry it.
- **`.streamlit/config.toml`** — base theme (colors) matching the "Organic" design
  system from the Scheduler Mockups Claude Design project
  (`claude.ai/design/p/54300750-ead0-406d-809e-0d8a38557c40`). `app.py` layers fonts
  (Caprasimo/Figtree), pill buttons, and category-colored capsule borders via injected
  CSS on top of that base theme.
- **`render.yaml`** — Render Blueprint config (build/start commands, declares the
  `DATABASE_URL` env var slot with `sync: false` so the value stays out of git).

## Data model

Each task row: `Task, Date, Category, Notes, Done, Source, _id, _import_id`.
`Source`/`_id`/`_import_id` are hidden from the UI tables and CSV export.

- **Category** is one of 4 fixed values, each with a hex color (`CATEGORY_META` in
  `schedule_core.py`): Social Media (sage), Advertising (terracotta), Product
  Design/Execution (deep sage), Other (sand). Auto-guessed from task text via keyword
  matching (`CATEGORY_KEYWORDS`) unless a category column is explicitly mapped.
- **Done** — checkbox per task, strikethrough + dimmed when checked.
- **`_import_id`** — tags every task added through the Upload tab with a batch id, so
  the import-history list can undo a whole batch at once. Manually-added tasks don't
  get one (they use a separate lightweight "Just added" undo list instead).

## Persistence

`schedule_core.get_engine()` uses `DATABASE_URL` if set (Postgres via SQLAlchemy),
otherwise falls back to local CSV files (`tasks_data.csv`, `import_history.csv` — both
gitignored). `load_tasks()` runs `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` on startup,
so schema changes are self-migrating against the live table.

## Deployment

Push to `main` on GitHub. Render **auto-deploy has been unreliable** in this project —
pushes have repeatedly needed a manual nudge:

1. Render dashboard → `scheduler` **Web Service** (not the Blueprint page — that only
   tracks `render.yaml` sync, a common mix-up)
2. **Manual Deploy** → **Deploy latest commit**

Worth checking `Settings → Build & Deploy → Auto-Deploy` is actually set to "Yes" if
this keeps happening.

## Secrets

`DATABASE_URL` (Supabase pooler connection string) lives only in:
- Render → `scheduler` service → Environment tab
- Your own Supabase dashboard

It is **not** in git anywhere. It was pasted in chat a few times during setup — worth
rotating (Supabase → Project Settings → Database → Reset Database Password) and
updating the Render env var to match, if that hasn't been done yet.

## Known caveats

- **Render free tier sleeps** after ~15 min idle; first load after that takes 30-60s
  to cold-start.
- **Checkbox clicks didn't register in this session's browser-automation tool** — verified
  directly that the data layer and rendering (strikethrough/dimming) both work
  correctly, and the click-handling code follows the exact same pattern as the capsule
  expand/collapse toggle (which does work). Root-caused to the automation tool, not the
  app — even a bare `st.checkbox` with no custom code around it failed to toggle the
  same way. Worth a real click to confirm, but there's no known reason it wouldn't work
  for an actual user.
- **Local dev venv** (`.venv`) lives on an external volume; the sandboxed `preview_start`
  tool can't access it (`PermissionError` on `pyvenv.cfg`). Workaround: start Streamlit
  directly via Bash (`nohup .venv/bin/streamlit run app.py --server.headless true
  --server.port 8501 &`) instead of using `preview_start` with `.claude/launch.json`.

## Running locally

```bash
cd /Volumes/ChiselledBox/codebase
.venv/bin/streamlit run app.py
```

Without `DATABASE_URL` set, it uses the local CSV fallback automatically — safe to
experiment with, won't touch production data.

## Using the sync-schedule skill

```bash
python .claude/skills/sync-schedule/scripts/sync_schedule.py "<path-to-spreadsheet>" [--sheet "Sheet Name"]
```

Set `DATABASE_URL` inline (not persisted to a file) if syncing against the live
database rather than local CSV. See `.claude/skills/sync-schedule/SKILL.md` for full
details on the reconciliation rules.

## Rough build history

1. Initial Streamlit app: upload spreadsheet/PDF, auto-detect columns, weekly calendar
2. Category auto-tagging (Social Media / Advertising / Product Design / Other)
3. Collapsible task capsules, single-open accordion
4. Postgres persistence (Supabase) — local CSV alone didn't survive host restarts
5. Deployed: GitHub → Render (Streamlit Community Cloud doesn't support custom
   domains, hence the move) → custom domain `scheduler.bdl.nyc` with real HTTPS
6. Tabbed navigation (Weekly calendar / Upload files / Add a task manually)
7. Adjustable calendar zoom (1/3/5/7 days, 4-week stacked grid)
8. `sync-schedule` skill for reconciling an edited spreadsheet without duplicating rows
9. Full visual reskin + Done state + import history, implementing the Scheduler
   Mockups Claude Design project
