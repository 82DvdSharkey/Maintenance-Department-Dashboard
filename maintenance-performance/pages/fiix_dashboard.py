"""Fiix labor, First-Time Fix and PM completion analysis."""
from __future__ import annotations

import streamlit as st

from kpi.definitions import KPI_DEFS
from kpi.first_time_fix import averaged_ftf, ftf_summary, repeat_failure_detail
from kpi.labor_efficiency import labor_breakdown, labor_flags
from kpi.pm_completion import pm_area_summary
from kpi.scorecard import scorecard
from ui.components import (
    PM_AREA_COLUMNS,
    REPEAT_COLUMNS,
    WO_COLUMNS,
    bar_chart,
    fmt,
    no_data_message,
    show_table,
)
from ui.pdf_reporting import ReportMetric, ReportSection, render_pdf_download_button
from utils.dates import week_label, week_label_short

LABOR_DIMS = {"WO Type": "wo_type", "WO Classification": "classification", "Technician": "technician",
              "Shift": "shift", "FF Group": "ff_group", "Asset": "asset", "Parent Asset": "parent_asset",
              "Calendar Week": "week_key"}
SCORECARD_DIMS = {"technician": "technician_id", "shift": "shift", "week_key": "week_key"}
FTF_DIMS = {"Technician": "technician", "Shift": "shift", "Calendar Week": "week_key", "Asset": "asset"}


def _labor_table(ctx, dim: str):
    if dim in SCORECARD_DIMS:
        by = SCORECARD_DIMS[dim]
        sc = scorecard(ctx.inputs, [by])
        if by == "technician_id":
            sc["technician"] = sc["technician_id"].map(ctx.tech_name)
        cols = {dim: "Group", "logged_hours": "Logged Hours", "worked_hours": "Worked Hours",
                "labor_efficiency_pct": "Labor Efficiency %"}
        return sc, cols, "logged_hours"
    br = labor_breakdown(ctx.inputs.labor_rows, dim)
    return br, {dim: "Group", "logged_hours": "Logged Hours", "work_orders": "Work Orders", "share_pct": "Share %"}, "logged_hours"


