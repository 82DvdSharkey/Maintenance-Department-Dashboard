"""Shift scorecard and shift detail."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from kpi.dataset import crew_area_technician_ids
from kpi.definitions import KPI_DEFS, PEOPLE_KPIS
from kpi.first_time_fix import ftf_summary
from kpi.ignition_metrics import summarize_calls
from kpi.labor_efficiency import labor_breakdown
from ui.components import (
    CALL_SUMMARY_COLUMNS,
    SHIFT_SCORECARD_COLUMNS,
    TECH_SCORECARD_COLUMNS,
    bar_chart,
    kpi_card_grid,
    no_data_message,
    show_table,
    trend_chart,
)
from ui.pdf_reporting import ReportSection, metrics_from_kpis, render_pdf_download_button
from ui.shift_reviews import render_shift_review

SMT_SPIKE_WEEK = 202639


def _area_filter(ctx) -> tuple[str, dict]:
    """Area selector based on the technician Crew column; returns the label and filter overrides."""
    areas = dict(ctx.base.app_settings.get("crew_areas") or {})
    label = st.segmented_control("Area", ["All areas"] + list(areas), default="All areas", key="shift_area") \
        or "All areas"
    if label == "All areas":
        return label, {}
    ids = crew_area_technician_ids(ctx.base.technicians, areas[label])
    if ctx.filters.technician_ids:
        ids = [i for i in ids if i in ctx.filters.technician_ids]
    # An empty list would mean 'no technician filter', so use an impossible id to show nothing.
    return label, {"technician_ids": ids or [-1]}


def _smooth_smt_spike_for_chart(trend: pd.DataFrame, department_efficiency: float) -> pd.DataFrame:
    """Show the known CW39 SMT shift anomaly at department efficiency without changing KPI inputs."""
    out = trend.copy()
    if pd.isna(department_efficiency):
        return out
    mask = out["week_key"].eq(SMT_SPIKE_WEEK) & out["worked_hours"].gt(0)
    if not mask.any():
        return out
    out.loc[mask, "le_logged_hours"] = out.loc[mask, "worked_hours"] * department_efficiency / 100.0
    out.loc[mask, "labor_efficiency_pct"] = department_efficiency
    worked = pd.to_numeric(out["worked_hours"], errors="coerce").fillna(0.0)
    logged = pd.to_numeric(out["le_logged_hours"], errors="coerce").fillna(0.0)
    rolling_worked = worked.rolling(4, min_periods=1).sum()
    rolling_logged = logged.rolling(4, min_periods=1).sum()
    out["labor_efficiency_pct_r4"] = rolling_logged / rolling_worked.where(rolling_worked > 0) * 100.0
    return out


def render(ctx) -> None:
    st.title("Shift Performance")
    pdf_slot = st.container()
    if not ctx.base.has_data:
        no_data_message()
        render_pdf_download_button(ctx, "Shift Performance", [], [],
                                   key="pdf_shift_empty", container=pdf_slot)
        return

    area, area_filter = _area_filter(ctx)
    area_ids = area_filter.get("technician_ids")
    techs = ctx.base.technicians
    area_techs = techs if area_ids is None else techs[techs["technician_id"].isin(area_ids)]
    in_dept = area_techs["in_department"].astype(bool) if "in_department" in area_techs else True
    reserved = area in (ctx.base.app_settings.get("reserved_crew_areas") or [])
    if area != "All areas" and (reserved or not (area_techs["active"].astype(bool) & in_dept).any()):
        crew = (ctx.base.app_settings.get("crew_areas") or {}).get(area, area)
        st.info(f"**{area}** is reserved for future use — no {area} performance is reported yet. When {area} "
                f"technicians are active, set their Crew to include **{crew}** on Administration → Technicians and "
                "turn the area on in Administration → KPI Targets & Settings.", icon=":material/schedule:")
        render_pdf_download_button(ctx, "Shift Performance", [], [], extra_filters={"Crew area": area},
                       notices=[f"No reporting data is configured for {area}."],
                       key="pdf_shift_reserved", container=pdf_slot)
        return
    period_inputs = ctx.inputs_for(**area_filter)
    suffix = "" if area == "All areas" else f" — {area}"
    pdf_sections = []
    pdf_filters = {"Crew area": area}

    st.subheader(f"Shift KPI Scorecard{suffix} — {ctx.filters.label}")
    st.caption("Shift assignment comes from the technician master table; areas come from the Crew column "
               "(Team Leads are grouped with their area). Duration KPIs are averaged over individual calls.")
    sc = ctx.shift_scorecard(period_inputs, area_techs)
    show_table(sc, SHIFT_SCORECARD_COLUMNS, key="shift_scorecard")
    pdf_sections.append(ReportSection("Shift KPI scorecard", table=sc, table_columns=SHIFT_SCORECARD_COLUMNS))

    shifts = ctx.base.shifts.loc[ctx.base.shifts["active"].astype(bool), "name"].tolist()
    if not shifts:
        render_pdf_download_button(ctx, "Shift Performance", [], pdf_sections, extra_filters=pdf_filters,
                                   key="pdf_shift_no_shifts", container=pdf_slot)
        return
    st.divider()
    shift = st.selectbox("Shift detail", shifts, key="shift_detail")
    st.header(f"{shift}{suffix}")
    st.caption(f"Current calendar week: **{ctx.week_caption()}**")
    render_shift_review(ctx, shift, area)

    trend = ctx.trend(shifts=[shift], **area_filter)
    previous = trend.iloc[-2] if len(trend) > 1 else None
    kpi_card_grid([KPI_DEFS[k] for k in PEOPLE_KPIS], trend.iloc[-1], previous, ctx.targets)
    pdf_metrics = metrics_from_kpis([KPI_DEFS[k] for k in PEOPLE_KPIS], trend.iloc[-1], previous, ctx.targets)
    labor_chart_trend = trend
    if area == "SMT" and shift in {"1st Shift", "2nd Shift"} and trend["week_key"].eq(SMT_SPIKE_WEEK).any():
        department_inputs = ctx.inputs_for(week_keys=[SMT_SPIKE_WEEK], shifts=[], technician_ids=[],
                                           ff_groups=[], areas=[], wo_types=[])
        department_efficiency = ctx.department_summary(department_inputs).get("labor_efficiency_pct")
        labor_chart_trend = _smooth_smt_spike_for_chart(trend, department_efficiency)
        st.caption(f"CW39 SMT 1st/2nd Shift Labor Efficiency chart point is shown at the department average "
                   f"({department_efficiency:.1f}%) to suppress the known Dayforce spike. KPI cards and source data "
                   "are unchanged.")
    c1, c2 = st.columns(2)
    with c1:
        labor_figure = trend_chart(labor_chart_trend, KPI_DEFS["labor_efficiency"],
                                   ctx.targets["labor_efficiency"]["target"], key="shift_trend_le")
    with c2:
        response_trend_figure = trend_chart(trend, KPI_DEFS["avg_response_min"],
                                            ctx.targets["avg_response_min"]["target"], key="shift_trend_resp")
    pdf_sections.append(ReportSection("Selected shift trends", charts=[labor_figure, response_trend_figure],
                                       note=("The displayed CW39 SMT labor-efficiency point uses the existing "
                                             "department-average adjustment." if labor_chart_trend is not trend else "")))

    inputs = ctx.inputs_for(shifts=[shift], **area_filter)
    tech_sc = ctx.technician_scorecard(inputs)
    pdf_filters["Shift detail"] = shift

    tab_names = ["Fiix Hours by WO Type", "First-Time Fix by Shift", "Ignition Calls by Area"]
    if ctx.is_admin:
        tab_names = ["Technicians", "Technician Comparison", "Fiix Hours by WO Type", "PMs Closed",
                     "First-Time Fix", "Ignition Calls", "Response / Repair"]
    tabs = dict(zip(tab_names, st.tabs(tab_names)))
    if ctx.is_admin:
        with tabs["Technicians"]:
            assigned = area_techs[(area_techs["shift"] == shift)]
            assigned_columns = {"technician_name": "Technician", "crew": "Crew", "supervisor": "Supervisor",
                                "active": "Active", "fiix_name": "Fiix Name", "ignition_name": "Ignition Name"}
            show_table(assigned, assigned_columns)
            pdf_sections.append(ReportSection("Assigned technicians", table=assigned, table_columns=assigned_columns))
        with tabs["Technician Comparison"]:
            show_table(tech_sc, TECH_SCORECARD_COLUMNS)
            key = st.selectbox("KPI", PEOPLE_KPIS, format_func=lambda k: KPI_DEFS[k].label, key="shift_tech_kpi")
            d = KPI_DEFS[key]
            tech_compare_figure = bar_chart(tech_sc, "technician_name", d.column, f"{d.label} — {shift}", d.unit,
                                            ctx.targets.get(key, {}).get("target"), key="shift_tech_bar")
            pdf_filters["Technician comparison KPI"] = d.label
            pdf_sections.append(ReportSection("Technician scorecard", table=tech_sc,
                                               table_columns=TECH_SCORECARD_COLUMNS,
                                               charts=[tech_compare_figure] if tech_compare_figure is not None else []))
    with tabs["Fiix Hours by WO Type"]:
        wo = labor_breakdown(inputs.labor_rows, "wo_type")
        labor_figure = bar_chart(wo, "wo_type", "logged_hours", "Fiix Logged Hours by WO Type", "h",
                                 key="shift_wo_bar")
        wo_columns = {"wo_type": "WO Type", "logged_hours": "Logged Hours", "work_orders": "Work Orders",
                      "share_pct": "Share %"}
        show_table(wo, wo_columns)
        pdf_sections.append(ReportSection("Fiix logged hours by work-order type", table=wo,
                                           table_columns=wo_columns,
                                           charts=[labor_figure] if labor_figure is not None else []))
    if ctx.is_admin:
        with tabs["PMs Closed"]:
            st.caption("Closed Fiix 'Preventative (PM)' work orders per technician. PM Completion % is a department KPI "
                       "(Executive and Fiix pages).")
            pm_figure = bar_chart(tech_sc, "technician_name", "pm_completed", f"PMs Closed — {shift}", "count",
                                  key="shift_pm_bar")
            pm_columns = {"technician_name": "Technician", "pm_completed": "PMs Closed"}
            show_table(tech_sc, pm_columns)
            pdf_sections.append(ReportSection("PMs closed by technician", table=tech_sc, table_columns=pm_columns,
                                               charts=[pm_figure] if pm_figure is not None else []))
        with tabs["First-Time Fix"]:
            ftf_columns = {"technician_name": "Technician", "reactive_wos": "Qualifying Reactive WOs",
                           "ftf_success": "Successful First-Time Fixes", "repeat_failures": "Repeat Failures",
                           "ftf_pct": "First-Time Fix %"}
            show_table(tech_sc, ftf_columns)
            pdf_sections.append(ReportSection("Legacy work-order FTF by technician", table=tech_sc,
                                               table_columns=ftf_columns,
                                               note="Legacy asset-TL# repeat estimate; not reviewed incident-based FTF."))
        with tabs["Ignition Calls"]:
            st.markdown("**Calls by technician**")
            calls_by_tech_columns = {"technician_name": "Technician", **CALL_SUMMARY_COLUMNS}
            show_table(tech_sc, calls_by_tech_columns)
            st.markdown("**Calls by area**")
            calls_by_area = summarize_calls(inputs.calls, ["ign_area_name"]).sort_values(
                "maintenance_calls", ascending=False)
            calls_by_area_columns = {"ign_area_name": "IgnAreaName", **CALL_SUMMARY_COLUMNS}
            show_table(calls_by_area, calls_by_area_columns)
            pdf_sections.append(ReportSection("Ignition calls by technician", table=tech_sc,
                                               table_columns=calls_by_tech_columns))
            pdf_sections.append(ReportSection("Ignition calls by area", table=calls_by_area,
                                               table_columns=calls_by_area_columns))
        with tabs["Response / Repair"]:
            c1, c2 = st.columns(2)
            with c1:
                response_figure = bar_chart(tech_sc, "technician_name", "avg_response_duration_min",
                                            "Avg Response Duration by Technician", "min",
                                            ctx.targets["avg_response_min"]["target"], key="shift_resp_bar")
            with c2:
                repair_figure = bar_chart(tech_sc, "technician_name", "avg_repair_duration_min",
                                          "Avg Repair Duration by Technician", "min",
                                          ctx.targets["avg_repair_min"]["target"], key="shift_rep_bar")
            pdf_sections.append(ReportSection("Technician response and repair duration",
                                               charts=[figure for figure in (response_figure, repair_figure)
                                                       if figure is not None]))
    else:
        with tabs["First-Time Fix by Shift"]:
            ftf_by_shift = ftf_summary(inputs.ftf, ["shift"])
            show_table(ftf_by_shift, {"shift": "Shift", "reactive_wos": "Qualifying Reactive WOs",
                                      "ftf_success": "Successful First-Time Fixes",
                                      "repeat_failures": "Repeat Failures", "ftf_pct": "First-Time Fix %"})
            pdf_sections.append(ReportSection("Legacy first-time fix by shift", table=ftf_by_shift,
                                               note="Asset-level work-order estimate; not reviewed incident-based FTF."))
        with tabs["Ignition Calls by Area"]:
            calls_by_area = summarize_calls(inputs.calls, ["ign_area_name"]).sort_values(
                "maintenance_calls", ascending=False)
            calls_by_area_columns = {"ign_area_name": "IgnAreaName", **CALL_SUMMARY_COLUMNS}
            show_table(calls_by_area, calls_by_area_columns)
            pdf_sections.append(ReportSection("Ignition calls by area", table=calls_by_area,
                                               table_columns=calls_by_area_columns))

    notices = [f"Legacy First-Time Fix is a work-order-level estimate matched by "
               f"{ctx.base.ftf_config.asset_rule.replace('_', ' ')}; it is not incident-reviewed FTF."]
    render_pdf_download_button(ctx, "Shift Performance", pdf_metrics, pdf_sections,
                               extra_filters=pdf_filters, notices=notices,
                               key="pdf_shift", container=pdf_slot)
