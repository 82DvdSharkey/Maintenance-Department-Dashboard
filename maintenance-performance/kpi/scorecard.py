"""Combines Fiix, Dayforce, PM and Ignition components into technician, shift, department and weekly scorecards."""
from __future__ import annotations

from dataclasses import dataclass
from functools import reduce

import pandas as pd

from kpi.first_time_fix import averaged_ftf, ftf_components
from kpi.ignition_metrics import MINUTE_COLUMNS, call_components, finalize_call_averages
from kpi.labor_efficiency import efficiency_series, labor_components
from kpi.pm_completion import pm_area_column, pm_components, pm_remaining, pm_series
from utils.dates import week_label_short


@dataclass
class KPIInputs:
    fiix: pd.DataFrame
    labor_rows: pd.DataFrame
    dayforce: pd.DataFrame
    ftf: pd.DataFrame
    pm_done: pd.DataFrame
    pm_status: pd.DataFrame
    calls: pd.DataFrame
    # Same period/filters but including rows not mapped to Dayforce (for data-quality checks).
    fiix_all: pd.DataFrame | None = None
    calls_all: pd.DataFrame | None = None


SUM_COLUMNS = (["logged_hours", "le_logged_hours", "reactive_wos", "ftf_success", "repeat_failures",
                "pm_completed", "maintenance_calls"]
               + [f"{m}_sum" for m in MINUTE_COLUMNS] + [f"{m}_n" for m in MINUTE_COLUMNS])
NULLABLE_SUM_COLUMNS = ["worked_hours", "pm_open", "pm_closed"]

_KEY_DTYPES = {"technician_id": "Int64", "week_key": "Int64", "_all": "Int64"}


