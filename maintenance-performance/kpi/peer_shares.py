"""Technician workload shares within the current shift-and-crew roster."""
from __future__ import annotations

import pandas as pd


def technician_peer_share(counts: pd.Series, technicians: pd.DataFrame, *,
                          prefix: str, activity: str) -> pd.DataFrame:
    """Own count / eligible group's count, including self and zero-activity peers."""
    roster = technicians[["technician_id"]].copy()
    for column in ("shift", "crew"):
        roster[column] = technicians.get(column, pd.Series(None, index=technicians.index, dtype="object"))
        roster[column] = roster[column].astype("string").str.strip().str.casefold().replace("", pd.NA)
    active = technicians["active"].fillna(False).astype(bool)
    lead = technicians.get("is_lead", pd.Series(False, index=technicians.index)).fillna(False).astype(bool)
    department = technicians.get("in_department", pd.Series(True, index=technicians.index)).fillna(False).astype(bool)
    designated = roster[["shift", "crew"]].notna().all(axis=1)
    eligible = active & ~lead & department & designated
    own, group = f"{prefix}_own_completed", f"{prefix}_group_completed"
    peers, reason = f"{prefix}_count", f"{prefix}_reason"
    roster[own] = roster["technician_id"].map(counts).fillna(0).astype(float)
    roster[peers] = 0
    roster[group] = float("nan")
    groups = roster.loc[eligible].groupby(["shift", "crew"], dropna=True)
    roster.loc[eligible, peers] = groups["technician_id"].transform("size") - 1
    roster.loc[eligible, group] = groups[own].transform("sum")
    roster[reason] = ""
    roster.loc[~designated, reason] = "N/A: shift and crew must both be assigned."
    roster.loc[~active | lead | ~department, reason] = (
        "N/A: peer comparison requires an active, non-lead maintenance technician.")
    roster.loc[eligible & roster[peers].eq(0), reason] = (
        "N/A: no other active, non-lead technicians match this shift and crew.")
    roster.loc[eligible & roster[peers].gt(0) & roster[group].eq(0), reason] = (
        f"N/A: the shift-and-crew group has no {activity} in this period.")
    valid = roster[reason].eq("")
    roster[f"{prefix}_share_pct"] = roster[own] / roster[group].where(valid) * 100.0
    return roster.drop(columns=["shift", "crew"])
