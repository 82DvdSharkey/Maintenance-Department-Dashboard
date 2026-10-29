"""Engine creation, schema initialisation, default seeding and settings access."""
from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import URL, create_engine, event, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from database.models import AppSetting, Base, KPITarget, Shift, WOTypeConfig
from utils.dates import production_week, week_key

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "fiix_source_dir": r"G:\Maintenance\$_Metrics\Fiix\Weekly Reports",
    "ignition_source_dir": r"G:\Maintenance\$_Metrics\IGN Call Data",
    "dayforce_source_dir": r"G:\Maintenance\$_Metrics\Dayforce Data",
    "fiix_file_patterns": ["*/*.csv"],
    "ignition_file_patterns": ["*/*.xlsx", "*/*.xls"],
    "dayforce_file_patterns": ["*.xlsx", "*/*.xlsx"],
    "ignition_sheet_name": "Dataset 1",
    "database_path": "data/maintenance_metrics.db",
    "week_start_offset_hours": 1,
    "department_name": "Maintenance Department",
    "bootstrap_admin_email": "David.Sharkey@Magna.Com",
}

DEFAULT_SHIFTS = ["1st Shift", "2nd Shift", "3rd Shift", "Weekend"]

WO_CLASSIFICATIONS = [
    "Productive Maintenance",
    "Reactive Maintenance",
    "Preventive Maintenance",
    "Planned Maintenance",
    "Projects",
    "Training",
    "Administration",
    "Other",
]

REACTIVE_TYPES = [
    "Reactive",
    "Reactive-Breakdown",
    "Reactive-O.E.",
    "Reactive-Process Checks",
    "Reactive-ENG",
    "Reactive-Controls",
]

# wo_type -> (classification, counts_toward_labor, ftf_qualifying, is_pm)
DEFAULT_WO_TYPES: dict[str, tuple[str, bool, bool, bool]] = {
    **{t: ("Reactive Maintenance", True, True, False) for t in REACTIVE_TYPES},
    "Corrective": ("Reactive Maintenance", True, False, False),
    "Preventative (PM)": ("Preventive Maintenance", True, False, True),
    "Autonomous Maintenance (A.M.)": ("Productive Maintenance", True, False, False),
    "Changeover": ("Planned Maintenance", True, False, False),
    "Start-Up/Shutdown": ("Planned Maintenance", True, False, False),
    "Consumables": ("Planned Maintenance", True, False, False),
    "Rebuild": ("Planned Maintenance", True, False, False),
    "Proactive": ("Planned Maintenance", True, False, False),
    "Engineering": ("Projects", True, False, False),
    "MES Project": ("Projects", True, False, False),
    "Project": ("Projects", True, False, False),
    "Controls Project": ("Projects", True, False, False),
    "Tool Room": ("Projects", True, False, False),
    "Training": ("Training", True, False, False),
    "Administration": ("Administration", True, False, False),
}

# key -> (label, target, watch_band, direction, unit); placeholders only.
DEFAULT_KPI_TARGETS: dict[str, tuple[str, float, float, str, str]] = {
    "labor_efficiency": ("Labor Efficiency %", 80.0, 5.0, "higher", "%"),
    "first_time_fix": ("First-Time Fix %", 90.0, 5.0, "higher", "%"),
    "pm_completion": ("PM Completion %", 100.0, 5.0, "higher", "%"),
    "pm_peer_share": ("Technician PM Completion Share %", 33.0, 5.0, "higher", "%"),
    "call_peer_share": ("Technician Maintenance Call Share %", 33.0, 5.0, "higher", "%"),
    "avg_ack_min": ("Avg Acknowledge Duration (min)", 5.0, 1.0, "lower", "min"),
    "avg_response_min": ("Avg Response Duration (min)", 5.0, 1.0, "lower", "min"),
    "avg_repair_min": ("Avg Repair Duration (min)", 30.0, 5.0, "lower", "min"),
    "avg_call_min": ("Avg Call Duration (min)", 45.0, 5.0, "lower", "min"),
}

DEFAULT_APP_SETTINGS: dict[str, Any] = {
    "data_version": 0,
    "ftf_window_days": 3,
    "ftf_asset_rule": "asset_number",
    "ftf_require_technician": True,
    "ftf_exclude_duplicates": True,
    "ftf_exclude_admin_closures": True,
    "labor_efficiency_warning_pct": 120.0,
    # Single-WO Time Spent above this is treated as a data error (e.g. elapsed time) and left out of labor hours.
    "max_time_spent_hours_per_wo": 24.0,
    "trend_weeks": 12,
    "auto_refresh_minutes": 60,
    "pm_areas": ["Final Assembly", "SMT"],
    # Shift Performance area filters: label -> text matched (case-insensitive) in the technician Crew column.
    "crew_areas": {"Final Assembly": "Final Assembly", "SMT": "SMT", "Tester Tech": "Test"},
    # Areas shown on the Shift page but not yet reported (future use).
    "reserved_crew_areas": ["Tester Tech"],
    "ftf_verification_days": 7,
    "ftf_low_volume_threshold": 10,
    # Dayforce job titles containing any of these are not counted as maintenance hours worked.
    "dayforce_excluded_jobs": ["Tool & Die"],
    "pm_denominator_source": "manual",
}


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    if path.exists():
        settings.update(json.loads(path.read_text(encoding="utf-8")))
    return settings


