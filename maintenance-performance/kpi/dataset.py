"""Prepares base datasets (technician resolution, shift assignment, WO type config, FTF evaluation) and applies filters."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

import pandas as pd

from kpi.dayforce import combine_hours, imported_weekly_hours, resolve_dayforce_technicians
from kpi.first_time_fix import FTFConfig, evaluate_first_time_fix, reactive_wo_types
from kpi.ignition_metrics import technician_call_peer_share
from kpi.labor_efficiency import qualifying_labor_rows
from kpi.pm_completion import completed_pm_rows, technician_pm_peer_share
from kpi.scorecard import KPIInputs, weekly_trend
from utils.names import build_alias_index, resolve_technician_ids
from utils.dates import week_key

DAYFORCE_ENTRY_COLUMNS = ["employee_id", "employee_name", "employee_key", "work_date", "in_time", "out_time", "job",
                          "pay_code", "pay_category", "normal_hours", "other_hours", "week_key",
                          "report_start", "report_end"]


@dataclass
class BaseData:
    fiix: pd.DataFrame
    calls: pd.DataFrame
    dayforce: pd.DataFrame
    pm_status: pd.DataFrame
    technicians: pd.DataFrame
    aliases: pd.DataFrame
    shifts: pd.DataFrame
    wo_types: pd.DataFrame
    ftf_eval: pd.DataFrame
    ftf_config: FTFConfig
    app_settings: dict
    ambiguous: dict[str, set[str]]
    week_keys: list[int]
    dayforce_manual: pd.DataFrame = field(default_factory=pd.DataFrame)
    dayforce_entries: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def has_data(self) -> bool:
        return bool(self.week_keys)

    def dayforce_imported_week(self, wk: int) -> pd.Series:
        """Imported Dayforce hours worked per technician_id for one week (before manual overrides)."""
        weekly = imported_weekly_hours(self.dayforce_entries[self.dayforce_entries["week_key"] == wk],
                                       list(self.app_settings.get("dayforce_excluded_jobs") or []))
        return weekly.set_index("technician_id")["total_hours_worked"]


@dataclass
class FilterState:
    week_keys: list[int] = field(default_factory=list)
    date_start: datetime | None = None
    date_end: datetime | None = None  # exclusive
    shifts: list[str] = field(default_factory=list)
    technician_ids: list[int] = field(default_factory=list)
    ff_groups: list[str] = field(default_factory=list)
    areas: list[str] = field(default_factory=list)
    wo_types: list[str] = field(default_factory=list)
    label: str = ""
    focus_factories: list[str] = field(default_factory=list)

    @property
    def end_week(self) -> int | None:
        return max(self.week_keys) if self.week_keys else None


def _attach_technician(df: pd.DataFrame, key_col: str, index: dict, technicians: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["technician_id"] = resolve_technician_ids(out[key_col], index)
    _attach_master(out, technicians)
    return out


def _attach_master(out: pd.DataFrame, technicians: pd.DataFrame) -> None:
    master = technicians.set_index("technician_id")
    out["technician"] = out["technician_id"].map(master["technician_name"]).astype("object")
    # Shift always comes from the technician master table, never inferred.
    out["shift"] = out["technician_id"].map(master["shift"]).astype("object")


def build_ftf_config(app_settings: dict, wo_types: pd.DataFrame) -> FTFConfig:
    return FTFConfig(
        qualifying_types=reactive_wo_types(wo_types["wo_type"]),
        window_days=int(app_settings.get("ftf_window_days", 3)),
        asset_rule=app_settings.get("ftf_asset_rule", "asset_number"),
        require_technician=bool(app_settings.get("ftf_require_technician", True)),
        exclude_duplicates=bool(app_settings.get("ftf_exclude_duplicates", True)),
        exclude_admin_closures=bool(app_settings.get("ftf_exclude_admin_closures", True)),
    )


def prepare_base(fiix: pd.DataFrame, calls: pd.DataFrame, dayforce: pd.DataFrame, pm_status: pd.DataFrame,
                 technicians: pd.DataFrame, aliases: pd.DataFrame, shifts: pd.DataFrame,
                 wo_types: pd.DataFrame, app_settings: dict,
                 dayforce_entries: pd.DataFrame | None = None) -> BaseData:
    technicians = technicians.astype({"technician_id": "Int64"})
    fiix_index, fiix_amb = build_alias_index(technicians, aliases, "fiix")
    ign_index, ign_amb = build_alias_index(technicians, aliases, "ignition")

    fiix = _attach_technician(fiix, "completed_by_key", fiix_index, technicians)
    cfg = wo_types.set_index("wo_type")
    for col, default in (("classification", "Other"), ("counts_toward_labor", True),
                         ("ftf_qualifying", False), ("is_pm", False)):
        fiix[col] = fiix["wo_type"].map(cfg[col]).astype("object").fillna(default)
    for col in ("counts_toward_labor", "ftf_qualifying", "is_pm"):
        fiix[col] = fiix[col].astype(bool)
    for col in ("week_key", "year", "calendar_week"):
        fiix[col] = fiix[col].astype("Int64")

    calls = _attach_technician(calls, "tech_key", ign_index, technicians)
    for col in ("week_key", "year", "calendar_week"):
        calls[col] = calls[col].astype("Int64")

    dayforce_manual = dayforce.astype({"technician_id": "Int64", "week_key": "Int64"})
    entries = dayforce_entries if dayforce_entries is not None else pd.DataFrame(columns=DAYFORCE_ENTRY_COLUMNS)
    if "dayforce_id" not in technicians.columns:
        technicians = technicians.assign(dayforce_id=None)
    entries = entries.assign(technician_id=resolve_dayforce_technicians(entries, technicians, aliases))
    imported = imported_weekly_hours(entries, list(app_settings.get("dayforce_excluded_jobs") or []))
    dayforce = combine_hours(dayforce_manual, imported)
    dept_ids = set(entries["technician_id"].dropna().astype(int)) | set(dayforce_manual["technician_id"].dropna().astype(int))
    technicians = technicians.assign(in_department=department_membership(technicians, dept_ids))
    fiix = _flag_non_department(fiix, technicians, bool(dept_ids))
    calls = _flag_non_department(calls, technicians, bool(dept_ids))
    _attach_master(dayforce, technicians)
    _attach_master(dayforce_manual, technicians)
    pm_status = pm_status.astype({"week_key": "Int64"})

    ftf_config = build_ftf_config(app_settings, wo_types)
    ftf_eval = evaluate_first_time_fix(fiix, ftf_config)

    weeks = sorted(set(fiix["week_key"].dropna().astype(int)) | set(calls["week_key"].dropna().astype(int)))
    return BaseData(fiix=fiix, calls=calls, dayforce=dayforce, pm_status=pm_status, technicians=technicians,
                    dayforce_manual=dayforce_manual, dayforce_entries=entries,
                    aliases=aliases, shifts=shifts, wo_types=wo_types, ftf_eval=ftf_eval, ftf_config=ftf_config,
                    app_settings=app_settings, ambiguous={"fiix": fiix_amb, "ignition": ign_amb}, week_keys=weeks)


def _period_mask(df: pd.DataFrame, ts_col: str, state: FilterState) -> pd.Series:
    if state.date_start is not None and state.date_end is not None:
        return ((df[ts_col] >= state.date_start) & (df[ts_col] < state.date_end)).fillna(False).astype(bool)
    return df["week_key"].isin(state.week_keys).fillna(False).astype(bool)


def _people_mask(df: pd.DataFrame, state: FilterState) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    if state.shifts:
        mask &= df["shift"].isin(state.shifts)
    if state.technician_ids:
        mask &= df["technician_id"].isin(state.technician_ids).fillna(False).astype(bool)
    return mask


def _fiix_mask(df: pd.DataFrame, state: FilterState) -> pd.Series:
    mask = _period_mask(df, "date_completed", state) & _people_mask(df, state)
    if state.ff_groups:
        mask &= df["ff_group"].isin(state.ff_groups)
    if state.wo_types:
        mask &= df["wo_type"].isin(state.wo_types)
    return mask


def focus_factories_for_ff_groups(ff_groups: list[str]) -> list[str]:
    mapping = {"FA": ("DAS1",), "SMT": ("SMT",), "POST SMT": ("Post SMT", "Post-SMT"),
               "POST-SMT": ("Post SMT", "Post-SMT")}
    return list(dict.fromkeys(factory for group in ff_groups
                              for factory in mapping.get(group.strip().upper(), (group.strip(),))))


def apply_filters(base: BaseData, state: FilterState) -> KPIInputs:
    s = base.app_settings
    fiix_all = base.fiix[_fiix_mask(base.fiix, state)]
    fiix = fiix_all[~fiix_all["non_department"]]
    ftf = base.ftf_eval[_fiix_mask(base.ftf_eval, state) & ~base.ftf_eval["non_department"]]
    # First-Time Fix is a technician metric: only technicians in scope (active, or inactive up to End Date).
    ftf = ftf[technician_in_scope(ftf, base.technicians)]
    weekly_mask = lambda df: df["week_key"].isin(state.week_keys).fillna(False).astype(bool) & _people_mask(df, state)  # noqa: E731
    calls_mask = _period_mask(base.calls, "call_time", state) & _people_mask(base.calls, state)
    if state.areas:
        calls_mask &= base.calls["ign_area_name"].isin(state.areas)
    focus_factories = state.focus_factories or focus_factories_for_ff_groups(state.ff_groups)
    if focus_factories:
        factories = base.calls["focus_factory"].astype("string").str.strip().str.casefold()
        selected = [factory.strip().casefold() for factory in focus_factories]
        calls_mask &= factories.isin(selected)
    calls_all = base.calls[calls_mask]
    labor_rows = qualifying_labor_rows(fiix, float(s.get("max_time_spent_hours_per_wo") or 0) or None)
    dayforce = base.dayforce[weekly_mask(base.dayforce)]
    pm_done = completed_pm_rows(fiix, bool(s.get("ftf_exclude_duplicates", True)),
                                bool(s.get("ftf_exclude_admin_closures", True)))
    return KPIInputs(
        fiix=fiix,
        labor_rows=labor_rows.assign(le_excluded=labor_excluded(labor_rows, base.technicians)),
        dayforce=dayforce.assign(le_excluded=labor_excluded(dayforce, base.technicians)),
        ftf=ftf,
        pm_done=pm_done[technician_in_scope(pm_done, base.technicians)],
        pm_status=base.pm_status[base.pm_status["week_key"].isin(state.week_keys).fillna(False).astype(bool)],
        calls=calls_all[~calls_all["non_department"]],
        fiix_all=fiix_all,
        calls_all=calls_all,
    )


def department_membership(technicians: pd.DataFrame, dayforce_ids: set[int]) -> pd.Series:
    """Maintenance department = technicians mapped to a Dayforce record.

    Before any Dayforce data exists everyone is treated as in the department.
    """
    if not dayforce_ids:
        return pd.Series(True, index=technicians.index)
    return technicians["technician_id"].isin(dayforce_ids).fillna(False).astype(bool)


def technician_pm_shares(base: BaseData, state: FilterState) -> pd.DataFrame:
    # Person/shift selectors must not shrink the peer denominator to the selected technician.
    inputs = apply_filters(base, replace(state, technician_ids=[], shifts=[]))
    return technician_pm_peer_share(inputs.pm_done, base.technicians)


def technician_call_shares(base: BaseData, state: FilterState) -> pd.DataFrame:
    inputs = apply_filters(base, replace(state, technician_ids=[], shifts=[]))
    return technician_call_peer_share(inputs.calls, base.technicians)


def technician_weekly_trend(base: BaseData, state: FilterState, keys: list[int],
                            technician_id: int, pm_areas: list[str]) -> pd.DataFrame:
    state = replace(state, week_keys=keys, date_start=None, date_end=None, technician_ids=[technician_id])
    trend = weekly_trend(apply_filters(base, state), keys, pm_areas=pm_areas)
    group_inputs = apply_filters(base, replace(state, technician_ids=[], shifts=[]))
    pm_done = group_inputs.pm_done
    peer_rows = []
    call_peer_rows = []
    for key in keys:
        peers = technician_pm_peer_share(pm_done[pm_done["week_key"].eq(key)], base.technicians)
        peer_rows.append(peers.set_index("technician_id").loc[technician_id])
        calls = group_inputs.calls[group_inputs.calls["week_key"].eq(key)]
        call_peers = technician_call_peer_share(calls, base.technicians)
        call_peer_rows.append(call_peers.set_index("technician_id").loc[technician_id])
    peers = pd.DataFrame(peer_rows).reset_index(drop=True)
    call_peers = pd.DataFrame(call_peer_rows).reset_index(drop=True)
    trend = pd.concat([trend, peers, call_peers], axis=1)
    for prefix in ("pm_peer", "call_peer"):
        valid = trend[f"{prefix}_share_pct"].notna()
        own = trend[f"{prefix}_own_completed"].where(valid).rolling(4, min_periods=1).sum()
        group = trend[f"{prefix}_group_completed"].where(valid).rolling(4, min_periods=1).sum()
        trend[f"{prefix}_share_pct_r4"] = own / group.where(group > 0) * 100.0
    return trend


def crew_area_technician_ids(technicians: pd.DataFrame, crew_match: str) -> list[int]:
    """Technicians whose Crew contains ``crew_match`` (e.g. 'SMT' matches 'SMT' and 'Team Lead SMT')."""
    crew = technicians["crew"].fillna("").astype(str).str.casefold()
    return technicians.loc[crew.str.contains(crew_match.casefold(), regex=False), "technician_id"].astype(int).tolist()


def technician_end_weeks(technicians: pd.DataFrame) -> pd.Series:
    """technician_id -> week_key of the End Date (NA when no End Date)."""
    ends = pd.to_datetime(technicians["end_date"], errors="coerce")
    keys = [week_key(*d.isocalendar()[:2]) if pd.notna(d) else pd.NA for d in ends]
    return pd.Series(keys, index=technicians["technician_id"].astype("Int64"), dtype="Int64")


def technician_in_scope(df: pd.DataFrame, technicians: pd.DataFrame) -> pd.Series:
    """Maintenance-department technicians that are active, or inactive up to and including their End Date week."""
    master = technicians.set_index(technicians["technician_id"].astype("Int64"))
    active = df["technician_id"].map(master["active"].astype(bool)).fillna(False).astype(bool)
    end_week = df["technician_id"].map(technician_end_weeks(technicians)).astype("Int64")
    before_end = (df["week_key"].astype("Int64") <= end_week).fillna(False).astype(bool)
    in_dept = pd.Series(True, index=df.index)
    if "in_department" in master:
        in_dept = df["technician_id"].map(master["in_department"].astype(bool)).fillna(False).astype(bool)
    return (active | before_end) & in_dept


def _flag_non_department(df: pd.DataFrame, technicians: pd.DataFrame, enforce: bool) -> pd.DataFrame:
    """Rows not tied to a Dayforce-mapped technician (other departments, unmapped or blank names)."""
    if not enforce:
        return df.assign(non_department=False)
    inside = set(technicians.loc[technicians["in_department"].astype(bool), "technician_id"].astype(int))
    return df.assign(non_department=~df["technician_id"].isin(inside).fillna(False).astype(bool))


def labor_excluded(df: pd.DataFrame, technicians: pd.DataFrame) -> pd.Series:
    """Rows left out of department/shift Labor Efficiency: leads, and technicians out of scope that week."""
    lead_col = technicians.get("is_lead", pd.Series(False, index=technicians.index)).fillna(False).astype(bool)
    leads = set(technicians.loc[lead_col, "technician_id"].astype(int))
    is_lead = df["technician_id"].isin(leads).fillna(False).astype(bool)
    return is_lead | ~technician_in_scope(df, technicians)