def render(ctx) -> None:
    st.title("Fiix Performance")
    pdf_slot = st.container()
    if not ctx.base.has_data:
        no_data_message()
        render_pdf_download_button(ctx, "Fiix Performance", [], [],
                                   key="pdf_fiix_empty", container=pdf_slot)
        return
    inputs = ctx.inputs
    dept = ctx.department_summary()
    st.caption(f"Period: {ctx.filters.label} · {len(inputs.fiix):,} work orders in selection")
    pdf_metrics = [
        ReportMetric("Qualifying Logged Hours", dept.get("logged_hours"), "h"),
        ReportMetric("Dayforce Hours Worked", dept.get("worked_hours"), "h"),
        ReportMetric("Labor Efficiency", dept.get("labor_efficiency_pct"), "%",
                     target=ctx.targets.get("labor_efficiency", {}).get("target")),
        ReportMetric("Legacy First-Time Fix", dept.get("ftf_pct"), "%",
                     target=ctx.targets.get("first_time_fix", {}).get("target"),
                     note="Work-order-level asset repeat estimate; not incident-reviewed FTF."),
    ]
    pdf_sections = []
    pdf_filters = {}

    cols = st.columns(4 + len(ctx.pm_areas))
    cols[0].metric("Qualifying Logged Hours", fmt(dept.get("logged_hours"), "h"),
                   help="Fiix Time Spent on WO Types that count toward labor, for mapped technicians.")
    cols[1].metric("Dayforce Hours Worked", fmt(dept.get("worked_hours"), "h"))
    cols[2].metric("Labor Efficiency", fmt(dept.get("labor_efficiency_pct"), "%"), help=KPI_DEFS["labor_efficiency"].help)
    cols[3].metric("First-Time Fix", fmt(dept.get("ftf_pct"), "%"), help=KPI_DEFS["first_time_fix"].help)
    by_area = pm_area_summary(inputs.pm_status, ["area"]).set_index("area")
    for col, area in zip(cols[4:], ctx.pm_areas):
        row = by_area.loc[area] if area in by_area.index else {}
        col.metric(f"PM Completion – {area}", fmt(row.get("pm_completion_pct") if len(row) else None, "%"),
                   help=f"{fmt(row.get('pm_closed') if len(row) else None, 'count')} closed of "
                        f"{fmt(row.get('pm_open') if len(row) else None, 'count')} open PMs. " + KPI_DEFS["pm_completion"].help)

    tabs = st.tabs(["Labor Breakdown", "Labor Flags", "First-Time Fix", "Repeat Failures", "PM Completion",
                    "Work Orders"])
    with tabs[0]:
        label = st.selectbox("Break down logged time by", list(LABOR_DIMS), key="fiix_labor_dim")
        dim = LABOR_DIMS[label]
        table, columns, value = _labor_table(ctx, dim)
        if dim == "week_key" and not table.empty:
            table = table.sort_values("week_key")
            table["week_key"] = table["week_key"].map(week_label_short)
        labor_figure = bar_chart(table.head(40), dim, value, f"Logged Hours by {label}", "h",
                     key="fiix_labor_bar", sort_desc=None if dim == "week_key" else True)
        show_table(table, {**columns, dim: label})
        pdf_filters["Labor breakdown"] = label
        pdf_sections.append(ReportSection(f"Logged hours by {label}", table=table,
                           table_columns={**columns, dim: label},
                           charts=[labor_figure] if labor_figure is not None else []))

    with tabs[1]:
        warning = float(ctx.base.app_settings.get("labor_efficiency_warning_pct", 120))
        sc = ctx.technician_scorecard()
        flags = labor_flags(sc[["technician_name", "shift", "logged_hours", "worked_hours"]], warning) if not sc.empty else sc
        st.caption(f"Warning threshold: {warning:g}% (Administration → KPI Targets & Settings)")
        flags_columns = {"technician_name": "Technician", "shift": "Shift", "logged_hours": "Logged Hours",
                 "worked_hours": "Worked Hours", "flag": "Flag"}
        show_table(flags, flags_columns)
        pdf_sections.append(ReportSection("Labor efficiency flags", table=flags, table_columns=flags_columns,
                           note=f"Warning threshold: {warning:g}%"))

    with tabs[2]:
        cfg = ctx.base.ftf_config
        st.caption(f"Repeat window: {cfg.window_days} days · Match: {cfg.asset_rule.replace('_', ' ')} · "
                   f"Technician WOs only: {'yes' if cfg.require_technician else 'no'} · "
                   f"Qualifying types: {', '.join(cfg.qualifying_types) or 'none configured'}")
        label = st.selectbox("Group by", list(FTF_DIMS), key="fiix_ftf_dim")
        dim = FTF_DIMS[label]
        summ = ftf_summary(inputs.ftf, [dim])
        if dim in ("shift", "week_key"):
            summ = summ.drop(columns="ftf_pct").merge(averaged_ftf(inputs.ftf, [dim]), on=dim, how="left")
        if dim == "week_key":
            summ = summ.sort_values("week_key")
            summ["week_key"] = summ["week_key"].map(week_label)
        else:
            summ = summ.sort_values("reactive_wos", ascending=False)
        ftf_columns = {dim: label, "reactive_wos": "Qualifying Reactive WOs",
                       "ftf_success": "Successful First-Time Fixes", "repeat_failures": "Repeat Failures",
                       "ftf_pct": "First-Time Fix %"}
        show_table(summ, ftf_columns)
        pdf_filters["Legacy FTF breakdown"] = label
        pdf_sections.append(ReportSection("Legacy work-order First-Time Fix", table=summ,
                                           table_columns=ftf_columns,
                                           note=f"Match rule: {cfg.asset_rule.replace('_', ' ')}; repeat window: "
                                                f"{cfg.window_days} days. This is an asset-level work-order estimate, "
                                                "not reviewed incident-based FTF."))

    with tabs[3]:
        st.caption("Original work order and the repeat work order responsible for the First-Time Fix failure.")
        repeats = repeat_failure_detail(inputs.ftf, ctx.base.fiix).sort_values("date_completed", ascending=False)
        show_table(repeats, REPEAT_COLUMNS)
        pdf_sections.append(ReportSection("Repeat failure detail", table=repeats, table_columns=REPEAT_COLUMNS,
                                           note=f"Asset matching rule: {ctx.base.ftf_config.asset_rule.replace('_', ' ')}."))

    with tabs[4]:
        st.markdown("**By area — selected period**")
        pm_by_area = pm_area_summary(inputs.pm_status, ["area"])
        show_table(pm_by_area, PM_AREA_COLUMNS)
        st.markdown("**By area and calendar week**")
        weekly = pm_area_summary(inputs.pm_status, ["week_key", "area"]).sort_values(["week_key", "area"])
        weekly["week"] = weekly["week_key"].map(week_label)
        show_table(weekly, PM_AREA_COLUMNS)
        st.markdown("**PMs Closed by technician (closed Preventative (PM) work orders)**")
        done = inputs.pm_done.groupby("technician").size().rename("pm_completed").reset_index()
        done = done.sort_values("pm_completed", ascending=False)
        done_columns = {"technician": "Technician", "pm_completed": "PMs Closed"}
        show_table(done, done_columns)
        pdf_sections.append(ReportSection("PM completion by area", table=pm_by_area,
                                           table_columns=PM_AREA_COLUMNS))
        pdf_sections.append(ReportSection("PM completion by week and area", table=weekly,
                                           table_columns=PM_AREA_COLUMNS))
        pdf_sections.append(ReportSection("PMs closed by technician", table=done, table_columns=done_columns))

    with tabs[5]:
        st.caption("First 1,000 work orders in the current selection (use Export for the full list).")
        work_orders = inputs.fiix.sort_values("date_completed", ascending=False).head(1000)
        work_order_columns = {**WO_COLUMNS, "technician": "Technician", "shift": "Shift",
                              "classification": "Classification"}
        show_table(work_orders, work_order_columns)
        pdf_sections.append(ReportSection("Recent work orders", table=work_orders,
                                           table_columns=work_order_columns,
                                           note="The report includes the same 1,000-row view shown on the dashboard."))

    for area in ctx.pm_areas:
        row = by_area.loc[area] if area in by_area.index else {}
        pdf_metrics.append(ReportMetric(f"PM Completion – {area}",
                                        row.get("pm_completion_pct") if len(row) else None, "%",
                                        target=ctx.targets.get("pm_completion", {}).get("target"),
                                        note=(f"{row.get('pm_closed')} closed of {row.get('pm_open')} open PMs"
                                              if len(row) else "No data")))
    notices = [f"Legacy First-Time Fix uses the {ctx.base.ftf_config.asset_rule.replace('_', ' ')} matching rule; "
               "it is a work-order-level estimate and not reviewer-classified incident FTF."]
    render_pdf_download_button(ctx, "Fiix Performance", pdf_metrics, pdf_sections,
                               extra_filters=pdf_filters, notices=notices,
                               key="pdf_fiix", container=pdf_slot)
