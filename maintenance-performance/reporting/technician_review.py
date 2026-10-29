"""Calendar-quarter and YTD KPI reports scoped to one review subject."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

import pandas as pd

from kpi.dataset import (
    BaseData, FilterState, apply_filters, technician_call_shares, technician_pm_shares,
)
from kpi.definitions import KPI_DEFS, TECHNICIAN_KPIS
from kpi.scorecard import technician_scorecard
from reporting.review_common import (
    DAYFORCE_BOUNDARY_NOTE, calendar_review_ranges, format_review_metrics, review_metrics,
)
from ui.pdf_reporting import ReportSection, build_dashboard_pdf
from utils.dates import week_keys_for_dates


@dataclass(frozen=True)
class TechnicianReview:
    technician_name: str
    shift: str
    crew: str
    year: int
    as_of: date
    metrics: pd.DataFrame
    periods: pd.DataFrame
    notices: tuple[str, ...]

    @property
    def filename(self) -> str:
        name = re.sub(r"[^\w-]+", "_", self.technician_name).strip("_")[:100] or "Technician"
        return f"Technician_Review_{name}_{self.year}_as_of_{self.as_of.isoformat()}.pdf"


def build_technician_review(base: BaseData, technician_id: int, year: int, targets: dict, *,
                            as_of: date | None = None, offset_hours: float = 1) -> TechnicianReview:
    """Pool existing KPI components over exact calendar dates; never export peer records."""
    as_of = as_of or datetime.now(ZoneInfo("America/Detroit")).date()
    master = base.technicians[base.technicians["technician_id"].eq(technician_id)]
    if len(master) != 1:
        raise ValueError("Select an existing, unique technician before generating a review.")
    ranges = calendar_review_ranges(year)
    person = master.iloc[0]
    year_end = date(year + 1, 1, 1)
    cutoff = min(as_of + timedelta(days=1), year_end)
    periods = []
    values = {}
    notices = [
        "Confidential individual performance review. Only the selected technician's KPIs are displayed. "
        "Sidebar person, shift, area, FF Group and WO Type filters do not restrict this review.",
        DAYFORCE_BOUNDARY_NOTE,
        KPI_DEFS["pm_peer_share"].help,
        KPI_DEFS["call_peer_share"].help,
        KPI_DEFS["first_time_fix"].help,
        "Peer grouping uses current technician master designations. Peer names, counts and details are not exported. "
        "N/A means the KPI cannot be calculated, not a zero result. Periods may be incomplete if imports or "
        "Dayforce entries are missing; 'complete' refers only to the calendar period.",
    ]
    for label, start, stop in ranges:
        end = min(stop, cutoff)
        if end <= start:
            periods.append((label, start.isoformat(), "", "Not started"))
            values[label] = pd.Series(dtype="float64")
            continue
        state = FilterState(
            week_keys=week_keys_for_dates(start, end - timedelta(days=1), offset_hours),
            date_start=datetime.combine(start, time()), date_end=datetime.combine(end, time()),
            technician_ids=[technician_id],
        )
        inputs = apply_filters(base, state)
        scorecard = technician_scorecard(inputs, base.technicians)
        row = scorecard.iloc[0].copy() if not scorecard.empty else pd.Series(dtype="float64")
        for shares in (technician_pm_shares(base, state), technician_call_shares(base, state)):
            own = shares.set_index("technician_id").loc[technician_id]
            row = pd.concat([row, own])
        values[label] = row
        periods.append((label, start.isoformat(), (end - timedelta(days=1)).isoformat(),
                        "Complete" if end == stop else "Partial - through cutoff"))
        for source, frame, stamp in (("Fiix", inputs.fiix, "date_completed"),
                                     ("Ignition", inputs.calls, "call_time")):
            if frame.empty:
                notices.append(f"{label}: no {source} records for the selected technician.")
            else:
                notices.append(f"{label}: latest {source} record for the selected technician: "
                               f"{frame[stamp].max():%Y-%m-%d}.")
        if inputs.dayforce.empty or not inputs.dayforce["total_hours_worked"].notna().any():
            notices.append(f"{label}: no Dayforce hours for the selected technician.")
        for prefix in ("pm_peer", "call_peer"):
            reason = row[f"{prefix}_reason"]
            if reason:
                notices.append(f"{label}: {reason}")
    return TechnicianReview(
        str(person["technician_name"]),
        str(person["shift"]) if pd.notna(person["shift"]) else "Not assigned",
        str(person["crew"]) if pd.notna(person["crew"]) else "Not assigned",
        year, as_of, review_metrics(values, TECHNICIAN_KPIS, targets),
        pd.DataFrame(periods, columns=["Period", "Start date", "Through date (inclusive)", "Calendar status"]),
        tuple(notices),
    )


def build_technician_review_pdf(report: TechnicianReview) -> bytes:
    table = format_review_metrics(report.metrics)
    return build_dashboard_pdf(
        title=f"Individual Technician Performance Review - {report.technician_name}",
        period=f"{report.year} calendar quarters and YTD - as of {report.as_of.isoformat()}",
        filters={"Technician": report.technician_name, "Shift": report.shift, "Crew": report.crew,
                 "Review year": str(report.year), "Cutoff": report.as_of.isoformat(),
                 "Scope": "Full individual performance; sidebar data filters ignored"},
        metrics=[],
        sections=[ReportSection("Quarterly and year-to-date performance KPIs", table=table),
                  ReportSection("Review periods and calculation notes", table=report.periods,
                                note=" ".join(report.notices[1:]))],
        notices=[report.notices[0]],
    )
