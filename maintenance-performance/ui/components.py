"""Reusable Streamlit UI components: KPI cards, status badges, tables and Plotly charts."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from kpi.definitions import KPIDef
from kpi.targets import evaluate_status

PRIMARY = "#c41230"
ROLLING = "#737982"
TARGET = "#c0392b"

STATUS_STYLE = {
    "On Target": ("green", ":material/check_circle:"),
    "Watch": ("orange", ":material/warning:"),
    "Below Target": ("red", ":material/arrow_downward:"),
    "Above Target": ("red", ":material/arrow_upward:"),
    "No Target": ("gray", ":material/remove:"),
    "No Data": ("gray", ":material/help:"),
}

TECH_SCORECARD_COLUMNS = {
    "technician_name": "Technician", "shift": "Shift",
    "worked_hours": "Dayforce Hours Worked", "logged_hours": "Fiix Logged Hours",
    "labor_efficiency_pct": "Labor Efficiency %", "reactive_wos": "Reactive WO Count",
    "ftf_pct": "First-Time Fix %", "pm_completed": "PMs Closed",
    "pm_peer_share_pct": "PM Completion Share (Shift/Crew) %",
    "pm_peer_reason": "PM Peer Comparison Note",
    "maintenance_calls": "Ignition Maintenance Calls",
    "avg_call_duration_min": "Avg Call Duration Min", "avg_ack_duration_min": "Avg Ack Duration Min",
    "avg_response_duration_min": "Avg Response Duration Min", "avg_repair_duration_min": "Avg Repair Duration Min",
}
SHIFT_SCORECARD_COLUMNS = {
    "shift": "Shift", "technician_count": "Technician Count", "worked_hours": "Dayforce Hours",
    "logged_hours": "Fiix Logged Hours", "labor_efficiency_pct": "Labor Efficiency %",
    "reactive_wos": "Reactive WO Count", "ftf_pct": "First-Time Fix %", "pm_completed": "PMs Closed",
    "maintenance_calls": "Maintenance Call Count", "avg_call_duration_min": "Avg Call Duration",
    "avg_ack_duration_min": "Avg Ack Duration", "avg_response_duration_min": "Avg Response Duration",
    "avg_repair_duration_min": "Avg Repair Duration",
}
CALL_SUMMARY_COLUMNS = {
    "maintenance_calls": "Maintenance Calls", "avg_call_duration_min": "Avg Call Duration Min",
    "avg_ack_duration_min": "Avg Ack Duration Min", "avg_response_duration_min": "Avg Response Duration Min",
    "avg_repair_duration_min": "Avg Repair Duration Min",
}
CALL_DETAIL_COLUMNS = {
    "id": "ID", "call_time": "CallTime", "ign_area_name": "IgnAreaName", "ign_machine_name": "IgnMachineName",
    "machine_name": "MachineName", "tech_name": "TechName", "raw_call_duration": "RawCallDuration",
    "raw_ack_duration": "RawAckDuration", "raw_response_duration": "RawResponseDuration",
    "raw_repair_duration": "RawRepairDuration", "call_duration_min": "Call Min", "ack_duration_min": "Ack Min",
    "response_duration_min": "Response Min", "repair_duration_min": "Repair Min",
}
WO_COLUMNS = {
    "wo": "WO", "date_completed": "Date Completed", "completed_by": "Completed By", "asset": "Asset",
    "parent_asset": "Parent Asset", "wo_type": "WO Type", "task_description": "Task Description",
    "time_spent_hours": "Time Spent (h)", "status": "Status",
}
PM_AREA_COLUMNS = {
    "week": "Calendar Week", "area": "Area", "pm_open": "PMs Open", "pm_closed": "PMs Closed",
    "pm_remaining": "PMs Remaining", "pm_completion_pct": "PM Completion %",
}
REPEAT_COLUMNS = {
    "wo": "Original WO", "asset": "Asset", "completed_by": "Original Tech", "date_completed": "Original Completed",
    "task_description": "Original Task", "repeat_wo": "Repeat WO", "repeat_date_created": "Repeat Created",
    "repeat_completed_by": "Repeat Tech", "repeat_wo_type": "Repeat WO Type",
    "repeat_task_description": "Repeat Task", "days_to_repeat": "Days to Repeat",
}


def fmt(value, unit: str = "") -> str:
    if value is None or pd.isna(value):
        return "—"
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "min":
        return f"{value:.2f} min"
    if unit == "count":
        return f"{value:,.0f}"
    if unit == "h":
        return f"{value:,.1f} h"
    return f"{value:,.2f}"


def fmt_delta(value, unit: str = "") -> str | None:
    if value is None or pd.isna(value):
        return None
    sign = "+" if value >= 0 else "-"
    return f"{sign}{fmt(abs(value), unit)}"


def status_badge(status: str) -> None:
    color, icon = STATUS_STYLE.get(status, ("gray", ":material/help:"))
    st.badge(status, icon=icon, color=color)


def kpi_card(defn: KPIDef, current, previous=None, rolling=None, target_row: dict | None = None,
             compare: bool = True) -> None:
    target = (target_row or {}).get("target")
    band = (target_row or {}).get("watch_band")
    delta_color = {"higher": "normal", "lower": "inverse"}.get(defn.direction or "", "off")
    with st.container(border=True):
        delta = None if not compare or previous is None or pd.isna(previous) or pd.isna(current) else current - previous
        st.metric(defn.label, fmt(current, defn.unit), delta=fmt_delta(delta, defn.unit),
                  delta_color=delta_color, help=defn.help)
        if compare:
            d4 = None if rolling is None or pd.isna(rolling) or pd.isna(current) else current - rolling
            st.caption(f"Prev wk: {fmt(previous, defn.unit)} · 4-wk avg: {fmt(rolling, defn.unit)} "
                       f"(Δ {fmt_delta(d4, defn.unit) or '—'}) · Target: {fmt(target, defn.unit)}")
        else:
            st.caption(f"Target: {fmt(target, defn.unit)}")
        if defn.direction:
            status_badge(evaluate_status(current, target, defn.direction, band))


def kpi_card_grid(defs: list[KPIDef], current: pd.Series, previous: pd.Series | None, targets: dict,
                  per_row: int = 4) -> None:
    for i in range(0, len(defs), per_row):
        cols = st.columns(per_row)
        for col, d in zip(cols, defs[i:i + per_row]):
            with col:
                prev = previous.get(d.column) if previous is not None else None
                kpi_card(d, current.get(d.column), prev, current.get(f"{d.column}_r4"), targets.get(d.key))


def show_table(df: pd.DataFrame, columns: dict[str, str] | None = None, height: int | None = None,
               key: str | None = None) -> None:
    if df is None or df.empty:
        st.info("No records for the current selection.")
        return
    view = df[[c for c in columns if c in df.columns]].rename(columns=columns) if columns else df
    config = {}
    for col in view.columns:
        dtype = view[col].dtype
        if pd.api.types.is_float_dtype(dtype):
            config[col] = st.column_config.NumberColumn(col, format="%.2f")
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            config[col] = st.column_config.DatetimeColumn(col, format="YYYY-MM-DD HH:mm")
    kwargs = {"height": height} if height else {}
    st.dataframe(view, hide_index=True, column_config=config, key=key, **kwargs)


def _layout(fig: go.Figure, title: str, y_title: str = "", height: int = 320) -> go.Figure:
    fig.update_layout(title=dict(text=title, font=dict(size=15)), height=height, margin=dict(l=10, r=10, t=45, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=-0.35), yaxis_title=y_title,
                      plot_bgcolor="white", hovermode="x unified")
    fig.update_yaxes(gridcolor="#e5e8eb")
    # Week labels like CW40-26 would otherwise be auto-detected as dates.
    fig.update_xaxes(type="category")
    return fig


def trend_chart(trend: pd.DataFrame, defn: KPIDef, target=None, key: str | None = None,
                title: str | None = None) -> go.Figure:
    col = defn.column
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=trend["week_label"], y=trend[col], mode="lines+markers", name="Weekly actual",
                             line=dict(color=PRIMARY, width=2)))
    if f"{col}_r4" in trend:
        fig.add_trace(go.Scatter(x=trend["week_label"], y=trend[f"{col}_r4"], mode="lines", name="4-week rolling avg",
                                 line=dict(color=ROLLING, dash="dash")))
    if target is not None and pd.notna(target):
        fig.add_trace(go.Scatter(x=trend["week_label"], y=[target] * len(trend), mode="lines", name="Target",
                                 line=dict(color=TARGET, dash="dot")))
    st.plotly_chart(_layout(fig, title or defn.label, defn.unit), key=key)
    return fig


def bar_chart(df: pd.DataFrame, x: str, y: str, title: str, y_title: str = "", target=None,
              key: str | None = None, sort_desc: bool = True) -> go.Figure | None:
    if df is None or df.empty or df[y].dropna().empty:
        st.info(f"No data for {title}.")
        return None
    data = df.dropna(subset=[y])
    data = data.sort_values(y, ascending=not sort_desc) if sort_desc is not None else data
    fig = go.Figure(go.Bar(x=data[x].astype(str), y=data[y], marker_color=PRIMARY,
                           text=data[y].round(2), textposition="outside"))
    if target is not None and pd.notna(target):
        fig.add_hline(y=target, line_dash="dot", line_color=TARGET, annotation_text="Target")
    st.plotly_chart(_layout(fig, title, y_title), key=key)
    return fig


def pareto_chart(df: pd.DataFrame, category: str, value: str, title: str, show_cumulative: bool = True,
                 key: str | None = None) -> go.Figure | None:
    if df is None or df.empty:
        st.info(f"No data for {title}.")
        return None
    data = df.sort_values(value, ascending=False)
    fig = go.Figure(go.Bar(x=data[category].astype(str), y=data[value], name=value, marker_color=PRIMARY))
    if show_cumulative:
        cum = data[value].cumsum() / data[value].sum() * 100.0
        fig.add_trace(go.Scatter(x=data[category].astype(str), y=cum, name="Cumulative %", yaxis="y2",
                                 mode="lines+markers", line=dict(color=TARGET)))
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", range=[0, 105], ticksuffix="%"))
    st.plotly_chart(_layout(fig, title, "Calls", height=380), key=key)
    return fig


def no_data_message() -> None:
    st.info("No data has been imported yet. Use **Refresh Weekly Data** in the sidebar to import Fiix and "
            "Ignition reports from the configured source folders.", icon=":material/info:")
