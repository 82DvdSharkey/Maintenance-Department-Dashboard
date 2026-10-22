"""Periodic background scan of the source folders while the dashboard is open."""
from __future__ import annotations

import time
from datetime import datetime

import streamlit as st
from sqlalchemy.engine import Engine

from database.database import get_app_settings, get_data_version
from ingestion.file_manager import REFRESH_LOCK, refresh_weekly_data

CHECK_SECONDS = 60


@st.cache_resource
def _scan_state() -> dict:
    # Shared by all browser sessions so only one scan runs per interval.
    return {"last_scan": 0.0, "last_result": None}


@st.fragment(run_every=CHECK_SECONDS)
def auto_refresh(engine: Engine, settings: dict, loaded_version: int) -> None:
    interval = float(get_app_settings(engine).get("auto_refresh_minutes", 60) or 0)
    state = _scan_state()
    if interval > 0 and time.time() - state["last_scan"] >= interval * 60 and not REFRESH_LOCK.locked():
        state["last_scan"] = time.time()
        outcomes = refresh_weekly_data(engine, settings)
        inserted = sum(o.inserted for o in outcomes)
        failed = sum(o.status == "failed" for o in outcomes)
        state["last_result"] = (f"{datetime.now():%H:%M} · {len(outcomes)} new file(s), {inserted:,} records"
                                + (f", {failed} failed" if failed else ""))

    if get_data_version(engine) != loaded_version:
        st.rerun(scope="app")

    if interval > 0:
        last = state["last_result"] or "pending"
        st.caption(f":material/schedule: Auto-scan every {interval:g} min · Last: {last}")
    else:
        st.caption(":material/schedule: Auto-scan off (Administration → Data Sources)")
