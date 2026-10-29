"""Calendar-quarter and annual team-lead reviews for one shift and crew area."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

import pandas as pd

from kpi.dataset import (
    BaseData, FilterState, apply_filters, crew_area_technician_ids, technician_call_shares,
    technician_pm_shares,
)
from kpi.definitions import KPI_DEFS, PEOPLE_KPIS, TECHNICIAN_KPIS
from kpi.scorecard import shift_scorecard, technician_scorecard
from reporting.review_common import (
    DAYFORCE_BOUNDARY_NOTE, calendar_review_ranges, format_review_metrics, review_metrics,
)
from ui.pdf_reporting import ReportSection, build_dashboard_pdf
from utils.dates import week_keys_for_dates


@dataclass(frozen=True)
class ShiftReview:
    shift: str
    area: str
    year: int
    as_of: date
    metrics: pd.DataFrame
    individuals: dict[int, pd.DataFrame]
    roster: pd.DataFrame
    periods: pd.DataFrame
    notices: tuple[str, ...]

    @property
    def filename(self) -> str:
        scope = re.sub(r"[^\w-]+", "_", f"{self.shift}_{self.area}").strip("_")
        return f"Shift_Review_{scope}_{self.year}_as_of_{self.as_of.isoformat()}.pdf"


def build_shift_review(base: BaseData, shift: str, area: str, year: int, targets: dict, *,
                       as_of: date | None = None, offset_hours: float = 1) -> ShiftReview:
    as_of = as_of or datetime.now(ZoneInfo("America/Detroit")).date()
    ranges = calendar_review_ranges(year)
    if shift not in base.shifts.loc[base.shifts["active"].astype(bool), "name"].tolist():
        raise ValueError("Select an active reporting shift.")
    areas = base.app_settings.get("crew_areas") or {}
    if area != "All areas" and area not in areas:
        raise ValueError("Select a configured crew area.")
    if area in (base.app_settings.get("reserved_crew_areas") or []):
        raise ValueError("This crew area is reserved; no review is available.")
    techs = base.technicians
    area_ids = None if area == "All areas" else crew_area_technician_ids(techs, areas[area])
    area_techs = techs if area_ids is None else techs[techs["technician_id"].isin(area_ids)]
    roster = area_techs[area_techs["shift"].eq(shift)].copy()
    if "in_department" in roster:
        roster = roster[roster["in_department"].fillna(False).astype(bool)]
    if roster.empty:
        raise ValueError("No maintenance technicians are assigned to the selected shift and crew area.")
    values = {}
    individual_values = {int(t): {} for t in roster["technician_id"]}
    activity_ids = set()
    periods = []
    notices = [
        "Confidential team-lead performance review for the selected shift and crew area only. "
        "Sidebar date, technician, shift, FF Group, IgnAreaName and WO Type filters do not restrict this review.",
        DAYFORCE_BOUNDARY_NOTE,
        "Team KPIs use the existing shift calculation services: labor efficiency excludes leads and "
        "out-of-scope technicians; call durations are call-weighted, not averages of technician averages. "
        "Team PM performance is PMs Closed, not the department's area-level PM Completion percentage. "
        "Individual KPI values do not necessarily sum or average to team KPI values.",
        KPI_DEFS["first_time_fix"].help,
        KPI_DEFS["pm_peer_share"].help,
        KPI_DEFS["call_peer_share"].help,
        "Shift, crew and lead assignments use the current technician master, not historical assignments. "
        "Team leads are identified in the roster; their team's KPIs are not labeled as personal lead KPIs. "
        "The existing SMT chart anomaly adjustment is not applied to review calculations.",
        "N/A means unavailable, not zero. Calendar status does not certify complete source imports. "
        "Individual peer shares use the full eligible shift-and-crew group, not only the displayed roster.",
    ]
    for label, start, stop in ranges:
        end = min(stop, as_of + timedelta(days=1))
        if end <= start:
            values[label] = pd.Series(dtype="float64")
            for person in individual_values.values():
                person[label] = pd.Series(dtype="float64")
            periods.append((label, start.isoformat(), "", "Not started"))
            continue
        state = FilterState(
            week_keys=week_keys_for_dates(start, end - timedelta(days=1), offset_hours),
            date_start=datetime.combine(start, time()), date_end=datetime.combine(end, time()),
            shifts=[shift], technician_ids=[] if area_ids is None else area_ids or [-1],
        )
        inputs = apply_filters(base, state)
        team = shift_scorecard(inputs, area_techs)
        values[label] = team.iloc[0] if not team.empty else pd.Series(dtype="float64")
        people = technician_scorecard(inputs, techs).set_index("technician_id")
        activity_ids.update(people.index)
        pm_shares = technician_pm_shares(base, state).set_index("technician_id")
        call_shares = technician_call_shares(base, state).set_index("technician_id")
        for tech_id, person in individual_values.items():
            row = people.loc[tech_id].copy() if tech_id in people.index else pd.Series(dtype="float64")
            person[label] = pd.concat([row, pm_shares.loc[tech_id], call_shares.loc[tech_id]])
        periods.append((label, start.isoformat(), (end - timedelta(days=1)).isoformat(),
                        "Complete" if end == stop else "Partial - through cutoff"))
        for source, frame, stamp in (("Fiix", inputs.fiix, "date_completed"),
                                     ("Ignition", inputs.calls, "call_time")):
            if frame.empty:
                notices.append(f"{label}: no {source} records for this team.")
            else:
                notices.append(f"{label}: latest {source} team record: {frame[stamp].max():%Y-%m-%d}.")
        if inputs.dayforce.empty or not inputs.dayforce["total_hours_worked"].notna().any():
            notices.append(f"{label}: no Dayforce hours for this team.")
    ends = pd.to_datetime(roster["end_date"], errors="coerce")
    roster = roster[roster["active"].astype(bool) | ends.ge(pd.Timestamp(date(year, 1, 1))) |
                    roster["technician_id"].isin(activity_ids)].sort_values("technician_name")
    roster["Review role"] = roster["is_lead"].fillna(False).map({True: "Team lead", False: "Technician"})
    roster = roster.rename(columns={"technician_name": "Technician", "crew": "Crew", "active": "Active"})
    roster = roster[["technician_id", "Technician", "Crew", "Review role", "Active"]]
    individuals = {int(t): review_metrics(individual_values[int(t)], TECHNICIAN_KPIS, targets)
                   for t in roster["technician_id"]}
    return ShiftReview(
        shift, area, year, as_of, review_metrics(values, PEOPLE_KPIS, targets), individuals, roster,
        pd.DataFrame(periods, columns=["Period", "Start date", "Through date (inclusive)", "Calendar status"]),
        tuple(notices),
    )


def build_shift_review_pdf(report: ShiftReview) -> bytes:
    sections = [
        ReportSection("Team quarterly and year-to-date performance KPIs",
                      table=format_review_metrics(report.metrics)),
        ReportSection("Selected shift and crew-area roster", table=report.roster.drop(columns="technician_id")),
    ]
    for person in report.roster.to_dict("records"):
        sections.append(ReportSection(
            f"Individual performance - {person['Technician']} ({person['Review role']})",
            table=format_review_metrics(report.individuals[person["technician_id"]])))
    sections.append(ReportSection("Review periods and calculation notes", table=report.periods,
                                  note=" ".join(report.notices[1:])))
    return build_dashboard_pdf(
        title=f"Team Lead Performance Review - {report.shift} - {report.area}",
        period=f"{report.year} calendar quarters and YTD - as of {report.as_of.isoformat()}",
        filters={"Shift": report.shift, "Crew area": report.area, "Review year": str(report.year),
                 "Cutoff": report.as_of.isoformat(), "Scope": "Team KPIs and individual technician breakdown"},
        metrics=[], sections=sections, notices=[report.notices[0]],
    )
