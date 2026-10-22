"""Executive / Maintenance Department dashboard."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from kpi.definitions import KPI_DEFS, PEOPLE_KPIS, executive_defs
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
from ui.pdf_reporting import ReportSection, metrics_from_kpis, render_pdf_download_button

COMPARISON_KPIS = PEOPLE_KPIS


def render(ctx) -> None:
    st.title("Maintenance Department Dashboard")
    pdf_slot = st.container()
    if not ctx.base.has_data:
        no_data_message()
        render_pdf_download_button(ctx, "Maintenance Department Dashboard", [], [],
                                   key="pdf_executive_empty", container=pdf_slot)
        return
    st.caption(f"{ctx.settings.get('department_name', '')} · Current calendar week: **{ctx.week_caption()}** · "
               f"Period filter: {ctx.filters.label}")

    trend = ctx.trend()
    current = trend.iloc[-1]
    previous = trend.iloc[-2] if len(trend) > 1 else None
    defs = executive_defs(ctx.pm_areas)
    kpi_card_grid(defs, current, previous, ctx.targets, per_row=3)
    pdf_metrics = metrics_from_kpis(defs, current, previous, ctx.targets)
    pdf_sections = []
    pdf_filters = {}

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
        st.caption(f"Department totals for the selected period ({ctx.filters.label}).")
        cols = st.columns(4)
        summary = [
            ("Fiix Logged Hours", dept.get("logged_hours"), "h"), ("Dayforce Hours", dept.get("worked_hours"), "h"),
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
