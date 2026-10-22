"""Ignition maintenance-call performance: technicians, areas, machines and individual calls."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from kpi.definitions import KPI_DEFS
from kpi.ignition_metrics import summarize_calls
from ui.components import (
    CALL_DETAIL_COLUMNS,
    CALL_SUMMARY_COLUMNS,
    bar_chart,
    kpi_card,
    no_data_message,
    pareto_chart,
    show_table,
    trend_chart,
)
from ui.pdf_reporting import ReportMetric, ReportSection, render_pdf_download_button

GROUP_OPTIONS = {"Shift": "shift", "IgnAreaName": "ign_area_name", "IgnMachineName": "ign_machine_name",
                 "FocusFactory": "focus_factory", "Calendar week": "week_key"}
DURATION_KPIS = ["avg_call_min", "avg_ack_min", "avg_response_min", "avg_repair_min"]


def render(ctx) -> None:
    st.title("Ignition Performance")
    pdf_slot = st.container()
    if not ctx.base.has_data:
        no_data_message()
        render_pdf_download_button(ctx, "Ignition Performance", [], [],
                                   key="pdf_ignition_empty", container=pdf_slot)
        return
    calls = ctx.inputs.calls
    st.caption(f"Maintenance calls only (CallType = Maintenance) · Period: {ctx.filters.label}")

    overall = summarize_calls(calls).iloc[0] if not calls.empty else pd.Series(dtype="float64")
    all_calls = ctx.inputs.calls_all if ctx.inputs.calls_all is not None else calls
    unassigned = int(all_calls["tech_key"].isna().sum())
    unmapped = int((all_calls["tech_key"].notna() & all_calls["technician_id"].isna()).sum())
    other_dept = int((all_calls["technician_id"].notna() & all_calls["non_department"]).sum())
    pdf_metrics = [ReportMetric("Maintenance Calls", overall.get("maintenance_calls"), "count")]
    for key in DURATION_KPIS:
        definition = KPI_DEFS[key]
        pdf_metrics.append(ReportMetric(definition.label, overall.get(definition.column), definition.unit or "",
                                        target=ctx.targets.get(key, {}).get("target")))
    pdf_metrics.append(ReportMetric("Excluded Calls", len(all_calls) - len(calls), "count",
                                    note=f"No TechName: {unassigned:,}; unmapped: {unmapped:,}; "
                                         f"other departments: {other_dept:,}"))
    pdf_sections = []
    pdf_filters = {}
    cols = st.columns(6)
    with cols[0]:
        kpi_card(KPI_DEFS["maintenance_calls"], overall.get("maintenance_calls"), compare=False)
    for col, key in zip(cols[1:5], DURATION_KPIS):
        with col:
            d = KPI_DEFS[key]
            kpi_card(d, overall.get(d.column), target_row=ctx.targets.get(key), compare=False)
    with cols[5]:
        with st.container(border=True):
            st.metric("Excluded Calls", f"{len(all_calls) - len(calls):,}",
                      help="Calls not answered by a Dayforce-mapped technician. They are left out of all "
                      "KPIs; see Data Quality.")
            st.caption(f"No TechName: {unassigned:,} · Unmapped TechNames: {unmapped:,} · "
                       f"Other departments: {other_dept:,}")

    tab_names = ["Group Analysis", "Area Drill-down", "Area Pareto", "Weekly Trends"]
    if ctx.is_admin:
        tab_names.insert(0, "Technicians")
    tabs = dict(zip(tab_names, st.tabs(tab_names)))
    if ctx.is_admin:
        with tabs["Technicians"]:
            named = calls.copy()
            named["tech_display"] = named["technician"]
            tbl = summarize_calls(named, ["tech_display"])
            shift_map = named.drop_duplicates("tech_display").set_index("tech_display")["shift"]
            tbl.insert(1, "shift", tbl["tech_display"].map(shift_map))
            tech_columns = {"tech_display": "Technician", "shift": "Shift", **CALL_SUMMARY_COLUMNS}
            tech_table = tbl.sort_values("maintenance_calls", ascending=False)
            show_table(tech_table, tech_columns, key="ign_tech_table")
            pdf_sections.append(ReportSection("Maintenance calls by technician", table=tech_table,
                                               table_columns=tech_columns))

    with tabs["Group Analysis"]:
        label = st.selectbox("Group by", list(GROUP_OPTIONS), key="ign_group")
        col = GROUP_OPTIONS[label]
        tbl = summarize_calls(calls, [col]).sort_values(col if col == "week_key" else "maintenance_calls",
                                                         ascending=col == "week_key")
        show_table(tbl, {col: label, **CALL_SUMMARY_COLUMNS})
        pdf_filters["Group analysis"] = label
        pdf_sections.append(ReportSection(f"Maintenance calls grouped by {label}", table=tbl,
                           table_columns={col: label, **CALL_SUMMARY_COLUMNS}))

    with tabs["Area Drill-down"]:
        areas = summarize_calls(calls, ["ign_area_name"]).sort_values("maintenance_calls", ascending=False)
        area_columns = {"ign_area_name": "IgnAreaName", **CALL_SUMMARY_COLUMNS}
        show_table(areas, area_columns, key="ign_area_table")
        pdf_sections.append(ReportSection("Maintenance calls by area", table=areas, table_columns=area_columns))
        if not areas.empty:
            area = st.selectbox("Drill into IgnAreaName", areas["ign_area_name"].tolist(), key="ign_area_pick")
            area_calls = calls[calls["ign_area_name"] == area]
            machines = summarize_calls(area_calls, ["ign_machine_name"]).sort_values("maintenance_calls", ascending=False)
            st.markdown(f"**Machines in {area}**")
            show_table(machines, {"ign_machine_name": "IgnMachineName", **CALL_SUMMARY_COLUMNS})
            pdf_filters["Drill-down area"] = area
            pdf_sections.append(ReportSection(f"Machines in {area}", table=machines,
                                               table_columns={"ign_machine_name": "IgnMachineName",
                                                              **CALL_SUMMARY_COLUMNS}))
            if ctx.is_admin and not machines.empty:
                machine = st.selectbox("Drill into IgnMachineName", machines["ign_machine_name"].tolist(),
                                       key="ign_machine_pick")
                st.markdown(f"**Individual calls — {machine}**")
                machine_calls = area_calls[area_calls["ign_machine_name"] == machine].sort_values("call_time")
                show_table(machine_calls, CALL_DETAIL_COLUMNS)
                pdf_filters["Drill-down machine"] = machine
                pdf_sections.append(ReportSection(f"Maintenance calls for {machine}", table=machine_calls,
                                                   table_columns=CALL_DETAIL_COLUMNS))

    with tabs["Area Pareto"]:
        areas = summarize_calls(calls, ["ign_area_name"])
        cumulative = st.checkbox("Show cumulative %", value=True, key="ign_pareto_cum")
        pareto_figure = pareto_chart(areas, "ign_area_name", "maintenance_calls",
                         "Maintenance Call Count by IgnAreaName", cumulative, key="ign_pareto")
        repair_figure = bar_chart(areas, "ign_area_name", "avg_repair_duration_min",
                      "Average Repair Duration by IgnAreaName", "min",
                      ctx.targets["avg_repair_min"]["target"], key="ign_repair_area")
        pdf_filters["Cumulative Pareto"] = "Shown" if cumulative else "Hidden"
        pdf_sections.append(ReportSection("Area Pareto and repair duration", table=areas,
                           table_columns=area_columns,
                           charts=[figure for figure in (pareto_figure, repair_figure)
                               if figure is not None]))

    with tabs["Weekly Trends"]:
        trend = ctx.trend()
        keys = ["maintenance_calls"] + DURATION_KPIS
        trend_figures = []
        for i in range(0, len(keys), 2):
            row = st.columns(2)
            for c, key in zip(row, keys[i:i + 2]):
                with c:
                    trend_figures.append(trend_chart(trend, KPI_DEFS[key], ctx.targets.get(key, {}).get("target"),
                                                     key=f"ign_trend_{key}"))
        pdf_sections.append(ReportSection("Weekly maintenance-call trends", charts=trend_figures))

    render_pdf_download_button(ctx, "Ignition Performance", pdf_metrics, pdf_sections,
                               extra_filters=pdf_filters,
                               notices=["Only Ignition Maintenance calls are included; excluded calls are summarized separately."],
                               key="pdf_ignition", container=pdf_slot)
