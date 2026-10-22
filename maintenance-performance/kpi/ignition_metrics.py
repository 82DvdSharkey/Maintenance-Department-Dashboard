"""Ignition maintenance-call KPIs. Durations are always computed from raw seconds, never from text fields."""
from __future__ import annotations

import pandas as pd

DURATION_COLUMNS = {
    "raw_call_duration": "call_duration_min",
    "raw_ack_duration": "ack_duration_min",
    "raw_response_duration": "response_duration_min",
    "raw_repair_duration": "repair_duration_min",
}
MINUTE_COLUMNS = list(DURATION_COLUMNS.values())
AVG_COLUMNS = {m: f"avg_{m}" for m in MINUTE_COLUMNS}


def seconds_to_minutes(seconds):
    """Raw seconds -> decimal minutes. Nulls and negative (invalid) values stay null, never zero."""
    if isinstance(seconds, pd.Series):
        s = pd.to_numeric(seconds, errors="coerce").astype("float64")
        return s.where(s >= 0) / 60.0
    if seconds is None or pd.isna(seconds) or float(seconds) < 0:
        return None
    return float(seconds) / 60.0


def is_maintenance_call(call_type: pd.Series) -> pd.Series:
    return call_type.astype("string").str.strip().str.lower().eq("maintenance").fillna(False).astype(bool)


def filter_maintenance_calls(calls: pd.DataFrame) -> pd.DataFrame:
    return calls[is_maintenance_call(calls["call_type"])]


def call_components(calls: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Additive components (count, per-metric sum and non-null n) so averages can be pooled correctly."""
    aggs = {"maintenance_calls": ("id", "count")}
    for m in MINUTE_COLUMNS:
        aggs[f"{m}_sum"] = (m, "sum")
        aggs[f"{m}_n"] = (m, "count")
    return calls.groupby(keys, dropna=True).agg(**aggs).reset_index()


def finalize_call_averages(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for m in MINUTE_COLUMNS:
        n = out[f"{m}_n"].astype("float64")
        out[AVG_COLUMNS[m]] = out[f"{m}_sum"].astype("float64") / n.where(n > 0)
    return out


def summarize_calls(calls: pd.DataFrame, by: list[str] | None = None) -> pd.DataFrame:
    """Call count and average durations computed directly from individual call records (call-weighted)."""
    by = list(by or [])
    data = calls.assign(_all=1) if not by else calls
    keys = by or ["_all"]
    out = finalize_call_averages(call_components(data, keys))
    if not by:
        out = out.drop(columns="_all")
    return out[by + ["maintenance_calls"] + list(AVG_COLUMNS.values())]
