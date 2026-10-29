"""Column standardisation, type coercion and validation helpers shared by loaders."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

DATETIME_FORMATS = (
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %I:%M %p",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%m/%d/%Y",
)


class ValidationError(Exception):
    """Raised when a source file cannot be imported (e.g. missing required columns)."""


@dataclass
class Issue:
    source: str
    issue_type: str
    record_key: str | None
    description: str


@dataclass
class LoadResult:
    frame: pd.DataFrame
    source_rows: int
    issues: list[Issue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def standardize_columns(df: pd.DataFrame, canonical: list[str], aliases: dict[str, str] | None = None) -> pd.DataFrame:
    """Trim column names and map case-insensitive matches/aliases onto canonical names."""
    lookup = {c.casefold(): c for c in canonical}
    for alias, target in (aliases or {}).items():
        lookup[alias.casefold()] = target
    renamed = {}
    for col in df.columns:
        stripped = " ".join(str(col).split())
        renamed[col] = lookup.get(stripped.casefold(), stripped)
    return df.rename(columns=renamed)


def missing_columns(df: pd.DataFrame, required: list[str]) -> list[str]:
    return [c for c in required if c not in df.columns]


def to_float(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("float64")


def to_hours(series: pd.Series) -> pd.Series:
    """Convert Fiix Time Spent to decimal hours. Accepts decimals and h:mm text."""
    s = series.astype("string").str.strip().str.replace(",", "", regex=False)
    hours = pd.to_numeric(s, errors="coerce").astype("float64")
    hhmm = s.str.fullmatch(r"\d{1,3}:\d{2}").fillna(False).astype(bool)
    if hhmm.any():
        parts = s[hhmm].str.split(":", expand=True).astype(float)
        valid_minutes = parts[1] < 60
        valid_index = parts.index[valid_minutes]
        hours.loc[valid_index] = parts.loc[valid_index, 0] + parts.loc[valid_index, 1] / 60.0
    return hours


def parse_datetime(series: pd.Series, formats: tuple[str, ...] = DATETIME_FORMATS) -> pd.Series:
    s = series.astype("string").str.strip()
    result = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    remaining = (s.notna() & (s != "")).fillna(False).astype(bool)
    for fmt in formats:
        if not remaining.any():
            break
        parsed = pd.to_datetime(s[remaining], format=fmt, errors="coerce")
        ok = parsed.notna()
        result.loc[parsed.index[ok]] = parsed[ok]
        remaining.loc[parsed.index[ok]] = False
    if remaining.any():
        parsed = pd.to_datetime(s[remaining], format="mixed", errors="coerce")
        result.loc[parsed.index] = parsed
    return result


def is_blank(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip()
    return (s.isna() | (s == "")).astype(bool)
