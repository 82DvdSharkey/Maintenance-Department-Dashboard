"""Dayforce hours worked: technician resolution and weekly aggregation of imported time entries."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from utils.names import build_alias_index, clean_text, flip_name, name_key

WORKED_CATEGORIES = {"REG", "OT"}
PTO_CATEGORIES = {"Sick", "Time Off"}
IMPORT_SOURCE = "Dayforce import"
MANUAL_SOURCE = "Manual entry"


def resolve_dayforce_technicians(entries: pd.DataFrame, technicians: pd.DataFrame,
                                 aliases: pd.DataFrame) -> pd.Series:
    """Employee ID first, then exact Dayforce name/alias, then 'Last, First' flipped to 'First Last'."""
    id_map = {clean_text(v): int(t) for t, v in zip(technicians["technician_id"], technicians["dayforce_id"])
              if clean_text(v)}
    index, _ambiguous = build_alias_index(technicians, aliases, "dayforce")
    by_id = entries["employee_id"].map(lambda v: id_map.get(clean_text(v)))
    by_name = entries["employee_name"].map(name_key).map(index)
    by_flip = entries["employee_name"].map(flip_name).map(name_key).map(index)
    return by_id.fillna(by_name).fillna(by_flip).astype("Int64")


def excluded_job_mask(job: pd.Series, excluded_jobs: list[str]) -> pd.Series:
    text = job.fillna("").astype(str).str.casefold()
    mask = pd.Series(False, index=job.index)
    for pattern in excluded_jobs or []:
        mask |= text.str.contains(pattern.casefold(), regex=False)
    return mask


def covered_weeks(entries: pd.DataFrame) -> set[int]:
    """Production weeks whose Monday-Saturday are all inside imported report date ranges.

    Sunday edge shifts are added when the neighbouring report arrives; weeks with only a Sunday
    fragment are not used, so partial hours never inflate Labor Efficiency.
    """
    if entries.empty or "report_start" not in entries:
        return set()
    days: set[date] = set()
    for start, end in entries[["report_start", "report_end"]].dropna().drop_duplicates().itertuples(index=False):
        d, last = pd.Timestamp(start).date(), pd.Timestamp(end).date()
        while d <= last:
            days.add(d)
            d += timedelta(days=1)
    weeks = set()
    for wk in entries["week_key"].dropna().astype(int).unique():
        monday = date.fromisocalendar(wk // 100, wk % 100, 1)
        if all(monday + timedelta(days=i) in days for i in range(6)):
            weeks.add(int(wk))
    return weeks


def imported_weekly_hours(entries: pd.DataFrame, excluded_jobs: list[str]) -> pd.DataFrame:
    """Weekly REG/OT hours worked per technician. Premium, OT memo and PTO lines are not hours worked."""
    cols = ["technician_id", "week_key", "regular_hours", "overtime_hours", "pto_hours", "total_hours_worked"]
    data = entries[entries["technician_id"].notna() & entries["week_key"].isin(covered_weeks(entries))]
    if data.empty:
        return pd.DataFrame(columns=cols)
    worked = data[data["pay_category"].isin(WORKED_CATEGORIES) & ~excluded_job_mask(data["job"], excluded_jobs)]
    keys = ["technician_id", "week_key"]
    reg = worked[worked["pay_category"] == "REG"].groupby(keys)["normal_hours"].sum().rename("regular_hours")
    ot = worked[worked["pay_category"] == "OT"].groupby(keys)["normal_hours"].sum().rename("overtime_hours")
    pto = data[data["pay_category"].isin(PTO_CATEGORIES)].groupby(keys)["other_hours"].sum().rename("pto_hours")
    out = pd.concat([reg, ot, pto], axis=1).fillna(0.0).reset_index()
    out["total_hours_worked"] = out["regular_hours"] + out["overtime_hours"]
    return out[cols]


def combine_hours(manual: pd.DataFrame, imported: pd.DataFrame) -> pd.DataFrame:
    """Manual entries override imported Dayforce hours for the same technician and week."""
    manual = manual.assign(source=MANUAL_SOURCE)
    if imported.empty:
        return manual
    have = set(zip(manual["technician_id"].astype("Int64"), manual["week_key"].astype("Int64")))
    keep = [(t, w) not in have for t, w in zip(imported["technician_id"], imported["week_key"])]
    extra = imported[keep].assign(
        year=lambda d: d["week_key"] // 100, calendar_week=lambda d: d["week_key"] % 100,
        other_paid_hours=0.0, excluded_hours=0.0, total_overridden=False, source=IMPORT_SOURCE,
        comments=lambda d: [f"PTO {p:g} h (not counted as worked)" if p else None for p in d["pto_hours"]],
    )
    out = pd.concat([manual, extra.drop(columns="pto_hours")], ignore_index=True)
    return out.astype({"technician_id": "Int64", "week_key": "Int64"})
