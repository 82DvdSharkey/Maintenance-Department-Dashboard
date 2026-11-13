"""Engine/data caching, global sidebar filters and the shared dashboard context."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy.engine import Engine
from streamlit.navigation.page import StreamlitPage

from database import repository as repo
from database.database import DEFAULT_KPI_TARGETS, create_db_engine, get_app_settings, init_db, seed_kpi_targets
from kpi.dataset import (
    BaseData, FilterState, apply_filters, prepare_base, technician_call_shares, technician_pm_shares,
    technician_weekly_trend,
)
from kpi.scorecard import KPIInputs, department_summary, shift_scorecard, technician_scorecard, weekly_trend
from utils.dates import (
    split_week_key,
    week_bounds,
    week_keys_between,
    week_keys_ending,
    week_keys_for_dates,
    week_label,
)


@st.cache_resource
def get_engine(db_path: str) -> Engine:
    engine = create_db_engine(db_path)
    init_db(engine)
    return engine


@st.cache_resource(max_entries=2, show_spinner="Loading maintenance data…")
def load_base(_engine: Engine, db_path: str, version: int) -> BaseData:
    return prepare_base(
        fiix=repo.load_fiix(_engine),
        calls=repo.load_maintenance_calls(_engine),
        dayforce=repo.load_dayforce(_engine),
        pm_status=repo.load_pm_status(_engine),
        technicians=repo.load_technicians(_engine),
        aliases=repo.load_aliases(_engine),
        shifts=repo.load_shifts(_engine),
        wo_types=repo.load_wo_types(_engine),
        app_settings=get_app_settings(_engine),
        dayforce_entries=repo.load_dayforce_entries(_engine),
    )


@dataclass
class DashboardContext:
    engine: Engine
    settings: dict
    base: BaseData
    filters: FilterState
    inputs: KPIInputs
    targets: dict
    version: int
    is_admin: bool = False
    home_page: StreamlitPage | None = None

    @property
    def offset_hours(self) -> float:
        return float(self.settings.get("week_start_offset_hours", 1))

    @property
    def pm_areas(self) -> list[str]:
        return list(self.base.app_settings.get("pm_areas") or ["Final Assembly", "SMT"])

    @property
    def trend_weeks(self) -> int:
        return int(self.base.app_settings.get("trend_weeks", 12))

    def inputs_for(self, week_keys: list[int] | None = None, **overrides) -> KPIInputs:
        state = self.filters
        if week_keys is not None:
            state = replace(state, week_keys=list(week_keys), date_start=None, date_end=None)
        return apply_filters(self.base, replace(state, **overrides))

    def trend(self, weeks: int | None = None, **overrides) -> pd.DataFrame:
        n = weeks or self.trend_weeks
        keys = week_keys_ending(self.filters.end_week, n + 3)
        state = replace(self.filters, **overrides)
        if len(state.technician_ids) == 1:
            return technician_weekly_trend(self.base, state, keys, state.technician_ids[0],
                                           self.pm_areas).iloc[3:].reset_index(drop=True)
        return weekly_trend(self.inputs_for(keys, **overrides), keys,
                            pm_areas=self.pm_areas).iloc[3:].reset_index(drop=True)

    def technician_scorecard(self, inputs: KPIInputs | None = None) -> pd.DataFrame:
        result = technician_scorecard(inputs or self.inputs, self.base.technicians)
        if inputs is None:
            result = result.merge(technician_pm_shares(self.base, self.filters), on="technician_id", how="left")
            result = result.merge(technician_call_shares(self.base, self.filters), on="technician_id", how="left")
        return result

    def shift_scorecard(self, inputs: KPIInputs | None = None, technicians: pd.DataFrame | None = None) -> pd.DataFrame:
        return shift_scorecard(inputs or self.inputs, self.base.technicians if technicians is None else technicians)

    def department_summary(self, inputs: KPIInputs | None = None) -> pd.Series:
        return department_summary(inputs or self.inputs)

    def week_caption(self) -> str:
        end = self.filters.end_week
        start, stop = week_bounds(*split_week_key(end), self.offset_hours)
        return f"{week_label(end)} ({start:%a %b %d %H:%M} – {stop:%a %b %d %H:%M})"

    def tech_name(self, tech_id) -> str:
        m = self.base.technicians.set_index("technician_id")["technician_name"]
        return m.get(tech_id, f"#{tech_id}")


def _ensure(key: str, valid, default) -> None:
    if key not in st.session_state or not valid(st.session_state[key]):
        st.session_state[key] = default


def render_global_filters(base: BaseData, offset_hours: float, is_admin: bool = False) -> FilterState:
    weeks = base.week_keys
    if not weeks:
        return FilterState(label="No data")
    ss = st.session_state
    # When a newer week is imported, move the selection to it so new data is visible immediately.
    if ss.get("f_latest_seen") != weeks[-1]:
        ss["f_latest_seen"] = weeks[-1]
        ss["f_year"], ss["f_week"] = weeks[-1] // 100, weeks[-1]
        ss.pop("f_range", None)
    mode = st.radio("Period", ["Calendar week", "Week range", "Date range"], key="f_mode", horizontal=True)
    date_start = date_end = None
    if mode == "Calendar week":
        years = sorted({k // 100 for k in weeks}, reverse=True)
        _ensure("f_year", lambda v: v in years, weeks[-1] // 100)
        year = st.selectbox("Year", years, key="f_year")
        year_weeks = [k for k in reversed(weeks) if k // 100 == year]
        _ensure("f_week", lambda v: v in year_weeks, year_weeks[0])
        wk = st.selectbox("Calendar week", year_weeks, key="f_week", format_func=week_label)
        keys, label = [wk], week_label(wk)
    elif mode == "Week range":
        _ensure("f_range", lambda v: isinstance(v, tuple) and all(x in weeks for x in v),
                (weeks[max(0, len(weeks) - 12)], weeks[-1]))
        a, b = st.select_slider("Weeks", options=weeks, key="f_range", format_func=week_label)
        keys, label = week_keys_between(a, b), f"{week_label(a)} to {week_label(b)}"
    else:
        last_start, last_end = week_bounds(*split_week_key(weeks[-1]), offset_hours)
        _ensure("f_dates", lambda v: isinstance(v, tuple), (last_start.date() + timedelta(days=1), last_end.date()))
        picked = st.date_input("Date range", key="f_dates", format="YYYY-MM-DD")
        start, end = (picked[0], picked[-1]) if isinstance(picked, tuple) and picked else (picked, picked)
        date_start, date_end = datetime.combine(start, time()), datetime.combine(end + timedelta(days=1), time())
        keys, label = week_keys_for_dates(start, end, offset_hours), f"{start:%Y-%m-%d} to {end:%Y-%m-%d}"

    techs = base.technicians
    shifts = base.shifts.loc[base.shifts["active"].astype(bool), "name"].tolist()
    sel_shifts = st.multiselect("Shift", shifts, key="f_shifts")
    if is_admin:
        tech_names = dict(zip(techs["technician_id"].astype(int), techs["technician_name"]))
        sel_techs = st.multiselect("Technician", list(tech_names), key="f_techs",
                                   format_func=lambda i: tech_names.get(i, i))
    else:
        ss.pop("f_techs", None)
        sel_techs = []
    sel_ff = st.multiselect("FF Group", sorted(base.fiix["ff_group"].dropna().unique()), key="f_ff")
    sel_areas = st.multiselect("IgnAreaName", sorted(base.calls["ign_area_name"].dropna().unique()), key="f_areas")
    sel_types = st.multiselect("WO Type", sorted(base.wo_types["wo_type"]), key="f_wotypes")
    return FilterState(week_keys=keys, date_start=date_start, date_end=date_end, shifts=sel_shifts,
                       technician_ids=sel_techs, ff_groups=sel_ff, areas=sel_areas, wo_types=sel_types, label=label)


def kpi_targets_dict(engine: Engine) -> dict:
    df = repo.load_kpi_targets(engine)
    # Cached engines can survive a code reload introducing new targets.
    if set(DEFAULT_KPI_TARGETS) - set(df["kpi_key"]):
        seed_kpi_targets(engine)
        df = repo.load_kpi_targets(engine)
    return {r["kpi_key"]: r for r in df.to_dict("records")}
