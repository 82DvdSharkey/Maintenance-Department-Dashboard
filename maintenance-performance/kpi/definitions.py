"""KPI metadata: labels, units, direction and tooltip definitions."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KPIDef:
    key: str
    label: str
    column: str
    unit: str  # % | min | count
    direction: str | None  # higher | lower | None
    help: str


KPI_DEFS: dict[str, KPIDef] = {
    d.key: d
    for d in [
        KPIDef("labor_efficiency", "Labor Efficiency", "labor_efficiency_pct", "%", "higher",
               "Qualifying Fiix logged maintenance hours divided by Dayforce hours worked, paired per technician. "
               "Only WO Types marked 'counts toward labor' are included. Department and shift values exclude Leads "
               "and inactive technicians (Administration → Technicians); individual technicians are still shown."),
        KPIDef("first_time_fix", "First-Time Fix", "ftf_pct", "%", "higher",
               "Percentage of technician-completed work orders whose WO Type contains 'Reactive' without another "
               "such work order created on the exact same asset number (TL) within the "
               "configured repeat-failure window. Calculated per active technician; shift % is the average of its "
               "technicians' %, and department % is the average of the shift %. Recent WOs may still be inside their window."),
        KPIDef("pm_completion", "PM Completion", "pm_completion_pct", "%", "higher",
               "Department KPI, reported separately per area. % of open PMs closed: PMs Closed ÷ PMs Open, from the "
               "weekly Open vs Closed entries (Data Entry)."),
        KPIDef("pm_closed", "PMs Closed", "pm_completed", "count", None,
               "Technician KPI. Number of Fiix 'Preventative (PM)' work orders with a Closed status completed by the "
               "technician (Completed By). Duplicate / admin-only closures are excluded. Shift value = sum of its technicians."),
        KPIDef("pm_peer_share", "PM Completion Share (Shift/Crew)", "pm_peer_share_pct", "%", "higher",
               "Own closed Fiix PMs divided by total closed PMs for the same shift AND crew, including self. "
               "Uses active, non-lead maintenance technicians, including peers with zero completions. "
               "N/A without both designations, another eligible peer, or group completions. "
               "This is a share of completions, not completion of assigned PMs or department PM completion."),
        KPIDef("maintenance_calls", "Maintenance Calls", "maintenance_calls", "count", None,
               "Count of Ignition calls where CallType = Maintenance (trimmed, case-insensitive). "
               "Department totals include calls with no TechName."),
        KPIDef("call_peer_share", "Maintenance Call Share (Shift/Crew)", "call_peer_share_pct", "%", "higher",
               "Own Ignition maintenance calls divided by total maintenance calls for the same shift AND crew, "
               "including self, times 100. Uses active, non-lead maintenance technicians. "
               "Person filters do not reduce the group. N/A without both designations, another eligible peer, "
               "or group calls. Uses current technician master designations."),
       KPIDef("avg_ack_min", "MTTA", "avg_ack_duration_min", "min", "lower",
               "Average time from maintenance request to technician acknowledgement. RawAckDuration ÷ 60."),
        KPIDef("avg_response_min", "Avg Response Time", "avg_response_duration_min", "min", "lower",
               "Average time from acknowledgement/response stage to technician arrival based on the Ignition "
               "RawResponseDuration field. RawResponseDuration ÷ 60."),
       KPIDef("avg_repair_min", "MTTR", "avg_repair_duration_min", "min", "lower",
               "Average repair duration based on Ignition RawRepairDuration. RawRepairDuration ÷ 60."),
        KPIDef("avg_call_min", "Avg Total Call Duration", "avg_call_duration_min", "min", "lower",
               "Average total maintenance call duration based on Ignition RawCallDuration. RawCallDuration ÷ 60."),
    ]
}

EXECUTIVE_KPIS = ["labor_efficiency", "first_time_fix", "pm_completion", "maintenance_calls",
                  "avg_ack_min", "avg_response_min", "avg_repair_min", "avg_call_min"]
# PM Completion % is a department KPI; technicians and shifts are measured on closed PM work orders instead.
PEOPLE_KPIS = ["labor_efficiency", "first_time_fix", "pm_closed", "maintenance_calls",
               "avg_ack_min", "avg_response_min", "avg_repair_min", "avg_call_min"]
TECHNICIAN_KPIS = [*PEOPLE_KPIS[:3], "pm_peer_share", "call_peer_share", *PEOPLE_KPIS[4:]]


def pm_area_defs(areas: list[str]) -> list[KPIDef]:
    """One PM Completion KPI per area; key stays 'pm_completion' so the shared PM target applies."""
    from kpi.pm_completion import pm_area_column

    base = KPI_DEFS["pm_completion"]
    return [KPIDef(base.key, f"PM Completion – {a}", pm_area_column(a), base.unit, base.direction,
                   f"{a} only. " + base.help) for a in areas]


def executive_defs(areas: list[str]) -> list[KPIDef]:
    """Department KPI cards with PM Completion split by area (Final Assembly, SMT)."""
    defs: list[KPIDef] = []
    for key in EXECUTIVE_KPIS:
        defs.extend(pm_area_defs(areas) if key == "pm_completion" else [KPI_DEFS[key]])
    return defs
