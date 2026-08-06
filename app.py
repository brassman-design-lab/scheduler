"""Daily Schedule Builder — upload spreadsheets/PDFs, extract tasks + dates, view sorted by day."""

import io
import os
import re
import uuid
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
from dateutil import parser as dateparser
from sqlalchemy import create_engine, text

st.set_page_config(page_title="Daily Schedule Builder", layout="wide")

COLUMNS = ["Task", "Date", "Category", "Notes", "Source", "_id"]
HIDDEN_COLUMNS = ["Source", "_id"]
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks_data.csv")
DATABASE_URL = os.environ.get("DATABASE_URL")
DB_TABLE = "tasks"

TASK_KEYWORDS = ["task", "title", "name", "item", "activity", "description", "subject"]
DATE_KEYWORDS = ["date", "due", "deadline", "when", "day"]
CATEGORY_KEYWORDS_COLUMN = ["category", "type", "channel", "team", "workstream"]

CATEGORY_META = {
    "Social Media": {"emoji": "📱"},
    "Advertising": {"emoji": "📢"},
    "Product Design/Execution": {"emoji": "🛠️"},
    "Other": {"emoji": "📌"},
}
CATEGORIES = list(CATEGORY_META.keys())

CATEGORY_KEYWORDS = {
    "Social Media": [
        "social", "instagram", " ig ", "facebook", "tiktok", "linkedin", "pinterest",
        "twitter", " x post", "influencer", "content calendar", "caption", "story", "reel", "hashtag",
    ],
    "Advertising": [
        "ad ", "ads", "advertising", "advertisement", "campaign", "ppc", "google ads",
        "meta ads", "sponsor", "media buy", "promo", "boost", "retarget", "creative brief",
    ],
    "Product Design/Execution": [
        "design", "prototype", "wireframe", "figma", "build", "develop", "engineering",
        "ux", "ui", "spec", "feature", "roadmap", "sprint", "mockup", "dev ", "qa ", "code", "implementation",
    ],
}


def guess_category(text):
    if not text:
        return "Other"
    t = f" {str(text).lower()} "
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return cat
    return "Other"


def empty_df():
    return pd.DataFrame(columns=COLUMNS)


def normalize_loaded_df(df):
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    if df["_id"].astype(str).str.strip().eq("").any():
        df["_id"] = [v if str(v).strip() else uuid.uuid4().hex for v in df["_id"]]
    return df[COLUMNS]


@st.cache_resource
def get_engine():
    if not DATABASE_URL:
        return None
    return create_engine(DATABASE_URL, pool_pre_ping=True)


def load_tasks():
    engine = get_engine()
    if engine is None:
        if not os.path.exists(DATA_FILE):
            return empty_df()
        try:
            df = pd.read_csv(DATA_FILE)
        except (pd.errors.EmptyDataError, OSError):
            return empty_df()
        return normalize_loaded_df(df)

    with engine.begin() as conn:
        conn.execute(
            text(
                f"""CREATE TABLE IF NOT EXISTS {DB_TABLE} (
                    "_id" TEXT PRIMARY KEY,
                    "Task" TEXT,
                    "Date" TIMESTAMP,
                    "Category" TEXT,
                    "Notes" TEXT,
                    "Source" TEXT
                )"""
            )
        )
        df = pd.read_sql_query(text(f'SELECT * FROM {DB_TABLE}'), conn)
    if df.empty:
        return empty_df()
    return normalize_loaded_df(df)


def save_tasks():
    engine = get_engine()
    if engine is None:
        st.session_state.tasks_df.to_csv(DATA_FILE, index=False)
        return
    df = st.session_state.tasks_df.copy()
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {DB_TABLE}"))
        if not df.empty:
            df.to_sql(DB_TABLE, conn, if_exists="append", index=False)


if "tasks_df" not in st.session_state:
    st.session_state.tasks_df = load_tasks()

if "expanded_task" not in st.session_state:
    st.session_state.expanded_task = None


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


def render_task_capsule(row):
    task_id = row["_id"]
    is_open = st.session_state.expanded_task == task_id
    icon = "▾" if is_open else "▸"
    label = f"{icon} {row['Task']}"
    if st.button(label, key=f"cap_{task_id}", use_container_width=True):
        st.session_state.expanded_task = None if is_open else task_id
        st.rerun()
    if is_open:
        with st.container(border=True):
            st.markdown(f"**{row['Task']}**")
            emoji = CATEGORY_META[row["Category"]]["emoji"]
            date_label = row["Date"].strftime("%b %d, %Y") if pd.notna(row["Date"]) else "Unscheduled"
            st.caption(f"{emoji} {row['Category']} · {date_label}")
            notes = str(row.get("Notes", "") or "").strip()
            if notes:
                st.write(notes)


def guess_column(columns, keywords):
    lower = {c: str(c).strip().lower() for c in columns}
    for c, l in lower.items():
        if any(k == l for k in keywords):
            return c
    for c, l in lower.items():
        if any(k in l for k in keywords):
            return c
    return None


