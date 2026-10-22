"""Fiix CMMS weekly CSV ingestion and cleaning."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from utils.dates import add_week_columns
from utils.names import clean_text, name_key, normalize_person_name
from utils.validation import (
    Issue,
    LoadResult,
    ValidationError,
    missing_columns,
    parse_datetime,
    standardize_columns,
    to_hours,
)

FIIX_COLUMN_MAP = {
    "WO": "wo",
    "FF Group": "ff_group",
    "TL": "tl",
    "Parent Asset": "parent_asset",
    "Asset": "asset",
    "Task Description": "task_description",
    "Task Notes Completion": "task_notes_completion",
    "Completion Notes": "completion_notes",
    "Time Spent": "time_spent_raw",
    "Completed By": "completed_by_raw",
    "Date Created": "date_created_raw",
    "Date Completed": "date_completed_raw",
    "Status": "status",
    "WO Type": "wo_type",
}
FIIX_COLUMN_ALIASES = {"WO#": "WO", "TL#": "TL", "Work Order": "WO"}


def read_fiix_csv(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8", encoding_errors="replace")


def _positional_fallback(df: pd.DataFrame, missing: list[str]) -> tuple[pd.DataFrame, str] | None:
    """Map renamed headers by position when every other standard column is exactly in its standard position."""
    expected = list(FIIX_COLUMN_MAP)
    cols = list(df.columns)
    if len(cols) < len(expected) or any(cols[i] != name for i, name in enumerate(expected) if name not in missing):
        return None
    renamed = {cols[expected.index(m)]: m for m in missing}
    cols[:len(expected)] = expected
    out = df.copy()
    out.columns = cols
    note = ", ".join(f"'{old}' -> '{new}'" for old, new in renamed.items())
    return out, f"Non-standard headers mapped by column position: {note}."


def clean_fiix(raw: pd.DataFrame, offset_hours: float = 1, source: str = "fiix") -> LoadResult:
    warnings: list[str] = []
    df = standardize_columns(raw, list(FIIX_COLUMN_MAP), FIIX_COLUMN_ALIASES)
    missing = missing_columns(df, list(FIIX_COLUMN_MAP))
    if missing:
        fallback = _positional_fallback(df, missing)
        if fallback is None:
            raise ValidationError(f"Missing required Fiix columns: {', '.join(missing)}")
        df, note = fallback
        warnings.append(note)

    out = df[list(FIIX_COLUMN_MAP)].rename(columns=FIIX_COLUMN_MAP).reset_index(drop=True)
    for col in out.columns:
        out[col] = out[col].map(clean_text).astype("object")
    # Rows blank in every Fiix column (e.g. beside a pivot table pasted into the export) are not records.
    out = out[out.notna().any(axis=1)]

    issues: list[Issue] = []
    no_wo = out["wo"].isna()
    for idx in out.index[no_wo]:
        issues.append(Issue(source, "missing_wo", f"row {idx + 2}", "Row has no WO number and cannot be stored."))
    out = out[~no_wo]

    dup = out["wo"].duplicated(keep="first")
    for wo in out.loc[dup, "wo"]:
        issues.append(Issue(source, "duplicate_wo_in_file", str(wo), "WO appears more than once in the file; first row kept."))
    out = out[~dup].copy()

    out["completed_by"] = out["completed_by_raw"].map(normalize_person_name)
    out["completed_by_key"] = out["completed_by_raw"].map(name_key)
    out["time_spent_hours"] = to_hours(out["time_spent_raw"])
    out["date_created"] = parse_datetime(out["date_created_raw"])
    out["date_completed"] = parse_datetime(out["date_completed_raw"])
    out["_week_basis"] = out["date_completed"].fillna(out["date_created"])
    out = add_week_columns(out, "_week_basis", offset_hours).drop(columns="_week_basis")

    if out["completed_by"].isna().any():
        warnings.append(f"{int(out['completed_by'].isna().sum())} rows missing Completed By (excluded from technician metrics).")
    return LoadResult(frame=out, source_rows=len(raw), issues=issues, warnings=warnings)


def load_fiix_file(path: Path, settings: dict) -> LoadResult:
    return clean_fiix(read_fiix_csv(path), float(settings.get("week_start_offset_hours", 1)))
