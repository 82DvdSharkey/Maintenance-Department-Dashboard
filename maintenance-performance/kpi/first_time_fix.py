"""First-Time Fix KPI: technician reactive WOs with no repeat reactive WO on the same asset number within a window."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from utils.names import clean_text

REACTIVE_KEYWORD = "reactive"
# Fiix TL holds the asset number; location codes (PLANT4, PROD-...) and labels like Ignition_HotlineCall are not assets.
_ASSET_NUMBER = re.compile(r"^\d")


def reactive_wo_types(wo_types) -> list[str]:
    return [t for t in wo_types if isinstance(t, str) and REACTIVE_KEYWORD in t.casefold()]


@dataclass
class FTFConfig:
    qualifying_types: list[str] = field(default_factory=list)
    window_days: int = 3
    asset_rule: str = "asset_number"  # asset_number | asset | parent_asset | asset_then_parent
    exclude_duplicates: bool = True
    exclude_admin_closures: bool = True
    require_technician: bool = True


def excluded_status_mask(status: pd.Series, exclude_duplicates: bool, exclude_admin: bool) -> pd.Series:
    s = status.astype("string").str.casefold().fillna("")
    mask = pd.Series(False, index=status.index)
    if exclude_duplicates:
        mask |= s.str.contains("duplicate", regex=False)
    if exclude_admin:
        mask |= s.str.contains("admin", regex=False)
    return mask.astype(bool)


def asset_match_key(df: pd.DataFrame, asset_rule: str) -> pd.Series:
    if asset_rule == "asset_number":
        tl = df["tl"].map(clean_text)
        return tl.where(tl.map(lambda v: isinstance(v, str) and bool(_ASSET_NUMBER.match(v))))
    if asset_rule == "parent_asset":
        key = df["parent_asset"].map(clean_text)
    else:
        key = df["asset"].map(clean_text)
    if asset_rule == "asset_then_parent":
        key = key.fillna(df["parent_asset"].map(clean_text))
    return key.map(lambda v: v.casefold() if isinstance(v, str) else None)


def ftf_pct(successes, total) -> float | None:
    if total is None or pd.isna(total) or total <= 0:
        return None
    return float(successes) / float(total) * 100.0


def evaluate_first_time_fix(fiix: pd.DataFrame, config: FTFConfig) -> pd.DataFrame:
    """One row per qualifying reactive WO with its repeat WO (if any).

    A repeat is the next qualifying WO with the exact same match key (asset number by default) whose Date
    Created falls after the original's Date Completed and within ``window_days``. With
    ``require_technician`` only WOs with a Completed By technician qualify, as originals or repeats.
    """
    qualifying = set(config.qualifying_types)
    mask = fiix["wo_type"].isin(qualifying) & fiix["date_completed"].notna()
    mask &= ~excluded_status_mask(fiix["status"], config.exclude_duplicates, config.exclude_admin_closures)
    if config.require_technician:
        mask &= fiix["completed_by"].map(clean_text).notna()
    q = fiix[mask].copy()
    q["match_key"] = asset_match_key(q, config.asset_rule)
    q = q[q["match_key"].notna()].copy()
    q["event_time"] = q["date_created"].fillna(q["date_completed"])

    window = np.timedelta64(int(config.window_days) * 24 * 3600, "s")
    repeat_wo_values = np.full(len(q), None, dtype=object)
    repeat_time_values = np.full(len(q), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    row_positions = np.arange(len(q), dtype=np.intp)
    match_keys = q["match_key"].to_numpy()
    event_times = q["event_time"].to_numpy(dtype="datetime64[ns]")
    completion_times = q["date_completed"].to_numpy(dtype="datetime64[ns]")
    work_orders = q["wo"].to_numpy()
    sort_order = np.lexsort((row_positions, event_times, match_keys))
    sorted_keys = match_keys[sort_order]
    boundaries = np.flatnonzero(sorted_keys[1:] != sorted_keys[:-1]) + 1
    group_starts = np.r_[0, boundaries]
    group_ends = np.r_[boundaries, len(q)]

    for start, end in zip(group_starts, group_ends):
        positions = sort_order[start:end]
        times = event_times[positions]
        completions = completion_times[positions]
        wos = work_orders[positions]
        next_positions = np.searchsorted(times, completions, side="right")
        has_next = next_positions < len(wos)
        while has_next.any():
            candidate_positions = np.minimum(next_positions, len(wos) - 1)
            skip_same_wo = has_next & (wos[candidate_positions] == wos)
            if not skip_same_wo.any():
                break
            next_positions[skip_same_wo] += 1
            has_next = next_positions < len(wos)

        candidates = np.flatnonzero(has_next)
        if len(candidates):
            candidate_positions = next_positions[candidates]
            within_window = times[candidate_positions] - completions[candidates] <= window
            matched_rows = candidates[within_window]
            output_positions = positions[matched_rows]
            repeat_wo_values[output_positions] = wos[next_positions[matched_rows]]
            repeat_time_values[output_positions] = times[next_positions[matched_rows]]

    q["repeat_wo"] = pd.Series(repeat_wo_values, index=q.index, dtype="object")
    q["repeat_date_created"] = pd.Series(repeat_time_values, index=q.index)
    q["days_to_repeat"] = (q["repeat_date_created"] - q["date_completed"]).dt.total_seconds() / 86400.0
    q["repeat_failure"] = q["repeat_wo"].notna()
    q["ftf_success"] = ~q["repeat_failure"]
    return q


def ftf_components(evaluated: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    return (evaluated.groupby(keys, dropna=True)
            .agg(reactive_wos=("wo", "count"), ftf_success=("ftf_success", "sum"),
                 repeat_failures=("repeat_failure", "sum"))
            .reset_index())


def averaged_ftf(evaluated: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """FTF % as a technician metric rolled up by simple averages.

    Technician % = own successes / own qualifying WOs. Shift % = mean of its technicians' %.
    Department (no shift/technician in ``by``) = mean of the shift %. Technicians without a shift
    only appear at technician level.
    """
    keys = [k for k in by if k != "_all"]
    data = evaluated[evaluated["technician_id"].notna()]
    tech_keys = list(dict.fromkeys(keys + ["technician_id", "shift"]))
    tech = (data.groupby(tech_keys, dropna=False)
            .agg(n=("wo", "count"), s=("ftf_success", "sum")).reset_index())
    tech["ftf_pct"] = tech["s"] / tech["n"] * 100.0
    if "technician_id" in keys:
        return tech[keys + ["ftf_pct"]]
    tech = tech[tech["shift"].notna()]
    shift_keys = list(dict.fromkeys(keys + ["shift"]))
    shift = tech.groupby(shift_keys)["ftf_pct"].mean().reset_index()
    if "shift" in keys:
        return shift[keys + ["ftf_pct"]]
    if not keys:
        return pd.DataFrame({"ftf_pct": [shift["ftf_pct"].mean() if not shift.empty else float("nan")]})
    return shift.groupby(keys)["ftf_pct"].mean().reset_index()


def ftf_summary(evaluated: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    data = evaluated.assign(_all=1) if not by else evaluated
    out = ftf_components(data, by or ["_all"])
    out["ftf_pct"] = [ftf_pct(s, t) for s, t in zip(out["ftf_success"], out["reactive_wos"])]
    return out.drop(columns="_all") if not by else out


def repeat_failure_detail(evaluated: pd.DataFrame, fiix: pd.DataFrame) -> pd.DataFrame:
    """Original WO alongside the repeat WO responsible for the FTF failure."""
    fails = evaluated[evaluated["repeat_failure"]]
    repeat = fiix[["wo", "completed_by", "date_completed", "wo_type", "task_description"]].rename(columns={
        "wo": "repeat_wo", "completed_by": "repeat_completed_by", "date_completed": "repeat_date_completed",
        "wo_type": "repeat_wo_type", "task_description": "repeat_task_description"})
    return fails.merge(repeat, on="repeat_wo", how="left")
