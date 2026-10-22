"""Data-quality checks over the prepared datasets (no records are modified or dropped)."""
from __future__ import annotations

import pandas as pd

from utils.dates import week_label


def fiix_missing_completed_by(fiix: pd.DataFrame) -> pd.DataFrame:
    return fiix[fiix["completed_by_key"].isna()]


def calls_missing_tech(calls: pd.DataFrame) -> pd.DataFrame:
    return calls[calls["tech_key"].isna()]


def unmapped_names(fiix: pd.DataFrame, calls: pd.DataFrame, dayforce: pd.DataFrame | None = None) -> pd.DataFrame:
    frames = []
    sources = [
        ("fiix", fiix, "completed_by", "completed_by_key", "date_completed"),
        ("ignition", calls, "tech_name_norm", "tech_key", "call_time"),
    ]
    if dayforce is not None and not dayforce.empty:
        sources.append(("dayforce", dayforce, "employee_name", "employee_key", "work_date"))
    for source, df, name_col, key_col, ts_col in sources:
        un = df[df[key_col].notna() & df["technician_id"].isna()]
        if un.empty:
            continue
        g = (un.groupby(key_col)
             .agg(name=(name_col, "first"), records=(key_col, "size"), first_seen=(ts_col, "min"),
                  last_seen=(ts_col, "max"))
             .reset_index().rename(columns={key_col: "name_key"}))
        g.insert(0, "source", source)
        frames.append(g)
    if not frames:
        return pd.DataFrame(columns=["source", "name_key", "name", "records", "first_seen", "last_seen"])
    return pd.concat(frames, ignore_index=True).sort_values(["source", "records"], ascending=[True, False])


def counted_dayforce_entries(base) -> pd.DataFrame:
    """Dayforce entries under job titles that count as maintenance (e.g. excludes Tool & Die)."""
    from kpi.dayforce import excluded_job_mask

    entries = base.dayforce_entries
    if entries is None or entries.empty:
        return pd.DataFrame()
    return entries[~excluded_job_mask(entries["job"], list(base.app_settings.get("dayforce_excluded_jobs") or []))]


def ambiguous_names(ambiguous: dict[str, set[str]]) -> pd.DataFrame:
    rows = [{"source": s, "name_key": k} for s, keys in ambiguous.items() for k in sorted(keys)]
    return pd.DataFrame(rows, columns=["source", "name_key"])


def technicians_without_shift(technicians: pd.DataFrame) -> pd.DataFrame:
    return technicians[technicians["active"].astype(bool) & technicians["shift"].isna()]


def missing_weekly_entries(technicians: pd.DataFrame, entries: pd.DataFrame, week_keys: list[int]) -> pd.DataFrame:
    active = technicians[technicians["active"].astype(bool)][["technician_id", "technician_name", "shift"]]
    if active.empty or not week_keys:
        return pd.DataFrame(columns=["technician_name", "shift", "week"])
    grid = active.merge(pd.DataFrame({"week_key": week_keys}), how="cross")
    have = entries[["technician_id", "week_key"]].drop_duplicates().astype("Int64")
    grid = grid.astype({"technician_id": "Int64", "week_key": "Int64"})
    out = grid.merge(have, on=["technician_id", "week_key"], how="left", indicator=True)
    out = out[out["_merge"] == "left_only"]
    out["week"] = out["week_key"].map(week_label)
    return out[["technician_name", "shift", "week"]]


def missing_pm_area_entries(pm_status: pd.DataFrame, areas: list[str], week_keys: list[int]) -> pd.DataFrame:
    if not areas or not week_keys:
        return pd.DataFrame(columns=["area", "week"])
    grid = pd.DataFrame({"area": areas}).merge(pd.DataFrame({"week_key": week_keys}), how="cross")
    have = set(zip(pm_status["area"], pm_status["week_key"].astype("Int64")))
    out = grid[[(a, w) not in have for a, w in zip(grid["area"], grid["week_key"])]].copy()
    out["week"] = out["week_key"].map(week_label)
    return out[["area", "week"]]


def invalid_fiix_dates(fiix: pd.DataFrame) -> pd.DataFrame:
    bad_created = fiix["date_created_raw"].notna() & fiix["date_created"].isna()
    bad_completed = fiix["date_completed"].isna()
    return fiix[bad_created | bad_completed]


def invalid_fiix_time(fiix: pd.DataFrame) -> pd.DataFrame:
    bad = (fiix["time_spent_raw"].notna() & fiix["time_spent_hours"].isna()) | (fiix["time_spent_hours"] < 0)
    return fiix[bad.fillna(False)]


def fiix_time_outliers(fiix: pd.DataFrame, max_hours: float | None) -> pd.DataFrame:
    if not max_hours:
        return fiix.iloc[0:0]
    return fiix[(fiix["time_spent_hours"] > max_hours).fillna(False)]


def invalid_call_durations(calls: pd.DataFrame) -> pd.DataFrame:
    raw = ["raw_call_duration", "raw_ack_duration", "raw_response_duration", "raw_repair_duration"]
    return calls[(calls[raw] < 0).any(axis=1)]


def invalid_call_dates(calls: pd.DataFrame) -> pd.DataFrame:
    return calls[calls["call_time"].isna()]
