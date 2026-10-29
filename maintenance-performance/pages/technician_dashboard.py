"""Technician scorecard and technician detail profile."""
from __future__ import annotations

from dataclasses import replace

import pandas as pd
import streamlit as st

from kpi.dataset import apply_filters, technician_end_weeks
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
from ui.technician_reports import render_review_download
from utils.dates import week_keys_between, week_label

TECH_TREND_KPIS = ["labor_efficiency", "first_time_fix", "pm_closed", "pm_peer_share",
                   "call_peer_share", "avg_response_min", "avg_repair_min"]


def year_to_date_context(ctx):
    """Context covering week 1 of the selected end week's year through that week; other filters are kept."""
    end_week = ctx.filters.end_week
    keys = week_keys_between((end_week // 100) * 100 + 1, end_week)
    label = f"{end_week // 100} year to date (through {week_label(end_week)})"
    filters = replace(ctx.filters, week_keys=keys, date_start=None, date_end=None, label=label)
    return replace(ctx, filters=filters, inputs=apply_filters(ctx.base, filters))


def period_snapshot(scorecard: pd.DataFrame, technician_id: int, template: pd.Series, defs) -> pd.Series:
    """One technician's whole-period KPI values (scorecard row) shaped like a weekly trend row."""
    out = template.copy()
    for column in out.index:
        if str(column).endswith("_r4"):
            out[column] = float("nan")
    row = scorecard[scorecard["technician_id"] == technician_id]
    for definition in defs:
        out[definition.column] = (row.iloc[0][definition.column]
                                  if not row.empty and definition.column in row.columns else float("nan"))
    return out


def render(ctx) -> None:
    st.title("Technician Performance")
    pdf_slot = st.container()
    if not ctx.base.has_data:
        no_data_message()
        return

    if st.toggle("Year to date", key="tech_ytd",
                 help="Report every technician from week 1 of the selected end week's year through that week. "
                      "Shift, technician and FF Group filters still apply."):
        ctx = year_to_date_context(ctx)

    st.subheader(f"Technician KPI Scorecard — {ctx.filters.label}")
    techs = ctx.base.technicians
    period_start = min(ctx.filters.week_keys) if ctx.filters.week_keys else None
    end_weeks = technician_end_weeks(techs)
    active_ids = {int(t) for t, a in zip(techs["technician_id"], techs["active"])
                  if a or (period_start and pd.notna(end_weeks.get(t)) and end_weeks.get(t) >= period_start)}
    sc = ctx.technician_scorecard()
    sc = sc[sc["technician_id"].isin(active_ids)]
    if sc.empty:
        st.info("No active technician activity for this selection. Technicians must be active in the master table "
                "(or inactive with an End Date inside the period) before technician metrics are shown.")
    else:
        show_table(sc, TECH_SCORECARD_COLUMNS, key="tech_scorecard")
        key = st.selectbox("Compare technicians by", TECHNICIAN_KPIS, format_func=lambda k: KPI_DEFS[k].label,
                           key="tech_compare_kpi")
        d = KPI_DEFS[key]
        bar_chart(sc, "technician_name", d.column, f"{d.label} by Technician", d.unit,
                  ctx.targets.get(key, {}).get("target"), key="tech_compare_bar")

    st.divider()
    options = sorted(active_ids, key=ctx.tech_name)
    if not options:
        st.info("No active technician is available for this selection.")
        return
    tech_id = st.selectbox("Technician detail", options, format_func=ctx.tech_name, key="tech_detail")
    shift = techs.set_index("technician_id")["shift"].get(tech_id)
    crew = techs.set_index("technician_id")["crew"].get(tech_id)
    st.header(ctx.tech_name(tech_id))
    st.caption(f"Shift: **{shift or 'Not assigned'}** · Current calendar week: **{ctx.week_caption()}**")
    st.caption(f"Crew: **{crew or 'Not assigned'}**. PM completion and maintenance-call shares compare active, non-lead "
               "technicians in the same shift AND crew, including self. Person filters do not reduce the group.")

    trend = ctx.trend(technician_ids=[tech_id])
    technician_defs = [KPI_DEFS[k] for k in TECHNICIAN_KPIS]
    multi_week = len(ctx.filters.week_keys) > 1 or ctx.filters.date_start is not None
    if multi_week:
        # Week-range / date-range selections report the whole period, not just its final (possibly idle) week.
        current, previous = period_snapshot(sc, tech_id, trend.iloc[-1], technician_defs), None
        pm_note = KPI_DEFS["pm_peer_share"].help
        st.caption(f"KPI cards show {ctx.tech_name(tech_id)}'s totals for the selected period "
                   f"({ctx.filters.label}); the charts below remain weekly.")
    else:
        current = trend.iloc[-1]
        previous = trend.iloc[-2] if len(trend) > 1 else None
        pm_note = current["pm_peer_reason"] or KPI_DEFS["pm_peer_share"].help
    kpi_card_grid(technician_defs, current, previous, ctx.targets, compare=not multi_week)
    st.caption(pm_note)
    st.caption(KPI_DEFS["call_peer_share"].help if multi_week else
               current["call_peer_reason"] or KPI_DEFS["call_peer_share"].help)

    st.subheader(f"{ctx.trend_weeks}-Week Trends")
    for i in range(0, len(TECH_TREND_KPIS), 2):
        cols = st.columns(2)
        for col, key in zip(cols, TECH_TREND_KPIS[i:i + 2]):
            with col:
                trend_chart(trend, KPI_DEFS[key], ctx.targets.get(key, {}).get("target"),
                            key=f"tech_trend_{key}")

    inputs = ctx.inputs_for(technician_ids=[tech_id])
    st.subheader(f"Detail — {ctx.filters.label}")
    t1, t2, t3, t4, t5, t6 = st.tabs(["Fiix Labor by WO Type", "Recent Reactive WOs", "Repeat Failures",
                                      "Completed PMs", "Ignition Calls by Area", "Recent Maintenance Calls"])
    with t1:
        labor_by_type = labor_breakdown(inputs.labor_rows, "wo_type")
        labor_columns = {"wo_type": "WO Type", "logged_hours": "Logged Hours", "work_orders": "Work Orders",
                         "share_pct": "Share %"}
        show_table(labor_by_type, labor_columns)
    with t2:
        recent = inputs.ftf.sort_values("date_completed", ascending=False).head(100)
        show_table(recent, {**WO_COLUMNS, "ftf_success": "First-Time Fix", "repeat_wo": "Repeat WO"})
    with t3:
        repeats = repeat_failure_detail(inputs.ftf, ctx.base.fiix)
        show_table(repeats, REPEAT_COLUMNS)
    with t4:
        completed_pm = inputs.pm_done.sort_values("date_completed", ascending=False)
        show_table(completed_pm, WO_COLUMNS)
    with t5:
        calls_by_area = summarize_calls(inputs.calls, ["ign_area_name"]).sort_values(
            "maintenance_calls", ascending=False)
        area_columns = {"ign_area_name": "IgnAreaName", **CALL_SUMMARY_COLUMNS}
        show_table(calls_by_area, area_columns)
    with t6:
        recent_calls = inputs.calls.sort_values("call_time", ascending=False).head(100)
        show_table(recent_calls, CALL_DETAIL_COLUMNS)

    render_review_download(ctx, tech_id, ctx.filters.end_week // 100, pdf_slot)
