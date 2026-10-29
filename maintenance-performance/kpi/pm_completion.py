"""Area PM Completion and distinct technician shift/crew completion shares.

PM Completion % = Closed / Open, where Open = PMs opened (due) for the week and Closed = how many of them
were closed. The counts are entered per area (e.g. Final Assembly, SMT) and week; any future source
(e.g. a Fiix scheduled-PM report) only needs to provide week_key, area, pm_open and pm_closed.
Technician peer shares use existing closed Fiix PM rows, not these area assignment counts.
"""
from __future__ import annotations

import re

import pandas as pd

from kpi.first_time_fix import excluded_status_mask
from kpi.peer_shares import technician_peer_share


def pm_area_column(area: str) -> str:
    """Trend column holding one area's PM Completion %, e.g. 'Final Assembly' -> pm_completion_pct__final_assembly."""
    return "pm_completion_pct__" + re.sub(r"[^0-9a-z]+", "_", str(area).casefold()).strip("_")


def pm_completion_pct(closed, pm_open) -> float | None:
    """% of open PMs that were closed. No % without open PMs."""
    if pm_open is None or pd.isna(pm_open) or float(pm_open) <= 0:
        return None
    closed = 0.0 if closed is None or pd.isna(closed) else float(closed)
    return closed / float(pm_open) * 100.0


def pm_series(closed: pd.Series, pm_open: pd.Series) -> pd.Series:
    closed = pd.to_numeric(closed, errors="coerce").astype("float64")
    pm_open = pd.to_numeric(pm_open, errors="coerce").astype("float64")
    return closed.fillna(0.0) / pm_open.where(pm_open > 0) * 100.0


def pm_remaining(closed: pd.Series, pm_open: pd.Series) -> pd.Series:
    return (pd.to_numeric(pm_open, errors="coerce") - pd.to_numeric(closed, errors="coerce").fillna(0)).clip(lower=0)


def pm_components(pm_status: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Open/Closed sums; NaN (not zero) when no entry exists for the group."""
    return (pm_status.groupby(keys, dropna=True)
            .agg(pm_open=("pm_open", lambda s: s.sum(min_count=1)),
                 pm_closed=("pm_closed", lambda s: s.sum(min_count=1)))
            .reset_index())


def pm_area_summary(pm_status: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    data = pm_status.assign(_all=1) if not by else pm_status
    out = pm_components(data, by or ["_all"])
    out["pm_remaining"] = pm_remaining(out["pm_closed"], out["pm_open"])
    out["pm_completion_pct"] = pm_series(out["pm_closed"], out["pm_open"])
    return out.drop(columns="_all") if not by else out


def completed_pm_rows(fiix: pd.DataFrame, exclude_duplicates: bool = True, exclude_admin: bool = True) -> pd.DataFrame:
    """Closed Fiix PM work orders completed by a mapped technician (technician PM KPI)."""
    closed = fiix["status"].astype("string").str.casefold().str.contains("closed", regex=False).fillna(False)
    mask = fiix["is_pm"].fillna(False).astype(bool) & fiix["technician_id"].notna() & closed.astype(bool)
    mask &= ~excluded_status_mask(fiix["status"], exclude_duplicates, exclude_admin)
    return fiix[mask]


def technician_pm_peer_share(pm_done: pd.DataFrame, technicians: pd.DataFrame) -> pd.DataFrame:
    """Own closed PMs / own plus eligible peers' closed PMs, for the supplied period."""
    counts = pm_done.groupby("technician_id")["wo"].count()
    return technician_peer_share(counts, technicians, prefix="pm_peer", activity="closed PMs")
