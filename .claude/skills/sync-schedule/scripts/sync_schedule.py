#!/usr/bin/env python3
"""Sync a schedule spreadsheet into the Daily Schedule Builder's task store.

Reconciles by filename: rows previously imported from the same source filename are
matched against the new file's rows by a natural key (a day/index/row/# column if the
sheet has one, else the row's position in the sheet), upserted, and any previously
imported row whose key is no longer present gets deleted. Rows from other files, and
tasks added by hand in the app, are never touched.

Usage:
    python sync_schedule.py <path-to-spreadsheet> [--sheet SHEET_NAME]
"""

import argparse
import hashlib
import os
import sys

import pandas as pd

def _find_repo_root(start):
    path = start
    for _ in range(10):
        if os.path.exists(os.path.join(path, "schedule_core.py")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    raise SystemExit(f"Couldn't find schedule_core.py by walking up from {start}")


sys.path.insert(0, _find_repo_root(os.path.dirname(os.path.abspath(__file__))))

import schedule_core as core

DAY_KEYWORDS = ["day", "index", "row", "#", "no.", "no", "num", "number", "order"]
NOTES_KEYWORDS = core.NOTES_KEYWORDS_COLUMN


def pick_sheet(sheets, explicit_name):
    if explicit_name:
        if explicit_name not in sheets:
            raise SystemExit(
                f"Sheet {explicit_name!r} not found. Available sheets: {', '.join(sheets)}"
            )
        return explicit_name
    for name, df in sheets.items():
        cols = list(df.columns)
        if core.guess_column(cols, core.TASK_KEYWORDS) and core.guess_column(cols, core.DATE_KEYWORDS):
            return name
    raise SystemExit(
        "Couldn't find a sheet with both a task-like column and a date-like column. "
        f"Available sheets: {', '.join(sheets)}. Pass --sheet to pick one explicitly."
    )


def load_sheets(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv"):
        sep = "\t" if ext == ".tsv" else None
        return {os.path.basename(path): pd.read_csv(path, sep=sep, engine="python")}
    return pd.read_excel(path, sheet_name=None)


def build_new_rows(df, filename):
    cols = list(df.columns)
    task_col = core.guess_column(cols, core.TASK_KEYWORDS)
    date_col = core.guess_column(cols, core.DATE_KEYWORDS)
    if not task_col or not date_col:
        raise SystemExit(f"Couldn't detect Task/Date columns in {cols}")
    category_col = core.guess_column(cols, core.CATEGORY_KEYWORDS_COLUMN)
    notes_col = core.guess_column(cols, NOTES_KEYWORDS)
    day_col = core.guess_column(cols, DAY_KEYWORDS)

    key_desc = repr(day_col) if day_col else "row position (no day/index column found)"
    print(f"Detected columns — Task: {task_col!r}, Date: {date_col!r}, "
          f"Category: {category_col!r}, Notes: {notes_col!r}, natural key: {key_desc}")

    rows = []
    for pos, (_, row) in enumerate(df.iterrows()):
        task_text = str(row[task_col]).strip() if pd.notna(row[task_col]) else ""
        if not task_text:
            continue
        natural_key = str(row[day_col]).strip() if day_col and pd.notna(row[day_col]) else str(pos)
        row_id = hashlib.md5(f"{filename}::{natural_key}".encode()).hexdigest()
        category_value = row[category_col] if category_col else None
        rows.append(
            {
                "Task": task_text,
                "Date": core.try_parse_date(row[date_col]),
                "Category": core.normalize_category(category_value, task_text),
                "Notes": str(row[notes_col]).strip() if notes_col and pd.notna(row[notes_col]) else "",
                "Source": filename,
                "_id": row_id,
            }
        )
    return pd.DataFrame(rows, columns=core.COLUMNS)


def describe(row):
    text = str(row["Task"])
    if len(text) > 80:
        text = text[:77] + "..."
    date = row["Date"]
    date_str = date.strftime("%b %d, %Y") if pd.notna(date) else "no date"
    return f"  [{date_str}] {text}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spreadsheet", help="Path to the schedule spreadsheet (xlsx, xls, or csv)")
    parser.add_argument("--sheet", help="Sheet name to use (default: auto-detect)")
    args = parser.parse_args()

    if not os.path.exists(args.spreadsheet):
        raise SystemExit(f"File not found: {args.spreadsheet}")

    filename = os.path.basename(args.spreadsheet)
    sheets = load_sheets(args.spreadsheet)
    sheet_name = pick_sheet(sheets, args.sheet)
    print(f"Using sheet: {sheet_name!r}")

    new_rows = build_new_rows(sheets[sheet_name], filename)
    if new_rows.empty:
        raise SystemExit("No usable rows found (all blank, or no task text) — nothing to sync.")

    existing = core.load_tasks()
    this_source_old = existing[existing["Source"] == filename]
    other_sources = existing[existing["Source"] != filename]

    old_ids = set(this_source_old["_id"])
    new_ids = set(new_rows["_id"])

    added_ids = new_ids - old_ids
    deleted_ids = old_ids - new_ids
    matched_ids = new_ids & old_ids

    def as_text(value):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        return str(value)

    old_by_id = this_source_old.set_index("_id")
    new_by_id = new_rows.set_index("_id")
    updated_ids = set()
    for rid in matched_ids:
        old_row = old_by_id.loc[rid]
        new_row = new_by_id.loc[rid]
        changed = (
            as_text(old_row["Task"]) != as_text(new_row["Task"])
            or pd.Timestamp(old_row["Date"]) != pd.Timestamp(new_row["Date"])
            or as_text(old_row["Category"]) != as_text(new_row["Category"])
            or as_text(old_row["Notes"]) != as_text(new_row["Notes"])
        )
        if changed:
            updated_ids.add(rid)
    unchanged_ids = matched_ids - updated_ids

    final_df = pd.concat([other_sources, new_rows], ignore_index=True)
    core.save_tasks(final_df)

    print()
    print(f"Synced {filename!r} against sheet {sheet_name!r}:")
    print(f"  {len(added_ids)} added, {len(updated_ids)} updated, "
          f"{len(deleted_ids)} deleted, {len(unchanged_ids)} unchanged")

    if added_ids:
        print(f"\nAdded ({len(added_ids)}):")
        for rid in added_ids:
            print(describe(new_by_id.loc[rid]))
    if updated_ids:
        print(f"\nUpdated ({len(updated_ids)}):")
        for rid in updated_ids:
            print(describe(new_by_id.loc[rid]))
    if deleted_ids:
        print(f"\nDeleted ({len(deleted_ids)}):")
        for rid in deleted_ids:
            print(describe(old_by_id.loc[rid]))


if __name__ == "__main__":
    main()