def save_settings(settings: dict[str, Any], path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def resolve_db_path(settings: dict[str, Any]) -> Path:
    p = Path(settings["database_path"])
    return p if p.is_absolute() else PROJECT_ROOT / p


def create_db_engine(db_path: Path | str) -> Engine:
    db_path = Path(db_path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite", database=str(db_path)))

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    return engine


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    _add_missing_indexes(engine)
    seed_defaults(engine)
    _migrate_dayforce_clock_in_weeks(engine)


def _migrate_dayforce_clock_in_weeks(engine: Engine) -> None:
    """Reassign existing Dayforce entries to the Sunday 9:30 PM clock-in week boundary once."""
    marker = "migration_dayforce_sunday_2130_clock_in_v1"
    with engine.begin() as conn:
        if conn.execute(select(AppSetting.key).where(AppSetting.key == marker)).first():
            return
        rows = conn.execute(text("SELECT entry_id, work_date, in_time, week_key FROM dayforce_entries")).mappings()
        updates = []
        for row in rows:
            in_time = datetime.fromisoformat(row["in_time"]) if row["in_time"] else None
            work_date = date.fromisoformat(row["work_date"])
            anchor = in_time or datetime.combine(work_date, time(12))
            year, week = production_week(anchor, 2.5)
            key = week_key(year, week)
            if row["week_key"] != key:
                updates.append({"entry_id": row["entry_id"], "week_key": key, "year": year, "week": week})
        for update in updates:
            conn.execute(text("UPDATE dayforce_entries SET week_key=:week_key, year=:year, "
                              "calendar_week=:week WHERE entry_id=:entry_id"), update)
        if updates:
            version_row = conn.execute(select(AppSetting.value).where(AppSetting.key == "data_version")).first()
            version = json.loads(version_row[0]) if version_row and version_row[0] else 0
            conn.execute(sqlite_insert(AppSetting).values(key="data_version", value=json.dumps(version + 1))
                         .on_conflict_do_update(index_elements=["key"], set_={"value": json.dumps(version + 1)}))
        conn.execute(sqlite_insert(AppSetting).values(key=marker, value=json.dumps(True)))


def _add_missing_columns(engine: Engine) -> None:
    """create_all does not alter existing tables, so add columns introduced after the first release."""
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table.name})"))}
            for col in table.columns:
                if col.name not in existing:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} "
                                      f"{col.type.compile(dialect=engine.dialect)}"))


def _add_missing_indexes(engine: Engine) -> None:
    """create_all skips indexes when a table already exists, so apply declared indexes separately."""
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(engine, checkfirst=True)


def seed_kpi_targets(engine: Engine) -> None:
    """Insert newly introduced targets without overwriting configured values."""
    with engine.begin() as conn:
        for key, (label, target, band, direction, unit) in DEFAULT_KPI_TARGETS.items():
            conn.execute(
                sqlite_insert(KPITarget)
                .values(kpi_key=key, label=label, target=target, watch_band=band, direction=direction, unit=unit)
                .on_conflict_do_nothing()
            )


def seed_defaults(engine: Engine) -> None:
    seed_kpi_targets(engine)
    now = datetime.now()
    with engine.begin() as conn:
        for i, name in enumerate(DEFAULT_SHIFTS):
            conn.execute(sqlite_insert(Shift).values(name=name, sort_order=i, active=True).on_conflict_do_nothing())
        for key, value in DEFAULT_APP_SETTINGS.items():
            conn.execute(sqlite_insert(AppSetting).values(key=key, value=json.dumps(value)).on_conflict_do_nothing())
        for wo_type, (cls, labor, ftf, pm) in DEFAULT_WO_TYPES.items():
            conn.execute(
                sqlite_insert(WOTypeConfig)
                .values(wo_type=wo_type, classification=cls, counts_toward_labor=labor, ftf_qualifying=ftf,
                        is_pm=pm, reviewed=True, first_seen=now)
                .on_conflict_do_nothing()
            )


def read_df(engine: Engine, sql: str, params: dict | None = None, parse_dates: list[str] | None = None) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {}, parse_dates=parse_dates)


def get_app_settings(engine: Engine) -> dict[str, Any]:
    settings = dict(DEFAULT_APP_SETTINGS)
    with engine.connect() as conn:
        for row in conn.execute(select(AppSetting.key, AppSetting.value)):
            settings[row.key] = json.loads(row.value) if row.value is not None else None
    return settings


def set_app_setting(engine: Engine, key: str, value: Any, bump: bool = True) -> None:
    with engine.begin() as conn:
        conn.execute(
            sqlite_insert(AppSetting)
            .values(key=key, value=json.dumps(value))
            .on_conflict_do_update(index_elements=["key"], set_={"value": json.dumps(value)})
        )
    if bump:
        bump_data_version(engine)


def get_data_version(engine: Engine) -> int:
    with engine.connect() as conn:
        value = conn.execute(select(AppSetting.value).where(AppSetting.key == "data_version")).scalar()
    return int(json.loads(value)) if value else 0


def bump_data_version(engine: Engine) -> int:
    version = get_data_version(engine) + 1
    set_app_setting(engine, "data_version", version, bump=False)
    return version
