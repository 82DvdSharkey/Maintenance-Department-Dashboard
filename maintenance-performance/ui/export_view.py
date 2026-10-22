"""Builds the filtered Excel export workbook."""
from __future__ import annotations

import pandas as pd

from kpi.pm_completion import pm_area_summary
from pages.data_quality import data_quality_summary
from ui.components import (
    CALL_DETAIL_COLUMNS,
    PM_AREA_COLUMNS,
    SHIFT_SCORECARD_COLUMNS,
    TECH_SCORECARD_COLUMNS,
)
from utils.dates import week_label
from utils.export import build_excel

FIIX_EXPORT_COLUMNS = {
    "wo": "WO", "ff_group": "FF Group", "tl": "TL", "parent_asset": "Parent Asset", "asset": "Asset",
    "task_description": "Task Description", "completion_notes": "Completion Notes", "time_spent_hours": "Time Spent (h)",
    "completed_by": "Completed By", "technician": "Technician", "shift": "Shift", "date_created": "Date Created",
    "date_completed": "Date Completed", "status": "Status", "wo_type": "WO Type", "classification": "Classification",
    "counts_toward_labor": "Counts Toward Labor", "year": "Year", "calendar_week": "Calendar Week",
    "source_file": "Source File",
}


def _select(df: pd.DataFrame, columns: dict) -> pd.DataFrame:
    return df[[c for c in columns if c in df.columns]].rename(columns=columns)


def build_export(ctx) -> bytes:
    dept = ctx.department_summary().to_frame().T
    dept.insert(0, "Period", ctx.filters.label)
    dept_cols = {k: v for k, v in SHIFT_SCORECARD_COLUMNS.items() if k not in ("shift", "technician_count")}
    dept_cols["pm_completed"] = "Fiix PM WOs Closed"
    by_area = pm_area_summary(ctx.inputs.pm_status, ["area"]).set_index("area")["pm_completion_pct"]
    for area in ctx.pm_areas:
        dept[f"pm_area_{area}"] = by_area.get(area)
        dept_cols[f"pm_area_{area}"] = f"PM Completion % – {area}"
    pm_weekly = pm_area_summary(ctx.inputs.pm_status, ["week_key", "area"])
    pm_weekly["week"] = pm_weekly["week_key"].map(week_label)
    sheets = {
        "Department KPIs": pd.concat([dept[["Period"]], _select(dept, dept_cols)], axis=1),
        "Shift KPIs": _select(ctx.shift_scorecard(), SHIFT_SCORECARD_COLUMNS),
        "Technician KPIs": _select(ctx.technician_scorecard(), TECH_SCORECARD_COLUMNS),
        "PM Open vs Closed": _select(pm_weekly.sort_values(["week_key", "area"]), PM_AREA_COLUMNS),
        "Fiix Detail": _select(ctx.inputs.fiix, FIIX_EXPORT_COLUMNS),
        "Ignition Detail": _select(ctx.inputs.calls, {**CALL_DETAIL_COLUMNS, "technician": "Technician", "shift": "Shift",
                                           "focus_factory": "FocusFactory", "calendar_week": "Calendar Week"}),
        "Data Quality": data_quality_summary(ctx),
    }
    return build_excel(sheets)