def try_parse_date(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return pd.Timestamp(value).normalize()
    s = str(value).strip()
    if not s:
        return None
    try:
        return pd.Timestamp(dateparser.parse(s, fuzzy=True)).normalize()
    except (ValueError, OverflowError):
        return None


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


def normalize_category(value, task_text):
    if value is not None and not (isinstance(value, float) and pd.isna(value)):
        s = str(value).strip()
        for cat in CATEGORIES:
            if s.lower() == cat.lower():
                return cat
    return guess_category(task_text)


def append_tasks(task_series, date_series, notes_series, category_series, source):
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
            "Source": [source] * n,
            "_id": [uuid.uuid4().hex for _ in range(n)],
        }
    )
    new_rows = new_rows[new_rows["Task"].astype(str).str.strip() != ""]
    st.session_state.tasks_df = pd.concat([st.session_state.tasks_df, new_rows], ignore_index=True)
    save_tasks()


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
                        append_tasks(edited["Task"], edited["Date"], None, edited["Category"], uf.name)
                        st.success(f"Added {len(edited)} tasks from {uf.name}.")
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
                        append_tasks(df[task_col], df[date_col], notes_series, category_series, uf.name)
                        st.success(f"Added {len(df)} tasks from {uf.name}.")
            except Exception as e:
                st.error(f"Couldn't parse {uf.name}: {e}")

with tab_manual:
    with st.form("manual_add", clear_on_submit=True):
        mc1, mc2, mc3, mc4 = st.columns([2, 1, 1.5, 2])
        m_task = mc1.text_input("Task")
        m_date = mc2.text_input("Date", placeholder="e.g. Aug 10, 2026")
        m_category = mc3.selectbox("Category", ["Auto-detect"] + CATEGORIES)
        m_notes = mc4.text_input("Notes")
        if st.form_submit_button("Add task") and m_task.strip():
            category = None if m_category == "Auto-detect" else [m_category]
            append_tasks([m_task], [m_date], [m_notes], category, "manual")
            st.success("Task added.")

with tab_calendar:
    df = st.session_state.tasks_df.copy()

    if df.empty:
        st.info("No tasks yet — upload a file above or add one manually.")
    else:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        unscheduled = df[df["Date"].isna()]
        scheduled = df[df["Date"].notna()].sort_values("Date")

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
                column_config={
                    "Category": st.column_config.SelectboxColumn("Category", options=CATEGORIES, required=True),
                },
            )
            st.session_state.tasks_df.update(edited_unsched)
            save_tasks()

        st.divider()
        st.subheader("Weekly calendar")

        if "week_start" not in st.session_state:
            today = pd.Timestamp.today().normalize().date()
            st.session_state.week_start = today - timedelta(days=today.weekday())

        nav1, nav2, nav3, nav4 = st.columns([1, 1, 1, 5])
        if nav1.button("◀ Prev week"):
            st.session_state.week_start -= timedelta(days=7)
        if nav2.button("Today"):
            today = pd.Timestamp.today().normalize().date()
            st.session_state.week_start = today - timedelta(days=today.weekday())
        if nav3.button("Next week ▶"):
            st.session_state.week_start += timedelta(days=7)

        week_start = st.session_state.week_start
        week_end = week_start + timedelta(days=6)
        st.caption(f"{week_start.strftime('%B %d')} – {week_end.strftime('%B %d, %Y')}")

        week_mask = (scheduled["Date"].dt.date >= week_start) & (scheduled["Date"].dt.date <= week_end)
        week_df = scheduled[week_mask]

        st.markdown(CALENDAR_CSS, unsafe_allow_html=True)
        with st.container(key="calendar_grid"):
            day_cols = st.columns(7)
            for i, col in enumerate(day_cols):
                day = week_start + timedelta(days=i)
                day_tasks = week_df[week_df["Date"].dt.date == day]
                with col:
                    is_today = day == pd.Timestamp.today().normalize().date()
                    header = f"**{day.strftime('%a %-m/%-d')}**" if not is_today else f"**{day.strftime('%a %-m/%-d')} 🔵**"
                    st.markdown(header)
                    if day_tasks.empty:
                        st.caption("—")
                    else:
                        for cat in CATEGORIES:
                            cat_tasks = day_tasks[day_tasks["Category"] == cat]
                            if cat_tasks.empty:
                                continue
                            emoji = CATEGORY_META[cat]["emoji"]
                            st.caption(f"{emoji} {cat}")
                            for _, row in cat_tasks.iterrows():
                                render_task_capsule(row)

        st.markdown("##### Edit this week's tasks")
        edited_week = st.data_editor(
            week_df.drop(columns=HIDDEN_COLUMNS, errors="ignore"),
            num_rows="dynamic",
            use_container_width=True,
            key=f"week_editor_{week_start}",
            column_config={
                "Category": st.column_config.SelectboxColumn("Category", options=CATEGORIES, required=True),
            },
        )
        st.session_state.tasks_df.update(edited_week)
        save_tasks()

        outside_week = scheduled[~week_mask]
        if not outside_week.empty:
            with st.expander(f"All other scheduled tasks, outside this week ({len(outside_week)})"):
                for day, group in outside_week.groupby(outside_week["Date"].dt.date):
                    label = day.strftime("%A, %B %d, %Y")
                    st.markdown(f"**{label}** ({len(group)})")
                    edited_day = st.data_editor(
                        group.drop(columns=HIDDEN_COLUMNS, errors="ignore"),
                        num_rows="dynamic",
                        use_container_width=True,
                        key=f"day_editor_{day}",
                        column_config={
                            "Category": st.column_config.SelectboxColumn("Category", options=CATEGORIES, required=True),
                        },
                    )
                    st.session_state.tasks_df.update(edited_day)
                    save_tasks()