def _norm_keys(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    out = df.copy()
    for k in keys:
        out[k] = out[k].astype(_KEY_DTYPES.get(k, "object"))
    return out


def _prep(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    data = df.assign(_all=1) if "_all" in keys else df
    return _norm_keys(data, [k for k in keys if k in data.columns])


def weekly_components(inputs: KPIInputs, by: list[str]) -> pd.DataFrame:
    keys = list(dict.fromkeys((list(by) or ["_all"]) + ["week_key"]))
    pm_status = _prep(inputs.pm_status, keys)
    labor_rows, dayforce = inputs.labor_rows, inputs.dayforce
    # Department/shift Labor Efficiency leaves out leads and out-of-scope inactive technicians (le_excluded).
    if "technician_id" not in keys:
        if "le_excluded" in labor_rows:
            labor_rows = labor_rows[~labor_rows["le_excluded"].astype(bool)]
        if "le_excluded" in dayforce:
            dayforce = dayforce[~dayforce["le_excluded"].astype(bool)]
    parts = [
        labor_components(_prep(labor_rows, keys), _prep(dayforce, keys), keys),
        ftf_components(_prep(inputs.ftf, keys), keys),
        _prep(inputs.pm_done, keys).groupby(keys, dropna=True).agg(pm_completed=("wo", "count")).reset_index(),
        call_components(_prep(inputs.calls, keys), keys),
    ]
    # PM Open/Closed is entered per area, so it only exists at area/department/week level.
    if all(k in pm_status.columns for k in keys):
        parts.append(pm_components(pm_status, keys))
    parts = [_norm_keys(p, keys) for p in parts]
    out = reduce(lambda a, b: a.merge(b, on=keys, how="outer"), parts)
    for col in NULLABLE_SUM_COLUMNS:
        out[col] = out[col].astype("float64") if col in out else float("nan")
    for col in SUM_COLUMNS:
        out[col] = out[col].astype("float64").fillna(0.0)
    return out


def rollup(weekly: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    keys = list(by) or ["_all"]
    agg = {c: (c, "sum") for c in SUM_COLUMNS}
    agg.update({c: (c, lambda s: s.sum(min_count=1)) for c in NULLABLE_SUM_COLUMNS})
    out = weekly.groupby(keys, dropna=True).agg(**agg).reset_index()
    return out.drop(columns="_all") if not by else out


def finalize(df: pd.DataFrame) -> pd.DataFrame:
    out = finalize_call_averages(df)
    out["labor_efficiency_pct"] = efficiency_series(out["le_logged_hours"], out["worked_hours"])
    reactive = out["reactive_wos"].astype("float64")
    out["ftf_pct"] = out["ftf_success"] / reactive.where(reactive > 0) * 100.0
    out["pm_completion_pct"] = pm_series(out["pm_closed"], out["pm_open"])
    out["pm_remaining"] = pm_remaining(out["pm_closed"], out["pm_open"])
    return out


def scorecard(inputs: KPIInputs, by: list[str]) -> pd.DataFrame:
    out = finalize(rollup(weekly_components(inputs, by), by))
    ftf = averaged_ftf(inputs.ftf, by)
    if not by:
        out["ftf_pct"] = ftf["ftf_pct"].iloc[0] if not out.empty else None
        return out
    return out.drop(columns="ftf_pct").merge(_norm_keys(ftf, by), on=by, how="left")


def technician_scorecard(inputs: KPIInputs, technicians: pd.DataFrame) -> pd.DataFrame:
    sc = scorecard(inputs, ["technician_id"])
    if "in_department" in technicians:
        technicians = technicians[technicians["in_department"].astype(bool)]
    info = technicians[["technician_id", "technician_name", "shift"]].astype({"technician_id": "Int64"})
    return info.merge(sc, on="technician_id", how="inner").sort_values("technician_name")


def shift_scorecard(inputs: KPIInputs, technicians: pd.DataFrame) -> pd.DataFrame:
    sc = scorecard(inputs, ["shift"])
    active = technicians[technicians["active"].astype(bool) & technicians["shift"].notna()]
    if "in_department" in active:
        active = active[active["in_department"].astype(bool)]
    counts = active.groupby("shift").size().rename("technician_count").reset_index()
    return counts.merge(sc, on="shift", how="right").fillna({"technician_count": 0})


def department_summary(inputs: KPIInputs) -> pd.Series:
    sc = scorecard(inputs, [])
    return sc.iloc[0] if not sc.empty else pd.Series(dtype="float64")


def weekly_trend(inputs: KPIInputs, week_keys: list[int], window: int = 4,
                 pm_areas: list[str] | None = None) -> pd.DataFrame:
    """Weekly KPIs plus pooled rolling ratios (``*_r{window}``) over contiguous weeks."""
    comps = weekly_components(inputs, []).drop(columns="_all")
    comps = comps.groupby("week_key").sum(numeric_only=True)
    comps = comps.reindex(pd.Index(week_keys, name="week_key")).fillna(0.0)
    weekly = finalize(comps)
    rolling = finalize(comps.rolling(window, min_periods=1).sum())
    calls = weekly["maintenance_calls"].where(weekly["maintenance_calls"] > 0)
    weekly["maintenance_calls"] = calls
    ratio_cols = ["labor_efficiency_pct", "ftf_pct", "pm_completion_pct"] + [f"avg_{m}" for m in MINUTE_COLUMNS]
    for col in ratio_cols:
        weekly[f"{col}_r{window}"] = rolling[col]
    weekly[f"maintenance_calls_r{window}"] = calls.rolling(window, min_periods=1).mean()
    weekly[f"pm_completed_r{window}"] = weekly["pm_completed"].rolling(window, min_periods=1).mean()
    weekly = weekly.reset_index()
    weekly["ftf_pct"] = weekly["week_key"].map(averaged_ftf(inputs.ftf, ["week_key"])
                                                .set_index("week_key")["ftf_pct"]).astype("float64")
    weekly[f"ftf_pct_r{window}"] = [
        averaged_ftf(inputs.ftf[inputs.ftf["week_key"].isin(week_keys[max(0, i - window + 1):i + 1])], [])["ftf_pct"].iloc[0]
        for i in range(len(week_keys))
    ]
    for area in dict.fromkeys(list(pm_areas or []) + list(inputs.pm_status["area"].dropna().unique())):
        col = pm_area_column(area)
        sub = inputs.pm_status[inputs.pm_status["area"] == area]
        counts = sub.groupby("week_key")[["pm_open", "pm_closed"]].sum(min_count=1)
        counts = counts.reindex(pd.Index(week_keys, name="week_key"))
        weekly[col] = pm_series(counts["pm_closed"], counts["pm_open"]).to_numpy()
        roll = counts.fillna(0.0).rolling(window, min_periods=1).sum()
        weekly[f"{col}_r{window}"] = pm_series(roll["pm_closed"], roll["pm_open"]).to_numpy()
    weekly["week_label"] = weekly["week_key"].map(week_label_short)
    return weekly
