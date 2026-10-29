"""Executive / Maintenance Department dashboard."""
from __future__ import annotations

from dataclasses import replace

import streamlit as st
import pandas as pd

from kpi.definitions import KPI_DEFS, PEOPLE_KPIS, executive_defs
from kpi.dataset import crew_area_technician_ids
from kpi.ignition_metrics import summarize_calls
from kpi.pm_completion import pm_area_summary
from ui.components import (
    PM_AREA_COLUMNS,
    SHIFT_SCORECARD_COLUMNS,
    TECH_SCORECARD_COLUMNS,
    bar_chart,
    fmt,
    kpi_card_grid,
    no_data_message,
    pareto_chart,
    show_table,
    trend_chart,
)
from ui.context import DashboardContext
from ui.pdf_reporting import ReportSection, metrics_from_kpis, render_pdf_download_button

COMPARISON_KPIS = PEOPLE_KPIS
FF_GROUP_FILTERS: dict[str, tuple[str, ...]] = {
    "All FF Groups": (),
    "FA": ("FA",),
    "SMT": ("SMT",),
    "Post SMT": ("POST SMT",),
}


def _context_for_ff_group(ctx: DashboardContext, selection: str) -> DashboardContext:
    filters = replace(ctx.filters, ff_groups=list(FF_GROUP_FILTERS[selection]))
    filtered = replace(ctx, filters=filters)
    return replace(filtered, inputs=filtered.inputs_for())


def _labor_context_for_ff_group(ctx: DashboardContext, selection: str) -> DashboardContext:
    if selection not in {"FA", "SMT", "Post SMT"}:
        return ctx
    crew_areas = ctx.base.app_settings.get("crew_areas") or {}
    crew_area = "Final Assembly" if selection == "FA" else "SMT"
    crew_match = crew_areas.get(crew_area, crew_area)
    technician_ids = crew_area_technician_ids(ctx.base.technicians, crew_match)
    if ctx.filters.technician_ids:
        technician_ids = [i for i in technician_ids if i in ctx.filters.technician_ids]
    filters = replace(ctx.filters, ff_groups=[], technician_ids=technician_ids or [-1])
    labor_context = replace(ctx, filters=filters)
    return replace(labor_context, inputs=labor_context.inputs_for())


