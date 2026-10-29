"""On-demand team-lead review preview and PDF download."""
from __future__ import annotations

from datetime import datetime
import logging
from zoneinfo import ZoneInfo

import streamlit as st

from reporting.review_common import format_review_metrics
from reporting.shift_review import build_shift_review, build_shift_review_pdf
from ui.components import show_table

logger = logging.getLogger(__name__)


def render_shift_review(ctx, shift: str, area: str) -> None:
    if not ctx.is_admin:
        st.session_state.pop("_shift_review", None)
        st.caption("Quarterly and annual team-lead reviews with individual breakdowns are available to administrators.")
        return
    with st.expander("Quarterly and annual team-lead review"):
        today = datetime.now(ZoneInfo("America/Detroit")).date()
        years = sorted({w // 100 for w in ctx.base.week_keys} | {today.year}, reverse=True)
        default = ctx.filters.end_week // 100
        year = st.selectbox("Review year", years, index=years.index(default), key="shift_review_year")
        st.caption(f"Review team: {shift} | {area} | {year} Q1-Q4 and YTD through {today.isoformat()}. "
                   "Includes team KPIs and individual performance. Other sidebar filters are ignored. "
                   "Past years show full-year results; current quarters are partial.")
        signature = (shift, area, year, today, ctx.version, ctx.offset_hours,
                     repr(ctx.base.app_settings.get("crew_areas")),
                     repr(ctx.base.app_settings.get("reserved_crew_areas")),
                     repr(sorted((key, sorted(value.items())) for key, value in ctx.targets.items())))
        cache_key = "_shift_review"
        cached = st.session_state.get(cache_key)
        if cached and cached[0] != signature:
            st.session_state.pop(cache_key)
            cached = None
        if st.button("Generate quarterly / annual review", key="shift_review_generate"):
            try:
                with st.spinner("Generating team-lead performance review..."):
                    report = build_shift_review(ctx.base, shift, area, year, ctx.targets,
                                                 as_of=today, offset_hours=ctx.offset_hours)
                    pdf = build_shift_review_pdf(report)
            except ValueError as exc:
                st.error(str(exc))
                return
            except (OSError, RuntimeError):
                logger.exception("Team-lead review generation failed")
                st.error("Review generation failed. Check the application log for details.")
                return
            cached = (signature, report, pdf)
            st.session_state[cache_key] = cached
        if cached:
            report, pdf = cached[1:]
            st.download_button("Download Team Lead Review PDF", pdf, file_name=report.filename,
                               mime="application/pdf", key="shift_review_download")
            team_tab, people_tab, notes_tab = st.tabs(["Team review KPIs", "Individual review KPIs",
                                                       "Review dates and notes"])
            with team_tab:
                show_table(format_review_metrics(report.metrics), key="shift_review_team")
            with people_tab:
                show_table(report.roster.drop(columns="technician_id"), key="shift_review_roster")
                for person in report.roster.to_dict("records"):
                    st.markdown(f"**{person['Technician']} - {person['Review role']}**")
                    show_table(format_review_metrics(report.individuals[person["technician_id"]]),
                               key=f"shift_review_person_{person['technician_id']}")
            with notes_tab:
                show_table(report.periods, key="shift_review_periods")
                for notice in report.notices:
                    st.caption(notice)
