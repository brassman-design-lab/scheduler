"""Shared data logic for the Daily Schedule Builder — column detection, category guessing,
date parsing, and persistence (Postgres via DATABASE_URL, or a local CSV fallback).

Deliberately has no Streamlit dependency so it can be imported by non-UI tools (e.g. the
sync-schedule skill) without pulling in or executing any app UI code.
"""

import os
import uuid
from datetime import datetime
from functools import lru_cache

import pandas as pd
from dateutil import parser as dateparser
from sqlalchemy import create_engine, text

COLUMNS = ["Task", "Date", "Category", "Notes", "Done", "Source", "_id", "_import_id"]
HIDDEN_COLUMNS = ["Source", "_id", "_import_id"]
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(DATA_DIR, "tasks_data.csv")
IMPORT_HISTORY_FILE = os.path.join(DATA_DIR, "import_history.csv")
DATABASE_URL = os.environ.get("DATABASE_URL")
DB_TABLE = "tasks"
IMPORT_HISTORY_TABLE = "import_history"
IMPORT_HISTORY_COLUMNS = ["import_id", "filename", "imported_at", "count_added", "count_skipped"]

TASK_KEYWORDS = ["task", "title", "name", "item", "activity", "description", "subject"]
# "day" is deliberately excluded here — a column literally named "Day" is far more often
# an ordinal (Day 1, Day 2, ...) than an actual date, and would otherwise shadow "Date".
DATE_KEYWORDS = ["date", "due", "deadline", "when"]
CATEGORY_KEYWORDS_COLUMN = ["category", "type", "channel", "team", "workstream"]
NOTES_KEYWORDS_COLUMN = ["notes", "note", "comment", "remarks", "details"]

# Colors match the "Organic" design system (sage / terracotta / deep sage / sand).
CATEGORY_META = {
    "Social Media": {"color": "#8fa073"},
    "Advertising": {"color": "#d67f48"},
    "Product Design/Execution": {"color": "#56633f"},
    "Other": {"color": "#c0b6a5"},
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


def normalize_category(value, task_text):
    if value is not None and not (isinstance(value, float) and pd.isna(value)):
        s = str(value).strip()
        for cat in CATEGORIES:
            if s.lower() == cat.lower():
                return cat
    return guess_category(task_text)


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
    df["Done"] = df["Done"].map(lambda v: bool(v) and str(v).strip().lower() not in ("", "false", "0", "nan"))
    df["_import_id"] = df["_import_id"].fillna("").astype(str)
    return df[COLUMNS]


@lru_cache(maxsize=1)
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
        # Migrate tables created before Done / _import_id existed.
        conn.execute(text(f'ALTER TABLE {DB_TABLE} ADD COLUMN IF NOT EXISTS "Done" BOOLEAN DEFAULT FALSE'))
        conn.execute(text(f'ALTER TABLE {DB_TABLE} ADD COLUMN IF NOT EXISTS "_import_id" TEXT'))
        df = pd.read_sql_query(text(f"SELECT * FROM {DB_TABLE}"), conn)
    if df.empty:
        return empty_df()
    return normalize_loaded_df(df)


def save_tasks(df):
    engine = get_engine()
    if engine is None:
        df.to_csv(DATA_FILE, index=False)
        return
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {DB_TABLE}"))
        if not df.empty:
            df.to_sql(DB_TABLE, conn, if_exists="append", index=False)


def empty_import_history():
    return pd.DataFrame(columns=IMPORT_HISTORY_COLUMNS)


def load_import_history():
    engine = get_engine()
    if engine is None:
        if not os.path.exists(IMPORT_HISTORY_FILE):
            return empty_import_history()
        try:
            return pd.read_csv(IMPORT_HISTORY_FILE)
        except (pd.errors.EmptyDataError, OSError):
            return empty_import_history()

    with engine.begin() as conn:
        conn.execute(
            text(
                f"""CREATE TABLE IF NOT EXISTS {IMPORT_HISTORY_TABLE} (
                    "import_id" TEXT PRIMARY KEY,
                    "filename" TEXT,
                    "imported_at" TIMESTAMP,
                    "count_added" INTEGER,
                    "count_skipped" INTEGER
                )"""
            )
        )
        df = pd.read_sql_query(text(f"SELECT * FROM {IMPORT_HISTORY_TABLE}"), conn)
    return df if not df.empty else empty_import_history()


def save_import_history(df):
    engine = get_engine()
    if engine is None:
        df.to_csv(IMPORT_HISTORY_FILE, index=False)
        return
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {IMPORT_HISTORY_TABLE}"))
        if not df.empty:
            df.to_sql(IMPORT_HISTORY_TABLE, conn, if_exists="append", index=False)
