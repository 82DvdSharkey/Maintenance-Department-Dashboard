"""Administration controls for Phase 2: preview and download only."""
from __future__ import annotations

import logging

import streamlit as st

from reporting.technician_pdf import build_technician_pdf
from reporting.weekly_technician import build_weekly_technician_report, most_recent_completed_week
from utils.dates import week_label

logger = logging.getLogger(__name__)


def render_technician_reports(ctx) -> None:
    st.subheader("Weekly Technician Reports")
    st.caption("Individual PDF preview and download only. Email, approval and scheduling are not enabled. "
               "Reports use the full technician/week, independently of sidebar filters.")
    if ctx.base.technicians.empty or not ctx.base.week_keys:
        st.info("Import source data and configure technicians before generating reports.")
        return
    completed = most_recent_completed_week(ctx.offset_hours)
    weeks = sorted(set(ctx.base.week_keys) | {completed}, reverse=True)
    week = st.selectbox("Reporting calendar week", weeks, index=weeks.index(completed),
                        format_func=week_label, key="weekly_report_week")
    techs = ctx.base.technicians.sort_values("technician_name")
    tech = st.selectbox("Report technician", techs["technician_id"].astype(int).tolist(),
                        format_func=ctx.tech_name, key="weekly_report_technician")
    allow_partial = st.checkbox("Allow partial-week preview", value=False, key="weekly_report_partial",
                                help="For manual review only. An unfinished reporting week is blocked by default.")
    signature = (week, tech, ctx.version, ctx.offset_hours, allow_partial,
                 repr(sorted((key, sorted(value.items())) for key, value in ctx.targets.items())))
    cache_key = "_weekly_technician_pdf"
    cached = st.session_state.get(cache_key)
    if cached and cached[0] != signature:
        st.session_state.pop(cache_key)
        cached = None
    preview_col, generate_col = st.columns(2)
    preview = preview_col.button("Preview Report", key="weekly_report_preview")
    generate = generate_col.button("Generate PDF", key="weekly_report_generate")
    if preview or generate:
        try:
            with st.spinner("Generating individual technician report..."):
                report = build_weekly_technician_report(ctx.base, tech, week, ctx.targets,
                                                        offset_hours=ctx.offset_hours, allow_partial=allow_partial)
                pdf = build_technician_pdf(report)
        except ValueError as exc:
            st.error(str(exc))
            return
        except (OSError, RuntimeError):
            logger.exception("Technician PDF generation failed")
            st.error("PDF generation failed. Check the application log for details.")
            return
        cached = (signature, report.filename, pdf, preview, report.notices)
        st.session_state[cache_key] = cached
    if cached:
        for notice in cached[4]:
            st.info(notice)
        st.download_button("Download Technician PDF", cached[2], file_name=cached[1], mime="application/pdf",
                           key="weekly_report_download")
        if cached[3]:
            st.pdf(cached[2], height=650, key="weekly_report_pdf_viewer")
