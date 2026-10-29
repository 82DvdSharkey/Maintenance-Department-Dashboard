"""UI-independent weekly report data; KPI calculations remain in kpi/."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from zoneinfo import ZoneInfo

import pandas as pd

from kpi.dataset import BaseData, FilterState, apply_filters, technician_weekly_trend
from kpi.definitions import KPI_DEFS, TECHNICIAN_KPIS
from kpi.ignition_metrics import summarize_calls
from kpi.labor_efficiency import labor_breakdown
from kpi.scorecard import technician_scorecard
from kpi.targets import evaluate_status
from utils.dates import production_week, shift_week, split_week_key, week_bounds, week_key, week_keys_ending

LOCAL_ZONE = ZoneInfo("America/Detroit")
PM_NOTE = ("PM Completion Share = own closed PMs / total closed PMs for the same shift AND crew, including self. "
           "Peers are active, non-lead maintenance technicians, including those with zero PMs. "
           "This is not an assignment-completion rate. PM Assigned and PM Remaining are unavailable.")


@dataclass(frozen=True)
class WeeklyTechnicianReport:
    technician_id: int
    technician_name: str
    shift: str
    crew: str
    week_key: int
    period_start: datetime
    period_end: datetime
    generated: datetime
    metrics: pd.DataFrame
    workload: pd.DataFrame
    labor: pd.DataFrame
    first_time_fix: pd.DataFrame
    repeats: pd.DataFrame
    areas: pd.DataFrame
    trend: pd.DataFrame
    pm_peer_share: float
    targets: dict
    notices: tuple[str, ...]

    @property
    def filename(self) -> str:
        name = re.sub(r"[^\w-]+", "_", self.technician_name, flags=re.UNICODE).strip("_")[:100] or "Technician"
        year, week = split_week_key(self.week_key)
        return f"Technician_Performance_{name}_CW{week:02d}_{year}.pdf"


def most_recent_completed_week(offset_hours: float = 1, now: datetime | None = None) -> int:
    local = now.astimezone(LOCAL_ZONE) if now is not None else datetime.now(LOCAL_ZONE)
    return shift_week(week_key(*production_week(local.replace(tzinfo=None), offset_hours)), -1)


def build_weekly_technician_report(base: BaseData, technician_id: int, reporting_week: int,
                                  targets: dict, *, offset_hours: float = 1,
                                  allow_partial: bool = False,
                                  now: datetime | None = None) -> WeeklyTechnicianReport:
    generated = now.astimezone(LOCAL_ZONE) if now is not None else datetime.now(LOCAL_ZONE)
    start, end = week_bounds(*split_week_key(reporting_week), offset_hours)
    partial = end > generated.replace(tzinfo=None)
    if partial and not allow_partial:
        raise ValueError("The reporting week is not complete. Explicitly allow a partial-week preview to continue.")
    master = base.technicians[base.technicians["technician_id"] == technician_id]
    if len(master) != 1:
        raise ValueError("Select an existing, unique technician before generating a report.")
    technician = master.iloc[0]
    # Reports deliberately ignore sidebar filters and always use one person's full production week.
    state = FilterState(week_keys=[reporting_week], technician_ids=[technician_id])
    inputs = apply_filters(base, state)
    keys = week_keys_ending(reporting_week, 15)
    trend = technician_weekly_trend(base, state, keys, technician_id,
                                    list(base.app_settings.get("pm_areas") or []))
    trend = trend.iloc[3:].reset_index(drop=True)
    pm_reason = trend.iloc[-1]["pm_peer_reason"]
    call_reason = trend.iloc[-1]["call_peer_reason"]
    person_columns = [KPI_DEFS[key].column for key in TECHNICIAN_KPIS]
    trend = trend[["week_key", "week_label", *person_columns,
                   *[f"{column}_r4" for column in person_columns]]].copy()
    current, previous = trend.iloc[-1], trend.iloc[-2]
    metrics = []
    for key in TECHNICIAN_KPIS:
        definition = KPI_DEFS[key]
        target = targets.get(key, {})
        value = current.get(definition.column)
        metrics.append({
            "KPI": definition.label, "Current Week": value,
            "Previous Week": previous.get(definition.column),
            "4-Week Average": current.get(f"{definition.column}_r4"),
            "Target": target.get("target"), "Unit": definition.unit,
            "Status": evaluate_status(value, target.get("target"), definition.direction, target.get("watch_band")),
        })
    scorecard = technician_scorecard(inputs, base.technicians)
    summary = scorecard.iloc[0] if not scorecard.empty else pd.Series(dtype="float64")
    workload = pd.DataFrame([
        ("Dayforce Hours Worked", summary.get("worked_hours"), "h"),
        ("Fiix Logged Hours", summary.get("logged_hours"), "h"),
        ("Total Fiix Work Orders Completed", len(inputs.fiix[inputs.fiix["status"].str.casefold().eq("closed")]), "count"),
        ("Reactive Work Orders (FTF qualifying)", summary.get("reactive_wos"), "count"),
        ("PM Work Orders Completed", summary.get("pm_completed"), "count"),
        ("Ignition Maintenance Calls", summary.get("maintenance_calls"), "count"),
    ], columns=["Measure", "Value", "Unit"])
    ftf = pd.DataFrame([
        ("Reactive WOs", summary.get("reactive_wos")),
        ("Successful First-Time Fixes", summary.get("ftf_success")),
        ("Repeat Failures", summary.get("repeat_failures")),
        ("First-Time Fix %", current.get("ftf_pct")),
    ], columns=["Measure", "Value"])
    # Evaluation already contains the repeat link. Never join another technician's detail into the report.
    repeats = inputs.ftf.loc[inputs.ftf["repeat_failure"],
                             ["wo", "tl", "asset", "repeat_wo", "days_to_repeat"]].copy()
    repeats = repeats.sort_values(["days_to_repeat", "wo"])
    areas = summarize_calls(inputs.calls.assign(ign_area_name=inputs.calls["ign_area_name"].fillna("(Not assigned)")),
                            ["ign_area_name"]).sort_values("maintenance_calls", ascending=False)
    notices = [PM_NOTE,
               f"First-Time Fix uses the dashboard's existing {base.ftf_config.asset_rule.replace('_', ' ')} "
               f"matching and {base.ftf_config.window_days}-day repeat window. Recent outcomes may change "
               "as repeat work orders arrive. This is not reviewer-classified incident FTF."]
    if pm_reason:
        notices.append(pm_reason)
    notices.append("Maintenance call share: own calls / shift-and-crew total (including self), "
                   "using active, non-lead maintenance peers and current designations.")
    if call_reason:
        notices.append(call_reason)
    if partial:
        notices.append("PARTIAL WEEK PREVIEW: this reporting week has not finished.")
    for label, frame in (("Fiix", inputs.fiix), ("Ignition", inputs.calls), ("Dayforce", inputs.dayforce)):
        if frame.empty:
            notices.append(f"No {label} data is available for this technician in the selected week.")
    return WeeklyTechnicianReport(
        technician_id=int(technician_id), technician_name=str(technician["technician_name"]),
        shift=str(technician["shift"]) if pd.notna(technician["shift"]) else "Not assigned",
        crew=str(technician["crew"]) if pd.notna(technician["crew"]) else "Not assigned",
        week_key=int(reporting_week), period_start=start, period_end=end, generated=generated,
        metrics=pd.DataFrame(metrics), workload=workload,
        labor=labor_breakdown(inputs.labor_rows, "classification"),
        first_time_fix=ftf, repeats=repeats, areas=areas, trend=trend,
        pm_peer_share=current["pm_peer_share_pct"],
        targets={key: dict(value) for key, value in targets.items()}, notices=tuple(notices))