def render(ctx) -> None:
    st.title("Maintenance Department Dashboard")
    pdf_slot = st.container()
    if not ctx.base.has_data:
        no_data_message()
        render_pdf_download_button(ctx, "Maintenance Department Dashboard", [], [],
                                   key="pdf_executive_empty", container=pdf_slot)
        return
    ff_group = st.selectbox("FF Group", list(FF_GROUP_FILTERS), key="exec_ff_group")
    ctx = _context_for_ff_group(ctx, ff_group)
    labor_ctx = _labor_context_for_ff_group(ctx, ff_group)
    labor_scope = ("Final Assembly crew" if ff_group == "FA" else "SMT-designated technicians"
                   if ff_group in {"SMT", "Post SMT"} else None)
    st.caption("This selector overrides the sidebar FF Group filter for Fiix work-order metrics. "
               + (f"When {ff_group} is selected, Labor Efficiency uses {labor_scope} across all FF Groups. "
                  if labor_scope else "")
               + "Ignition calls and PM completion by area are not tied to FF Group.")
    st.caption(f"{ctx.settings.get('department_name', '')} · Current calendar week: **{ctx.week_caption()}** · "
               f"Period filter: {ctx.filters.label}")

    trend = ctx.trend()
    if labor_ctx is not ctx:
        labor_trend = labor_ctx.trend()
        for column in ("labor_efficiency_pct", "labor_efficiency_pct_r4"):
            trend[column] = labor_trend[column]
    current = trend.iloc[-1]
    previous = trend.iloc[-2] if len(trend) > 1 else None
    defs = executive_defs(ctx.pm_areas)
    kpi_card_grid(defs, current, previous, ctx.targets, per_row=3)
    pdf_metrics = metrics_from_kpis(defs, current, previous, ctx.targets)
    pdf_sections = []
    pdf_labor_scope = ("Final Assembly crew (all FF Groups)" if ff_group == "FA" else
                       "SMT-designated technicians (all FF Groups)" if ff_group in {"SMT", "Post SMT"} else None)
    pdf_filters = {"Labor Efficiency scope": pdf_labor_scope} if pdf_labor_scope else {}

    tab_names = ["Weekly Trends", "Shift Comparison", "Ignition Areas", "Period Summary"]
    if ctx.is_admin:
        tab_names.insert(2, "Technician Comparison")
    tabs = dict(zip(tab_names, st.tabs(tab_names)))
    with tabs["Weekly Trends"]:
        chart_defs = [d for d in defs if d.key != "maintenance_calls"] + [KPI_DEFS["maintenance_calls"]]
        trend_figures = []
        for i in range(0, len(chart_defs), 2):
            cols = st.columns(2)
            for col, d in zip(cols, chart_defs[i:i + 2]):
                with col:
                    target = ctx.targets.get(d.key, {}).get("target")
                    trend_figures.append(trend_chart(trend, d, target, key=f"exec_trend_{d.column}"))
        pdf_sections.append(ReportSection("Weekly KPI trends", charts=trend_figures))

    with tabs["Shift Comparison"]:
        shift_key = st.selectbox("KPI", COMPARISON_KPIS, format_func=lambda k: KPI_DEFS[k].label, key="exec_shift_kpi")
        sc = ctx.shift_scorecard()
        if shift_key == "labor_efficiency" and labor_ctx is not ctx:
            labor_sc = labor_ctx.shift_scorecard()[["shift", "labor_efficiency_pct"]]
            sc = sc.drop(columns="labor_efficiency_pct").merge(labor_sc, on="shift", how="left")
        d = KPI_DEFS[shift_key]
        shift_figure = bar_chart(sc, "shift", d.column, f"{d.label} by Shift — {ctx.filters.label}", d.unit,
                                 ctx.targets.get(shift_key, {}).get("target"), key="exec_shift_bar")
        show_table(sc, SHIFT_SCORECARD_COLUMNS)
        pdf_filters["Shift comparison KPI"] = d.label
        pdf_sections.append(ReportSection("Shift comparison", table=sc, table_columns=SHIFT_SCORECARD_COLUMNS,
                                           charts=[shift_figure] if shift_figure is not None else []))

    if ctx.is_admin:
        with tabs["Technician Comparison"]:
            technician_key = st.selectbox("KPI", COMPARISON_KPIS, format_func=lambda k: KPI_DEFS[k].label,
                                          key="exec_tech_kpi")
            d = KPI_DEFS[technician_key]
            technician_sc = ctx.technician_scorecard()
            if technician_key == "labor_efficiency" and labor_ctx is not ctx:
                labor_sc = labor_ctx.technician_scorecard()[["technician_id", "labor_efficiency_pct"]]
                technician_sc = (technician_sc.drop(columns="labor_efficiency_pct")
                                 .merge(labor_sc, on="technician_id", how="left"))
            technician_figure = bar_chart(technician_sc, "technician_name", d.column,
                                          f"{d.label} by Technician — {ctx.filters.label}", d.unit,
                                          ctx.targets.get(technician_key, {}).get("target"), key="exec_tech_bar")
            pdf_filters["Technician comparison KPI"] = d.label
            pdf_sections.append(ReportSection("Technician comparison", table=technician_sc,
                                               table_columns=TECH_SCORECARD_COLUMNS,
                                               charts=[technician_figure] if technician_figure is not None else []))

    with tabs["Ignition Areas"]:
        areas = summarize_calls(ctx.inputs.calls, ["ign_area_name"])
        cumulative = st.checkbox("Show cumulative %", value=True, key="exec_pareto_cum")
        pareto_figure = pareto_chart(areas, "ign_area_name", "maintenance_calls",
                                     "Maintenance Call Count by IgnAreaName", cumulative, key="exec_pareto")
        repair_figure = bar_chart(areas, "ign_area_name", "avg_repair_duration_min",
                                  "Average Repair Duration by IgnAreaName", "min",
                                  ctx.targets.get("avg_repair_min", {}).get("target"), key="exec_repair_area")
        pdf_filters["Cumulative Pareto"] = "Shown" if cumulative else "Hidden"
        pdf_sections.append(ReportSection("Ignition area analysis", table=areas,
                                           table_columns={"ign_area_name": "IgnAreaName"},
                                           charts=[figure for figure in (pareto_figure, repair_figure)
                                                   if figure is not None]))

    with tabs["Period Summary"]:
        dept = ctx.department_summary()
        labor_dept = labor_ctx.department_summary()
        st.caption(f"Department totals for the selected period ({ctx.filters.label}).")
        cols = st.columns(4)
        summary = [
            ("Fiix Logged Hours", labor_dept.get("logged_hours"), "h"),
            ("Dayforce Hours", labor_dept.get("worked_hours"), "h"),
            ("Qualifying Reactive WOs", dept.get("reactive_wos"), "count"),
            ("Repeat Failures", dept.get("repeat_failures"), "count"),
            ("Maintenance Calls", dept.get("maintenance_calls"), "count"),
        ]
        for i, (label, value, unit) in enumerate(summary):
            cols[i % 4].metric(label, fmt(value, unit))
        st.markdown("**PM Completion by area** (reported separately for Final Assembly and SMT)")
        pm_by_area = pm_area_summary(ctx.inputs.pm_status, ["area"])
        show_table(pm_by_area, PM_AREA_COLUMNS)
        pdf_sections.append(ReportSection("Department period summary",
                                           table=pd.DataFrame(summary, columns=["KPI", "Value", "Unit"])))
        pdf_sections.append(ReportSection("PM completion by area", table=pm_by_area,
                                           table_columns=PM_AREA_COLUMNS))

    render_pdf_download_button(ctx, "Maintenance Department Dashboard", pdf_metrics, pdf_sections,
                               extra_filters=pdf_filters, key="pdf_executive", container=pdf_slot)
