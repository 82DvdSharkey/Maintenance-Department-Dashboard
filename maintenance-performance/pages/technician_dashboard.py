"""Technician scorecard and technician detail profile."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from kpi.dataset import technician_end_weeks
from kpi.definitions import KPI_DEFS, TECHNICIAN_KPIS
from kpi.first_time_fix import repeat_failure_detail
from kpi.ignition_metrics import summarize_calls
from kpi.labor_efficiency import labor_breakdown
from ui.components import (
    CALL_DETAIL_COLUMNS,
    CALL_SUMMARY_COLUMNS,
    REPEAT_COLUMNS,
    TECH_SCORECARD_COLUMNS,
    WO_COLUMNS,
    bar_chart,
    kpi_card_grid,
    no_data_message,
    show_table,
    trend_chart,
)
from ui.pdf_reporting import ReportSection, metrics_from_kpis, render_pdf_download_button

TECH_TREND_KPIS = ["labor_efficiency", "first_time_fix", "pm_closed", "pm_peer_share",
                   "avg_response_min", "avg_repair_min"]


def render(ctx) -> None:
    st.title("Technician Performance")
    pdf_slot = st.container()
    if not ctx.base.has_data:
        no_data_message()
        render_pdf_download_button(ctx, "Technician Performance", [], [],
                                   key="pdf_technician_empty", container=pdf_slot)
        return

    st.subheader(f"Technician KPI Scorecard — {ctx.filters.label}")
    techs = ctx.base.technicians
    period_start = min(ctx.filters.week_keys) if ctx.filters.week_keys else None
    end_weeks = technician_end_weeks(techs)
    active_ids = {int(t) for t, a in zip(techs["technician_id"], techs["active"])
                  if a or (period_start and pd.notna(end_weeks.get(t)) and end_weeks.get(t) >= period_start)}
    sc = ctx.technician_scorecard()
    sc = sc[sc["technician_id"].isin(active_ids)]
    pdf_sections = []
    if sc.empty:
        st.info("No active technician activity for this selection. Technicians must be active in the master table "
                "(or inactive with an End Date inside the period) before technician metrics are shown.")
    else:
        show_table(sc, TECH_SCORECARD_COLUMNS, key="tech_scorecard")
        compare_figure = None
        key = st.selectbox("Compare technicians by", TECHNICIAN_KPIS, format_func=lambda k: KPI_DEFS[k].label,
                           key="tech_compare_kpi")
        d = KPI_DEFS[key]
        compare_figure = bar_chart(sc, "technician_name", d.column, f"{d.label} by Technician", d.unit,
                                   ctx.targets.get(key, {}).get("target"), key="tech_compare_bar")
        pdf_sections.append(ReportSection("Technician scorecard", table=sc, table_columns=TECH_SCORECARD_COLUMNS,
                                           charts=[compare_figure] if compare_figure is not None else []))
        pdf_compare_filter = d.label

    st.divider()
    options = sorted(active_ids, key=ctx.tech_name)
    if not options:
        render_pdf_download_button(ctx, "Technician Performance", [], pdf_sections,
                                   notices=["No active technician is available for this selection."],
                                   key="pdf_technician_no_options", container=pdf_slot)
        return
    tech_id = st.selectbox("Technician detail", options, format_func=ctx.tech_name, key="tech_detail")
    shift = techs.set_index("technician_id")["shift"].get(tech_id)
    crew = techs.set_index("technician_id")["crew"].get(tech_id)
    st.header(ctx.tech_name(tech_id))
    st.caption(f"Shift: **{shift or 'Not assigned'}** · Current calendar week: **{ctx.week_caption()}**")
    st.caption(f"Crew: **{crew or 'Not assigned'}**. PM completion share compares active, non-lead "
               "technicians in the same shift AND crew, including self. Person filters do not reduce the group.")

    trend = ctx.trend(technician_ids=[tech_id])
    previous = trend.iloc[-2] if len(trend) > 1 else None
    kpi_card_grid([KPI_DEFS[k] for k in TECHNICIAN_KPIS], trend.iloc[-1], previous, ctx.targets)
    pm_note = trend.iloc[-1]["pm_peer_reason"] or KPI_DEFS["pm_peer_share"].help
    st.caption(pm_note)
    pdf_metrics = metrics_from_kpis([KPI_DEFS[k] for k in TECHNICIAN_KPIS], trend.iloc[-1], previous, ctx.targets)

    st.subheader(f"{ctx.trend_weeks}-Week Trends")
    trend_figures = []
    for i in range(0, len(TECH_TREND_KPIS), 2):
        cols = st.columns(2)
        for col, key in zip(cols, TECH_TREND_KPIS[i:i + 2]):
            with col:
                trend_figures.append(trend_chart(trend, KPI_DEFS[key], ctx.targets.get(key, {}).get("target"),
                                                 key=f"tech_trend_{key}"))
    pdf_sections.append(ReportSection(f"{ctx.trend_weeks}-week trends", charts=trend_figures))

    inputs = ctx.inputs_for(technician_ids=[tech_id])
    st.subheader(f"Detail — {ctx.filters.label}")
    t1, t2, t3, t4, t5, t6 = st.tabs(["Fiix Labor by WO Type", "Recent Reactive WOs", "Repeat Failures",
                                      "Completed PMs", "Ignition Calls by Area", "Recent Maintenance Calls"])
    with t1:
        labor_by_type = labor_breakdown(inputs.labor_rows, "wo_type")
        labor_columns = {"wo_type": "WO Type", "logged_hours": "Logged Hours", "work_orders": "Work Orders",
                         "share_pct": "Share %"}
        show_table(labor_by_type, labor_columns)
        pdf_sections.append(ReportSection("Fiix labor by work-order type", table=labor_by_type,
                                           table_columns=labor_columns))
    with t2:
        recent = inputs.ftf.sort_values("date_completed", ascending=False).head(100)
        show_table(recent, {**WO_COLUMNS, "ftf_success": "First-Time Fix", "repeat_wo": "Repeat WO"})
        pdf_sections.append(ReportSection("Recent reactive work orders", table=recent,
                                           table_columns={**WO_COLUMNS, "ftf_success": "First-Time Fix",
                                                          "repeat_wo": "Repeat WO"}))
    with t3:
        repeats = repeat_failure_detail(inputs.ftf, ctx.base.fiix)
        show_table(repeats, REPEAT_COLUMNS)
        pdf_sections.append(ReportSection("Repeat failure detail", table=repeats, table_columns=REPEAT_COLUMNS,
                                           note=f"Legacy FTF matching uses {ctx.base.ftf_config.asset_rule.replace('_', ' ')}."))
    with t4:
        completed_pm = inputs.pm_done.sort_values("date_completed", ascending=False)
        show_table(completed_pm, WO_COLUMNS)
        pdf_sections.append(ReportSection("Completed preventive-maintenance work orders", table=completed_pm,
                                           table_columns=WO_COLUMNS))
    with t5:
        calls_by_area = summarize_calls(inputs.calls, ["ign_area_name"]).sort_values(
            "maintenance_calls", ascending=False)
        area_columns = {"ign_area_name": "IgnAreaName", **CALL_SUMMARY_COLUMNS}
        show_table(calls_by_area, area_columns)
        pdf_sections.append(ReportSection("Ignition calls by area", table=calls_by_area,
                                           table_columns=area_columns))
    with t6:
        recent_calls = inputs.calls.sort_values("call_time", ascending=False).head(100)
        show_table(recent_calls, CALL_DETAIL_COLUMNS)
        pdf_sections.append(ReportSection("Recent maintenance calls", table=recent_calls,
                                           table_columns=CALL_DETAIL_COLUMNS))

    legacy_note = (f"Legacy First-Time Fix is a work-order-level estimate matched by "
                   f"{ctx.base.ftf_config.asset_rule.replace('_', ' ')}. It is not reviewer-classified incident FTF.")
    extra_filters = {"Technician detail": ctx.tech_name(tech_id), "Initial shift": shift or "Not assigned"}
    if sc.empty:
        extra_filters["Technician comparison KPI"] = "Not available"
    else:
        extra_filters["Technician comparison KPI"] = pdf_compare_filter
    render_pdf_download_button(ctx, "Technician Performance", pdf_metrics, pdf_sections,
                               extra_filters=extra_filters, notices=[legacy_note, pm_note],
                               key="pdf_technician", container=pdf_slot)
