"""Shared calendar periods and KPI presentation for performance reviews."""
from __future__ import annotations

from datetime import date

import pandas as pd

from kpi.definitions import KPI_DEFS
from kpi.targets import evaluate_status

DAYFORCE_BOUNDARY_NOTE = (
    "Calendar dates are used for Fiix completion dates and Ignition call times. Dayforce hours use "
    "full weekly totals for every production week overlapping each period, including boundary weeks. "
    "Labor efficiency is therefore not an exact calendar-date ratio. Quarter hours can overlap; "
    "YTD is calculated independently, not by adding quarterly hours or averaging percentages."
)


def calendar_review_ranges(year: int) -> list[tuple[str, date, date]]:
    if not 1 <= year < 9999:
        raise ValueError("Select a valid review year.")
    year_end = date(year + 1, 1, 1)
    return [(f"Q{q}", date(year, 3 * q - 2, 1),
             date(year, 3 * q + 1, 1) if q < 4 else year_end) for q in range(1, 5)] + [
                 ("YTD", date(year, 1, 1), year_end)]


def review_metrics(values: dict[str, pd.Series], keys: list[str], targets: dict) -> pd.DataFrame:
    rows = []
    for key in keys:
        definition = KPI_DEFS[key]
        target = targets.get(key, {})
        ytd = values["YTD"].get(definition.column)
        rows.append({
            "KPI": definition.label, "Unit": definition.unit, "Target": target.get("target"),
            **{label: row.get(definition.column) for label, row in values.items()},
            "YTD Status": evaluate_status(ytd, target.get("target"), definition.direction, target.get("watch_band")),
        })
    return pd.DataFrame(rows)


def format_review_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    table = metrics.astype("object").copy()
    for index, row in table.iterrows():
        for column in ("Target", "Q1", "Q2", "Q3", "Q4", "YTD"):
            value = row[column]
            table.loc[index, column] = (
                "N/A" if value is None or pd.isna(value) else
                f"{value:,.0f}" if row["Unit"] == "count" else
                f"{value:.1f}%" if row["Unit"] == "%" else f"{value:.2f} {row['Unit']}")
    return table.drop(columns="Unit")
