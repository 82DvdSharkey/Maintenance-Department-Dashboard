"""Data Quality dashboard."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from database import repository as repo
from kpi import data_quality as dq
from ui.components import CALL_DETAIL_COLUMNS, WO_COLUMNS, no_data_message, show_table

DUPLICATE_TYPES = {"fiix": ["duplicate_wo_in_file", "duplicate_already_imported"],
                   "ignition": ["duplicate_id_in_file", "duplicate_already_imported"]}


def _scope(ctx, all_data: bool):
    if all_data:
        return ctx.base.fiix, ctx.base.calls, ctx.base.week_keys
    inputs = ctx.inputs
    fiix = inputs.fiix_all if inputs.fiix_all is not None else inputs.fiix
    calls = inputs.calls_all if inputs.calls_all is not None else inputs.calls
    return fiix, calls, ctx.filters.week_keys


def _checks(ctx, all_data: bool = False) -> dict[str, pd.DataFrame]:
    fiix, calls, weeks = _scope(ctx, all_data)
    issues = repo.load_dq_issues(ctx.engine)
    history = repo.load_import_history(ctx.engine)
    unmapped = dq.unmapped_names(fiix, calls, dq.counted_dayforce_entries(ctx.base))
    techs = ctx.base.technicians
    return {
        "Fiix rows missing Completed By": dq.fiix_missing_completed_by(fiix),
        "Ignition maintenance calls missing TechName": dq.calls_missing_tech(calls),
        "Fiix names not mapped": unmapped[unmapped["source"] == "fiix"],
        "Ignition names not mapped": unmapped[unmapped["source"] == "ignition"],
        "Dayforce employees not mapped": unmapped[unmapped["source"] == "dayforce"],
        "Ambiguous names (claimed by >1 technician)": dq.ambiguous_names(ctx.base.ambiguous),
        "Active technicians without a shift": dq.technicians_without_shift(techs),
        "Technicians missing Dayforce hours": dq.missing_weekly_entries(techs, ctx.base.dayforce, weeks),
        "Missing PM Open vs Closed entries": dq.missing_pm_area_entries(
            ctx.base.pm_status, ctx.base.app_settings.get("pm_areas", []), weeks),
        "Duplicate Fiix WOs": issues[(issues["source"] == "fiix") & issues["issue_type"].isin(DUPLICATE_TYPES["fiix"])],
        "Duplicate Ignition IDs": issues[(issues["source"] == "ignition")
                                         & issues["issue_type"].isin(DUPLICATE_TYPES["ignition"])],
        "Invalid Fiix dates": dq.invalid_fiix_dates(fiix),
        "Invalid Fiix Time Spent": dq.invalid_fiix_time(fiix),
        "Fiix Time Spent above per-WO limit (excluded from labor)": dq.fiix_time_outliers(
            fiix, float(ctx.base.app_settings.get("max_time_spent_hours_per_wo") or 0)),
        "Invalid Ignition dates": dq.invalid_call_dates(calls),
        "Invalid duration values": pd.concat([
            dq.invalid_call_durations(calls),
            issues[issues["issue_type"] == "invalid_duration"],
        ], ignore_index=True),
        "Rows without a storable key": issues[issues["issue_type"].isin(["missing_wo", "missing_id"])],
        "Files that failed import": history[history["status"] == "failed"],
        "Files imported with warnings": history[(history["status"] == "success") & history["warnings"].notna()],
    }


def data_quality_summary(ctx) -> pd.DataFrame:
    checks = _checks(ctx)
    return pd.DataFrame({"Check": list(checks), "Count": [len(v) for v in checks.values()],
                         "Scope": ctx.filters.label})


DETAIL_COLUMNS = {
    "Fiix rows missing Completed By": {**WO_COLUMNS, "source_file": "Source File"},
    "Ignition maintenance calls missing TechName": {**CALL_DETAIL_COLUMNS, "source_file": "Source File"},
    "Fiix names not mapped": {"name": "Name", "records": "Work Orders", "first_seen": "First Seen", "last_seen": "Last Seen"},
    "Ignition names not mapped": {"name": "Name", "records": "Calls", "first_seen": "First Seen", "last_seen": "Last Seen"},
    "Dayforce employees not mapped": {"name": "Name", "records": "Time Entries", "first_seen": "First Date",
                                      "last_seen": "Last Date"},
    "Active technicians without a shift": {"technician_name": "Technician", "fiix_name": "Fiix Name",
                                           "ignition_name": "Ignition Name"},
    "Invalid Fiix dates": {"wo": "WO", "date_created_raw": "Date Created (raw)",
                           "date_completed_raw": "Date Completed (raw)", "source_file": "Source File"},
    "Invalid Fiix Time Spent": {"wo": "WO", "time_spent_raw": "Time Spent (raw)", "completed_by": "Completed By",
                                "source_file": "Source File"},
    "Fiix Time Spent above per-WO limit (excluded from labor)": {
        "wo": "WO", "time_spent_hours": "Time Spent (h)", "completed_by": "Completed By", "wo_type": "WO Type",
        "date_created": "Date Created", "date_completed": "Date Completed", "source_file": "Source File"},
    "Files that failed import": {"source": "Source", "filename": "File", "full_path": "Path",
                                 "import_datetime": "Attempted", "warnings": "Error"},
    "Files imported with warnings": {"source": "Source", "filename": "File", "year": "Year",
                                     "calendar_week": "Data Week", "filename_week": "Filename Week",
                                     "warnings": "Warnings"},
}


def render(ctx) -> None:
    st.title("Data Quality")
    if not ctx.base.has_data:
        no_data_message()
        return
    all_data = st.toggle("Check all imported history (otherwise selected period)", key="dq_all")
    st.caption("Records are never silently corrected or dropped. Map names in Administration → Name Mapping.")
    checks = _checks(ctx, all_data)

    names = list(checks)
    for i in range(0, len(names), 4):
        cols = st.columns(4)
        for col, name in zip(cols, names[i:i + 4]):
            n = len(checks[name])
            with col.container(border=True):
                st.metric(name, f"{n:,}")
                st.badge("OK" if n == 0 else "Review", icon=":material/check:" if n == 0 else ":material/flag:",
                         color="green" if n == 0 else "orange")

    st.divider()
    pick = st.selectbox("Show detail for", [n for n in names if len(checks[n])] or names, key="dq_pick")
    show_table(checks[pick].head(2000), DETAIL_COLUMNS.get(pick))

    with st.expander("Ignition records by CallType (all imported calls)"):
        st.caption("Only CallType = Maintenance is used for technician KPIs.")
        show_table(repo.load_call_type_counts(ctx.engine))
