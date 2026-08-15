"""Daily Schedule Builder — upload spreadsheets/PDFs, extract tasks + dates, view sorted by day."""

import io
import re
import uuid
from datetime import timedelta

import pandas as pd
import streamlit as st

from schedule_core import (
    CATEGORIES,
    CATEGORY_KEYWORDS_COLUMN,
    CATEGORY_META,
    DATE_KEYWORDS,
    HIDDEN_COLUMNS,
    TASK_KEYWORDS,
    empty_df,
    guess_category,
    guess_column,
    load_import_history,
    load_tasks,
    normalize_category,
    save_import_history as _save_import_history,
    save_tasks as _save_tasks,
    try_parse_date,
)

st.set_page_config(page_title="Daily Schedule Builder", layout="wide")

VIEW_OPTIONS = ["1 Day", "3 Days", "5 Days", "7 Days", "4 Weeks"]
VIEW_SPANS = {"1 Day": 1, "3 Days": 3, "5 Days": 5, "7 Days": 7, "4 Weeks": 28}
WEEK_ALIGNED_MODES = {"7 Days", "4 Weeks"}


def save_tasks():
    _save_tasks(st.session_state.tasks_df)


def save_import_history():
    _save_import_history(st.session_state.import_history)


if "tasks_df" not in st.session_state:
    st.session_state.tasks_df = load_tasks()

if "import_history" not in st.session_state:
    st.session_state.import_history = load_import_history()

if "expanded_task" not in st.session_state:
    st.session_state.expanded_task = None

if "recent_manual" not in st.session_state:
    st.session_state.recent_manual = []


# Palette/fonts match the "Organic" design system used in the Scheduler Mockups.
THEME_CSS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Caprasimo&family=Figtree:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
html, body, [class*="css"] { font-family: 'Figtree', system-ui, sans-serif; }
h1, h2, h3, h4, h5, h6 {
    font-family: 'Caprasimo', system-ui, sans-serif !important;
    font-weight: 400 !important;
    letter-spacing: -0.01em;
}
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    border-radius: 999px !important;
    font-family: 'Figtree', system-ui, sans-serif;
    font-weight: 600;
}
[data-testid="stExpander"] {
    border-radius: 16px !important;
    border: 1px solid rgba(32,30,29,.16) !important;
}
.stTextInput input, [data-baseweb="select"] > div { border-radius: 999px !important; }
.stTextArea textarea { border-radius: 16px !important; }

