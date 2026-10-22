"""Central production calendar-week utilities.

Convention: ISO-8601 weeks of (timestamp + offset). With the default offset of 1 hour
the production week starts Sunday 23:00 (start of the Monday 3rd shift).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

import pandas as pd

_FILENAME_WEEK_PATTERNS = [
    re.compile(r"(?<!\d)(\d{2})?CW[-_ ]?(\d{1,2})(?!\d)", re.IGNORECASE),
    re.compile(r"(?<![\d.])(\d{1,2})\s*CW", re.IGNORECASE),
]


def week_key(year: int, week: int) -> int:
    return int(year) * 100 + int(week)


def split_week_key(key: int) -> tuple[int, int]:
    year, week = divmod(int(key), 100)
    return year, week


def week_label(key: int) -> str:
    year, week = split_week_key(key)
    return f"{year}-CW{week:02d}"


def week_label_short(key: int) -> str:
    year, week = split_week_key(key)
    return f"CW{week:02d}-{year % 100:02d}"


def production_week(ts: datetime, offset_hours: float = 1) -> tuple[int, int]:
    iso = (ts + timedelta(hours=offset_hours)).isocalendar()
    return iso.year, iso.week


def week_bounds(year: int, week: int, offset_hours: float = 1) -> tuple[datetime, datetime]:
    """Return (start, end) where end is exclusive."""
    monday = date.fromisocalendar(int(year), int(week), 1)
    start = datetime.combine(monday, time()) - timedelta(hours=offset_hours)
    return start, start + timedelta(days=7)


def shift_week(key: int, n: int) -> int:
    year, week = split_week_key(key)
    iso = (date.fromisocalendar(year, week, 1) + timedelta(weeks=n)).isocalendar()
    return week_key(iso.year, iso.week)


def week_keys_ending(key: int, count: int) -> list[int]:
    return [shift_week(key, -i) for i in range(count - 1, -1, -1)]


def week_keys_between(start_key: int, end_key: int) -> list[int]:
    keys, k = [], start_key
    while k <= end_key:
        keys.append(k)
        k = shift_week(k, 1)
    return keys


def week_keys_for_dates(start: date, end: date, offset_hours: float = 1) -> list[int]:
    first = week_key(*production_week(datetime.combine(start, time()), offset_hours))
    last = week_key(*production_week(datetime.combine(end, time(23, 59)), offset_hours))
    return week_keys_between(first, last)


def add_week_columns(df: pd.DataFrame, ts_col: str, offset_hours: float = 1) -> pd.DataFrame:
    """Add year, calendar_week, week_key, week_start and week_end based on ``ts_col``."""
    out = df.copy()
    shifted = pd.to_datetime(out[ts_col], errors="coerce") + pd.Timedelta(hours=offset_hours)
    iso = shifted.dt.isocalendar()
    out["year"] = iso["year"].astype("Int64")
    out["calendar_week"] = iso["week"].astype("Int64")
    out["week_key"] = out["year"] * 100 + out["calendar_week"]
    monday = shifted.dt.normalize() - pd.to_timedelta(shifted.dt.weekday, unit="D")
    out["week_start"] = monday - pd.Timedelta(hours=offset_hours)
    out["week_end"] = out["week_start"] + pd.Timedelta(days=7)
    return out


def parse_filename_week(filename: str) -> tuple[int | None, int | None]:
    """Extract (year, week) from names like FiixCW40.csv, 26CW1.xls, CW-10-Rev.1.csv, 30CW.xls."""
    m = _FILENAME_WEEK_PATTERNS[0].search(filename)
    if m:
        year = 2000 + int(m.group(1)) if m.group(1) else None
        week = int(m.group(2))
        return year, week if 1 <= week <= 53 else None
    m = _FILENAME_WEEK_PATTERNS[1].search(filename)
    if m and 1 <= int(m.group(1)) <= 53:
        return None, int(m.group(1))
    return None, None


def dominant_week(week_keys: pd.Series) -> int | None:
    keys = pd.Series(week_keys).dropna()
    return int(keys.mode().iloc[0]) if not keys.empty else None
