"""Labor Efficiency KPI: qualifying Fiix logged hours / Dayforce hours worked."""
from __future__ import annotations

import pandas as pd


def total_hours_worked(regular=0.0, overtime=0.0, other_paid=0.0, excluded=0.0) -> float:
    vals = [0.0 if v is None or pd.isna(v) else float(v) for v in (regular, overtime, other_paid, excluded)]
    return vals[0] + vals[1] + vals[2] - vals[3]


def labor_efficiency_pct(logged_hours, worked_hours) -> float | None:
    """Returns None when worked hours are zero/missing (no percentage is calculated)."""
    if worked_hours is None or pd.isna(worked_hours) or float(worked_hours) <= 0:
        return None
    logged = 0.0 if logged_hours is None or pd.isna(logged_hours) else float(logged_hours)
    return logged / float(worked_hours) * 100.0


def efficiency_series(logged: pd.Series, worked: pd.Series) -> pd.Series:
    worked = pd.to_numeric(worked, errors="coerce").astype("float64")
    logged = pd.to_numeric(logged, errors="coerce").astype("float64").fillna(0.0)
    return logged / worked.where(worked > 0) * 100.0


def qualifying_labor_rows(fiix: pd.DataFrame, max_hours_per_wo: float | None = None) -> pd.DataFrame:
    """Rows whose WO Type counts toward labor, with a mapped technician and plausible numeric Time Spent."""
    mask = fiix["counts_toward_labor"].fillna(False).astype(bool)
    mask &= fiix["technician_id"].notna() & fiix["time_spent_hours"].notna()
    if max_hours_per_wo:
        mask &= fiix["time_spent_hours"] <= max_hours_per_wo
    return fiix[mask]


def labor_components(labor_rows: pd.DataFrame, dayforce: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Weekly-grain components.

    ``le_logged_hours`` pairs each technician's Fiix hours with that technician's own Dayforce hours:
    hours from technicians without Dayforce hours that week are shown as logged but not used in the ratio.
    """
    grain = list(dict.fromkeys(keys + ["technician_id"]))
    logged = labor_rows.groupby(grain, dropna=True).agg(logged_hours=("time_spent_hours", "sum")).reset_index()
    worked = dayforce.groupby(grain, dropna=True).agg(worked_hours=("total_hours_worked", "sum")).reset_index()
    per_tech = logged.merge(worked, on=grain, how="outer")
    per_tech["logged_hours"] = per_tech["logged_hours"].fillna(0.0)
    has_worked = per_tech["worked_hours"].fillna(0) > 0
    per_tech["le_logged_hours"] = per_tech["logged_hours"].where(has_worked, 0.0)
    return (per_tech.groupby(keys, dropna=True)
            .agg(logged_hours=("logged_hours", "sum"),
                 worked_hours=("worked_hours", lambda s: s.sum(min_count=1)),
                 le_logged_hours=("le_logged_hours", "sum"))
            .reset_index())


def labor_breakdown(labor_rows: pd.DataFrame, dim: str) -> pd.DataFrame:
    out = (labor_rows.assign(**{dim: labor_rows[dim].fillna("(blank)")})
           .groupby(dim).agg(logged_hours=("time_spent_hours", "sum"), work_orders=("wo", "count"))
           .reset_index().sort_values("logged_hours", ascending=False))
    total = out["logged_hours"].sum()
    out["share_pct"] = out["logged_hours"] / total * 100.0 if total else None
    return out


def labor_flags(summary: pd.DataFrame, warning_pct: float) -> pd.DataFrame:
    """Flag Fiix-without-Dayforce, Dayforce-without-Fiix, and efficiencies above the warning threshold."""
    flags = []
    for row in summary.itertuples(index=False):
        worked = row.worked_hours if pd.notna(row.worked_hours) else 0.0
        logged = row.logged_hours if pd.notna(row.logged_hours) else 0.0
        if logged > 0 and worked <= 0:
            flags.append((row, "Fiix hours with no Dayforce hours"))
        if worked > 0 and logged <= 0:
            flags.append((row, "Dayforce hours with no Fiix activity"))
        pct = labor_efficiency_pct(logged, worked)
        if pct is not None and pct > warning_pct:
            flags.append((row, f"Labor efficiency above {warning_pct:g}%"))
    return pd.DataFrame([{**r._asdict(), "flag": f} for r, f in flags])