.dsb-legend { display:flex; flex-wrap:wrap; gap:16px; font-size:12px; color:rgba(32,30,29,.6); margin: 4px 0 14px; }
.dsb-legend-item { display:flex; align-items:center; gap:6px; }
.dsb-dot { width:9px; height:9px; border-radius:999px; display:inline-block; flex:none; }
</style>
"""

CALENDAR_CSS = """
<style>
div.st-key-calendar_grid { overflow-wrap: break-word; }
div.st-key-calendar_grid [data-testid="column"] { min-width: 0; }
div.st-key-calendar_grid button {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    display: block;
    border-radius: 999px !important;
    padding: 0.15rem 0.75rem !important;
    font-size: 0.8rem !important;
    text-align: left !important;
    min-height: 1.8rem !important;
    margin-bottom: 0.2rem !important;
}
div.st-key-calendar_grid button p {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: left;
}
div.st-key-calendar_grid .stMarkdown, div.st-key-calendar_grid .stCaption {
    overflow-wrap: break-word;
    word-break: break-word;
}
</style>
"""


def render_legend():
    items = "".join(
        f'<span class="dsb-legend-item"><span class="dsb-dot" style="background:{meta["color"]}"></span>{cat}</span>'
        for cat, meta in CATEGORY_META.items()
    )
    st.markdown(f'<div class="dsb-legend">{items}</div>', unsafe_allow_html=True)


def render_task_capsule(row):
    task_id = row["_id"]
    is_open = st.session_state.expanded_task == task_id
    is_done = bool(row["Done"])
    color = CATEGORY_META[row["Category"]]["color"]

    with st.container(key=f"cap_{task_id}"):
        done_style = "opacity:.55;text-decoration:line-through;" if is_done else ""
        st.markdown(
            f"<style>div.st-key-cap_{task_id} button{{border-left:4px solid {color} !important;{done_style}}}</style>",
            unsafe_allow_html=True,
        )
        chk_col, btn_col = st.columns([0.13, 0.87])
        new_done = chk_col.checkbox(
            "Done", value=is_done, key=f"done_{task_id}", label_visibility="collapsed"
        )
        if new_done != is_done:
            idx = st.session_state.tasks_df.index[st.session_state.tasks_df["_id"] == task_id]
            st.session_state.tasks_df.loc[idx, "Done"] = new_done
            save_tasks()
            st.rerun()
        icon = "▾" if is_open else "▸"
        if btn_col.button(f"{icon} {row['Task']}", key=f"cap_btn_{task_id}", use_container_width=True):
            st.session_state.expanded_task = None if is_open else task_id
            st.rerun()

    if is_open:
        with st.container(border=True):
            st.markdown(f"**{row['Task']}**")
            date_label = row["Date"].strftime("%b %d, %Y") if pd.notna(row["Date"]) else "Unscheduled"
            status = " · Done" if is_done else ""
            st.caption(f"{row['Category']} · {date_label}{status}")
            notes = str(row.get("Notes", "") or "").strip()
            if notes:
                st.write(notes)


def read_spreadsheet(uploaded_file):
    name = uploaded_file.name
    if name.lower().endswith(".csv") or name.lower().endswith(".tsv"):
        sep = "\t" if name.lower().endswith(".tsv") else None
        return pd.read_csv(uploaded_file, sep=sep, engine="python")
    return pd.read_excel(uploaded_file)


DATE_LINE_RE = re.compile(
    r"("
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{4}[/-]\d{1,2}[/-]\d{1,2}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{2,4})?"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:,?\s*\d{2,4})?"
    r")",
    re.IGNORECASE,
)


def parse_pdf_lines(uploaded_file):
    import pdfplumber

    rows = []
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw_line in text.split("\n"):
                line = raw_line.strip()
                if len(line) < 3:
                    continue
                match = DATE_LINE_RE.search(line)
                if not match:
                    continue
                date_str = match.group(0)
                parsed = try_parse_date(date_str)
                if parsed is None:
                    continue
                task_text = (line[: match.start()] + " " + line[match.end() :]).strip(" -:\t")
                task_text = re.sub(r"\s+", " ", task_text).strip()
                if not task_text:
                    task_text = line
                rows.append({"Task": task_text, "Date": parsed, "Category": guess_category(task_text)})
    return pd.DataFrame(rows, columns=["Task", "Date", "Category"])


def append_tasks(task_series, date_series, notes_series, category_series, source, import_id=""):
    n = len(task_series)
    tasks_list = list(task_series)
    if category_series is not None:
        categories = [normalize_category(c, t) for c, t in zip(category_series, tasks_list)]
    else:
        categories = [guess_category(t) for t in tasks_list]
    new_rows = pd.DataFrame(
        {
            "Task": tasks_list,
            "Date": [try_parse_date(d) for d in date_series] if date_series is not None else [None] * n,
            "Category": categories,
            "Notes": list(notes_series) if notes_series is not None else [""] * n,
            "Done": [False] * n,
            "Source": [source] * n,
            "_id": [uuid.uuid4().hex for _ in range(n)],
            "_import_id": [import_id] * n,
        }
    )
    new_rows = new_rows[new_rows["Task"].astype(str).str.strip() != ""]
    st.session_state.tasks_df = pd.concat([st.session_state.tasks_df, new_rows], ignore_index=True)
    save_tasks()
    return new_rows


def record_import_history(import_id, filename, added_count, skipped_count):
    entry = pd.DataFrame(
        [
            {
                "import_id": import_id,
                "filename": filename,
                "imported_at": pd.Timestamp.now(),
                "count_added": added_count,
                "count_skipped": skipped_count,
            }
        ]
    )
    st.session_state.import_history = pd.concat([st.session_state.import_history, entry], ignore_index=True)
    save_import_history()


def render_import_history():
    hist = st.session_state.import_history
    if hist.empty:
        return
    st.markdown("##### Import history")
    st.caption("Undo an import to remove its tasks.")
    for _, entry in hist.sort_values("imported_at", ascending=False).iterrows():
        c1, c2 = st.columns([5, 1])
        ts = pd.Timestamp(entry["imported_at"]).strftime("%b %d, %I:%M %p")
        skipped = int(entry["count_skipped"]) if pd.notna(entry["count_skipped"]) else 0
        skipped_note = f" · {skipped} skipped" if skipped else ""
        c1.markdown(f"**{entry['filename']}** — {ts} · {int(entry['count_added'])} tasks added{skipped_note}")
        if c2.button("Undo", key=f"undo_import_{entry['import_id']}"):
            iid = entry["import_id"]
            st.session_state.tasks_df = st.session_state.tasks_df[st.session_state.tasks_df["_import_id"] != iid]
            save_tasks()
            st.session_state.import_history = hist[hist["import_id"] != iid]
            save_import_history()
            st.rerun()


st.markdown(THEME_CSS, unsafe_allow_html=True)

st.title("Daily Schedule Builder")
st.caption(
    "Upload a spreadsheet (CSV/XLSX) or PDF full of tasks — it extracts each task, date, and category "
    "(Social Media, Advertising, Product Design/Execution, or Other), then lays them out on a weekly calendar."
)

tab_calendar, tab_upload, tab_manual = st.tabs(["Weekly calendar", "Upload files", "Add a task manually"])

with tab_upload:
    uploaded_files = st.file_uploader(
        "Spreadsheets or PDFs",
        type=["csv", "tsv", "xlsx", "xls", "pdf"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        for uf in uploaded_files:
            st.markdown(f"**{uf.name}**")
            key_prefix = f"{uf.name}_{uf.size}"
            try:
                if uf.name.lower().endswith(".pdf"):
                    preview = parse_pdf_lines(uf)
                    if preview.empty:
                        st.warning("No date-bearing lines found in this PDF. Nothing to add.")
                        continue
                    edited = st.data_editor(
                        preview,
                        num_rows="dynamic",
                        use_container_width=True,
                        key=f"{key_prefix}_pdf_edit",
                        column_config={
                            "Category": st.column_config.SelectboxColumn("Category", options=CATEGORIES, required=True),
                        },
                    )
                    if st.button(f"Add tasks from {uf.name}", key=f"{key_prefix}_add_pdf"):
                        import_id = uuid.uuid4().hex
                        added = append_tasks(
                            edited["Task"], edited["Date"], None, edited["Category"], uf.name, import_id=import_id
                        )
                        record_import_history(import_id, uf.name, len(added), len(edited) - len(added))
                        st.success(f"Added {len(added)} tasks from {uf.name}.")
                else:
                    df = read_spreadsheet(uf)
                    if df.empty:
                        st.warning("This file has no rows.")
                        continue
                    cols = list(df.columns)
                    default_task = guess_column(cols, TASK_KEYWORDS) or cols[0]
                    default_date = guess_column(cols, DATE_KEYWORDS) or (cols[1] if len(cols) > 1 else cols[0])
                    default_category = guess_column(cols, CATEGORY_KEYWORDS_COLUMN)

                    c1, c2, c3, c4 = st.columns(4)
                    task_col = c1.selectbox("Task column", cols, index=cols.index(default_task), key=f"{key_prefix}_task_col")
                    date_col = c2.selectbox("Date column", cols, index=cols.index(default_date), key=f"{key_prefix}_date_col")
                    category_options = ["(auto-detect)"] + cols
                    category_col = c3.selectbox(
                        "Category column (optional)",
                        category_options,
                        index=category_options.index(default_category) if default_category else 0,
                        key=f"{key_prefix}_category_col",
                    )
                    notes_options = ["(none)"] + cols
                    notes_col = c4.selectbox("Notes column (optional)", notes_options, key=f"{key_prefix}_notes_col")

                    st.dataframe(df.head(10), use_container_width=True)

                    if st.button(f"Add tasks from {uf.name}", key=f"{key_prefix}_add_sheet"):
                        notes_series = df[notes_col] if notes_col != "(none)" else None
                        category_series = df[category_col] if category_col != "(auto-detect)" else None
                        import_id = uuid.uuid4().hex
                        added = append_tasks(
                            df[task_col], df[date_col], notes_series, category_series, uf.name, import_id=import_id
                        )
                        record_import_history(import_id, uf.name, len(added), len(df) - len(added))
                        st.success(f"Added {len(added)} tasks from {uf.name}.")
            except Exception as e:
                st.error(f"Couldn't parse {uf.name}: {e}")

    st.divider()
    render_import_history()

with tab_manual:
    with st.form("manual_add", clear_on_submit=True):
        mc1, mc2 = st.columns([2, 1])
        m_task = mc1.text_input("Task")
        m_date = mc2.text_input("Date", placeholder="e.g. Aug 10, 2026")
        m_category = st.segmented_control("Category", ["Auto-detect"] + CATEGORIES, default="Auto-detect")
        m_notes = st.text_area("Notes (optional)", height=68)
        if st.form_submit_button("Add task") and m_task.strip():
            if m_category is None:
                m_category = "Auto-detect"
            category = None if m_category == "Auto-detect" else [m_category]
            added = append_tasks([m_task], [m_date], [m_notes], category, "manual")
            if not added.empty:
                st.session_state.recent_manual.insert(0, added.iloc[0].to_dict())
                st.session_state.recent_manual = st.session_state.recent_manual[:5]
            st.success("Task added.")

    if st.session_state.recent_manual:
        st.divider()
        st.markdown("##### Just added")
        for item in list(st.session_state.recent_manual):
            rc1, rc2, rc3 = st.columns([3, 1.2, 0.8])
            rc1.markdown(item["Task"])
            date_val = item.get("Date")
            date_str = pd.Timestamp(date_val).strftime("%b %d") if pd.notna(date_val) else "no date"
            rc2.caption(date_str)
            if rc3.button("Undo", key=f"undo_manual_{item['_id']}"):
                st.session_state.tasks_df = st.session_state.tasks_df[st.session_state.tasks_df["_id"] != item["_id"]]
                save_tasks()
                st.session_state.recent_manual = [
                    r for r in st.session_state.recent_manual if r["_id"] != item["_id"]
                ]
                st.rerun()

with tab_calendar:
    df = st.session_state.tasks_df.copy()

    if df.empty:
        st.info("No tasks yet — upload a file above or add one manually.")
    else:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        unscheduled = df[df["Date"].isna()]
        scheduled = df[df["Date"].notna()].sort_values("Date")

        done_col_config = {
            "Category": st.column_config.SelectboxColumn("Category", options=CATEGORIES, required=True),
            "Done": st.column_config.CheckboxColumn("Done"),
        }

        top_col1, top_col2 = st.columns([3, 1])
        with top_col2:
            csv_buf = io.StringIO()
            export_df = pd.concat([scheduled, unscheduled]).drop(columns=HIDDEN_COLUMNS, errors="ignore")
            export_df.to_csv(csv_buf, index=False)
            st.download_button(
                "Export schedule as CSV",
                data=csv_buf.getvalue(),
                file_name="daily_schedule.csv",
                mime="text/csv",
            )
            if st.button("Clear all tasks"):
                st.session_state.tasks_df = empty_df()
                save_tasks()
                st.rerun()

        if not unscheduled.empty:
            st.subheader(f"Unscheduled ({len(unscheduled)})")
            st.caption("No date could be found for these — edit the Date column to slot them in.")
            edited_unsched = st.data_editor(
                unscheduled.drop(columns=HIDDEN_COLUMNS, errors="ignore"),
                num_rows="dynamic",
                use_container_width=True,
                key="unscheduled_editor",
                column_config=done_col_config,
            )
            st.session_state.tasks_df.update(edited_unsched)
            save_tasks()

        st.divider()
        st.subheader("Calendar")
        render_legend()

        if "view_mode" not in st.session_state:
            st.session_state.view_mode = "7 Days"
        if "view_start" not in st.session_state:
            st.session_state.view_start = pd.Timestamp.today().normalize().date()

        def monday_of(d):
            return d - timedelta(days=d.weekday())

        view_mode = st.segmented_control("Zoom", VIEW_OPTIONS, key="view_mode")
        if view_mode is None:
            view_mode = "7 Days"
            st.session_state.view_mode = view_mode
        span = VIEW_SPANS[view_mode]
        week_aligned = view_mode in WEEK_ALIGNED_MODES

        nav1, nav2, nav3, nav4 = st.columns([1, 1, 1, 5])
        if nav1.button("◀ Prev"):
            st.session_state.view_start -= timedelta(days=span)
        if nav2.button("Today"):
            st.session_state.view_start = pd.Timestamp.today().normalize().date()
        if nav3.button("Next ▶"):
            st.session_state.view_start += timedelta(days=span)

        anchor = monday_of(st.session_state.view_start) if week_aligned else st.session_state.view_start
        view_end = anchor + timedelta(days=span - 1)
        st.caption(f"{anchor.strftime('%B %d')} – {view_end.strftime('%B %d, %Y')}")

        view_mask = (scheduled["Date"].dt.date >= anchor) & (scheduled["Date"].dt.date <= view_end)
        view_df = scheduled[view_mask]
        today_date = pd.Timestamp.today().normalize().date()

        def render_day_cell(col, day):
            day_tasks = view_df[view_df["Date"].dt.date == day].sort_values("Category")
            with col:
                is_today = day == today_date
                fmt = "%A, %B %d" if span == 1 else "%a %-m/%-d"
                header = f"**{day.strftime(fmt)}**" + (" 🔵" if is_today else "")
                st.markdown(header)
                if day_tasks.empty:
                    st.caption("—")
                else:
                    for _, row in day_tasks.iterrows():
                        render_task_capsule(row)

        st.markdown(CALENDAR_CSS, unsafe_allow_html=True)
        with st.container(key="calendar_grid"):
            if span <= 7:
                day_cols = st.columns(span)
                for i, col in enumerate(day_cols):
                    render_day_cell(col, anchor + timedelta(days=i))
            else:
                for week_i in range(span // 7):
                    week_cols = st.columns(7)
                    for day_i, col in enumerate(week_cols):
                        render_day_cell(col, anchor + timedelta(days=week_i * 7 + day_i))

        st.markdown("##### Edit tasks in this view")
        edited_view = st.data_editor(
            view_df.drop(columns=HIDDEN_COLUMNS, errors="ignore"),
            num_rows="dynamic",
            use_container_width=True,
            key=f"view_editor_{view_mode}_{anchor}",
            column_config=done_col_config,
        )
        st.session_state.tasks_df.update(edited_view)
        save_tasks()

        outside_view = scheduled[~view_mask]
        if not outside_view.empty:
            with st.expander(f"All other scheduled tasks, outside this view ({len(outside_view)})"):
                for day, group in outside_view.groupby(outside_view["Date"].dt.date):
                    label = day.strftime("%A, %B %d, %Y")
                    st.markdown(f"**{label}** ({len(group)})")
                    edited_day = st.data_editor(
                        group.drop(columns=HIDDEN_COLUMNS, errors="ignore"),
                        num_rows="dynamic",
                        use_container_width=True,
                        key=f"day_editor_{day}",
                        column_config=done_col_config,
                    )
                    st.session_state.tasks_df.update(edited_day)
                    save_tasks()
