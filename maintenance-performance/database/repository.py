"""Query and persistence helpers used by the UI. All writes bump the data version to invalidate caches."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from database.database import bump_data_version, read_df
from database.page_access import protected_write
from database.models import (
    AdministrativeUser,
    DayforceHours,
    KPITarget,
    PMAreaStatus,
    Shift,
    Technician,
    TechnicianAlias,
    WeekLock,
    WOTypeConfig,
)
from utils.dates import week_key
from utils.names import clean_text, name_key

FIIX_DATES = ["date_created", "date_completed", "week_start", "week_end"]
CALL_DATES = ["call_time", "response_time", "arrival_time", "complete_time", "week_start", "week_end"]


# ---------- reads ----------

def load_technicians(engine: Engine) -> pd.DataFrame:
    df = read_df(engine, "SELECT * FROM technicians ORDER BY technician_name")
    df["active"] = df["active"].astype(bool)
    df["is_lead"] = df["is_lead"].fillna(False).astype(bool)
    df["needs_review"] = df["needs_review"].fillna(False).astype(bool)
    for col in ("start_date", "end_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    return df


def load_administrative_users(engine: Engine) -> pd.DataFrame:
    df = read_df(engine, "SELECT * FROM administrative_users ORDER BY display_name, email")
    df["active"] = df["active"].astype(bool)
    return df


def ensure_bootstrap_administrator(engine: Engine, email: str, display_name: str = "") -> None:
    normalized_email = clean_text(email)
    if not normalized_email:
        return
    with engine.begin() as conn:
        count = conn.execute(select(AdministrativeUser.administrative_user_id)).first()
        if count is None:
            conn.execute(sqlite_insert(AdministrativeUser).values(
                email=normalized_email, display_name=clean_text(display_name) or "", active=True))


def is_active_administrator(engine: Engine, email: str | None) -> bool:
    normalized_email = (email or "").strip().casefold()
    if not normalized_email:
        return False
    with engine.connect() as conn:
        result = conn.execute(select(AdministrativeUser.administrative_user_id).where(
            AdministrativeUser.active.is_(True),
            func.lower(AdministrativeUser.email) == normalized_email)).first()
    return result is not None


@protected_write
def save_administrative_users(engine: Engine, rows: list[dict]) -> None:
    values = []
    seen_emails = set()
    active_count = 0
    for row in rows:
        email = clean_text(row.get("email"))
        if not email or "@" not in email:
            raise ValueError("Every administrative user must have a valid email address.")
        key = email.casefold()
        if key in seen_emails:
            raise ValueError(f"Administrative email addresses must be unique: {email}")
        seen_emails.add(key)
        active = bool(row.get("active", True))
        active_count += int(active)
        values.append({
            "administrative_user_id": (int(row["administrative_user_id"])
                                       if pd.notna(row.get("administrative_user_id")) else None),
            "email": email,
            "display_name": clean_text(row.get("display_name")) or "",
            "active": active,
        })
    if not active_count:
        raise ValueError("At least one administrative user must remain active.")

    with engine.begin() as conn:
        existing_ids = {row[0] for row in conn.execute(select(AdministrativeUser.administrative_user_id))}
        kept_ids = set()
        for row in values:
            user_id = row.pop("administrative_user_id")
            if user_id is None:
                conn.execute(sqlite_insert(AdministrativeUser).values(**row))
            else:
                kept_ids.add(user_id)
                conn.execute(update(AdministrativeUser).where(
                    AdministrativeUser.administrative_user_id == user_id).values(**row))
        removed_ids = existing_ids - kept_ids
        if removed_ids:
            conn.execute(delete(AdministrativeUser).where(
                AdministrativeUser.administrative_user_id.in_(removed_ids)))


def load_aliases(engine: Engine) -> pd.DataFrame:
    return read_df(engine, """SELECT a.*, t.technician_name FROM technician_aliases a
                              JOIN technicians t ON t.technician_id = a.technician_id ORDER BY a.source, a.alias""")


def load_shifts(engine: Engine) -> pd.DataFrame:
    return read_df(engine, "SELECT * FROM shifts ORDER BY sort_order, name")


def load_wo_types(engine: Engine) -> pd.DataFrame:
    df = read_df(engine, "SELECT * FROM wo_type_config ORDER BY classification, wo_type")
    for col in ("counts_toward_labor", "ftf_qualifying", "is_pm", "reviewed"):
        df[col] = df[col].astype(bool)
    return df


def load_kpi_targets(engine: Engine) -> pd.DataFrame:
    return read_df(engine, "SELECT * FROM kpi_targets")


def load_fiix(engine: Engine) -> pd.DataFrame:
    return read_df(engine, "SELECT * FROM fiix_work_orders", parse_dates=FIIX_DATES)


def load_maintenance_calls(engine: Engine) -> pd.DataFrame:
    return read_df(engine, "SELECT * FROM ignition_calls WHERE call_type_norm = 'maintenance'", parse_dates=CALL_DATES)


def load_call_type_counts(engine: Engine) -> pd.DataFrame:
    return read_df(engine, """SELECT COALESCE(call_type, '(blank)') AS call_type, COUNT(*) AS calls
                              FROM ignition_calls GROUP BY call_type ORDER BY calls DESC""")


def load_dayforce(engine: Engine) -> pd.DataFrame:
    return read_df(engine, "SELECT * FROM dayforce_hours")


def load_dayforce_entries(engine: Engine) -> pd.DataFrame:
    return read_df(engine, "SELECT * FROM dayforce_entries",
                   parse_dates=["work_date", "in_time", "out_time", "report_start", "report_end"])


def load_pm_status(engine: Engine) -> pd.DataFrame:
    return read_df(engine, "SELECT * FROM pm_area_status")


def load_import_history(engine: Engine) -> pd.DataFrame:
    return read_df(engine, "SELECT * FROM file_import_history ORDER BY import_datetime DESC, import_id DESC",
                   parse_dates=["file_modified", "import_datetime"])


def load_dq_issues(engine: Engine) -> pd.DataFrame:
    return read_df(engine, "SELECT * FROM data_quality_issues ORDER BY issue_id DESC")


def latest_imported_week(engine: Engine) -> int | None:
    df = read_df(engine, """SELECT MAX(k) AS k FROM (SELECT MAX(week_key) k FROM fiix_work_orders
                            UNION ALL SELECT MAX(week_key) FROM ignition_calls)""")
    k = df["k"].iloc[0]
    return int(k) if pd.notna(k) else None


# ---------- technicians & aliases ----------

def _tech_values(row: dict) -> dict:
    def _date(v):
        if v is None or v is pd.NaT or (isinstance(v, float) and pd.isna(v)):
            return None
        ts = pd.to_datetime(v, errors="coerce")
        return None if pd.isna(ts) else ts.date()

    return {
        "technician_name": clean_text(row.get("technician_name")),
        "shift": clean_text(row.get("shift")),
        "crew": clean_text(row.get("crew")),
        "department": clean_text(row.get("department")),
        "active": bool(row.get("active", True)) if pd.notna(row.get("active", True)) else True,
        "is_lead": bool(row.get("is_lead")) if pd.notna(row.get("is_lead")) else False,
        "needs_review": bool(row.get("needs_review")) if pd.notna(row.get("needs_review")) else False,
        "dayforce_name": clean_text(row.get("dayforce_name")),
        "dayforce_id": clean_text(str(row.get("dayforce_id")).split(".")[0]) if pd.notna(row.get("dayforce_id")) else None,
        "fiix_name": clean_text(row.get("fiix_name")),
        "ignition_name": clean_text(row.get("ignition_name")),
        "supervisor": clean_text(row.get("supervisor")),
        "start_date": _date(row.get("start_date")),
        "end_date": _date(row.get("end_date")),
    }


@protected_write
def save_technicians(engine: Engine, rows: list[dict], deactivate_ids: list[int] | None = None) -> None:
    with engine.begin() as conn:
        for row in rows:
            values = _tech_values(row)
            if not values["technician_name"]:
                continue
            tech_id = row.get("technician_id")
            if tech_id is not None and pd.notna(tech_id):
                conn.execute(update(Technician).where(Technician.technician_id == int(tech_id)).values(**values))
            else:
                conn.execute(sqlite_insert(Technician).values(**values))
        if deactivate_ids:
            conn.execute(update(Technician).where(Technician.technician_id.in_(deactivate_ids)).values(active=False))
    bump_data_version(engine)


@protected_write
def create_technician(engine: Engine, name: str, **fields) -> int:
    return _insert_technician(engine, name, **fields)


def _insert_technician(engine: Engine, name: str, **fields) -> int:
    """Shared insert; automatic Dayforce discovery does not require the manual-entry password."""
    values = _tech_values({"technician_name": name, **fields})
    with engine.begin() as conn:
        result = conn.execute(sqlite_insert(Technician).values(**values))
        tech_id = int(result.inserted_primary_key[0])
    bump_data_version(engine)
    return tech_id


@protected_write
def mark_technicians_reviewed(engine: Engine, technician_ids: list[int]) -> None:
    with engine.begin() as conn:
        conn.execute(update(Technician).where(Technician.technician_id.in_(technician_ids)).values(needs_review=False))
    bump_data_version(engine)


@protected_write
def link_new_technician(engine: Engine, new_id: int, existing_id: int) -> None:
    """Fold an auto-created Dayforce technician into an existing technician (same person, different name)."""
    with engine.begin() as conn:
        new = conn.execute(select(Technician).where(Technician.technician_id == new_id)).mappings().one()
        existing = conn.execute(select(Technician).where(Technician.technician_id == existing_id)).mappings().one()
        conn.execute(update(TechnicianAlias).where(TechnicianAlias.technician_id == new_id)
                     .values(technician_id=existing_id))
        conn.execute(update(DayforceHours).where(DayforceHours.technician_id == new_id)
                     .values(technician_id=existing_id))
        conn.execute(delete(Technician).where(Technician.technician_id == new_id))
        conn.execute(update(Technician).where(Technician.technician_id == existing_id).values(
            dayforce_id=existing["dayforce_id"] or new["dayforce_id"],
            dayforce_name=existing["dayforce_name"] or new["dayforce_name"],
            is_lead=bool(existing["is_lead"]) or bool(new["is_lead"])))
    bump_data_version(engine)


@protected_write
def add_alias(engine: Engine, source: str, alias: str, technician_id: int) -> None:
    key = name_key(alias)
    with engine.begin() as conn:
        conn.execute(
            sqlite_insert(TechnicianAlias)
            .values(source=source, alias=clean_text(alias), alias_key=key, technician_id=int(technician_id),
                    created_at=datetime.now())
            .on_conflict_do_update(index_elements=["source", "alias_key"],
                                   set_={"technician_id": int(technician_id), "alias": clean_text(alias)})
        )
    bump_data_version(engine)


@protected_write
def delete_aliases(engine: Engine, alias_ids: list[int]) -> None:
    with engine.begin() as conn:
        conn.execute(delete(TechnicianAlias).where(TechnicianAlias.alias_id.in_(alias_ids)))
    bump_data_version(engine)


# ---------- configuration ----------

@protected_write
def save_shifts(engine: Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        for row in rows:
            name = clean_text(row.get("name"))
            if not name:
                continue
            values = {"name": name, "sort_order": int(row.get("sort_order") or 0), "active": bool(row.get("active", True))}
            if pd.notna(row.get("shift_id")):
                conn.execute(update(Shift).where(Shift.shift_id == int(row["shift_id"])).values(**values))
            else:
                conn.execute(sqlite_insert(Shift).values(**values).on_conflict_do_nothing())
    bump_data_version(engine)


@protected_write
def save_wo_types(engine: Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        for row in rows:
            conn.execute(update(WOTypeConfig).where(WOTypeConfig.wo_type == row["wo_type"]).values(
                classification=row.get("classification") or "Other",
                counts_toward_labor=bool(row.get("counts_toward_labor")),
                ftf_qualifying=bool(row.get("ftf_qualifying")),
                is_pm=bool(row.get("is_pm")),
                reviewed=bool(row.get("reviewed")),
            ))
    bump_data_version(engine)


@protected_write
def save_kpi_targets(engine: Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        for row in rows:
            conn.execute(update(KPITarget).where(KPITarget.kpi_key == row["kpi_key"]).values(
                target=None if pd.isna(row.get("target")) else float(row["target"]),
                watch_band=None if pd.isna(row.get("watch_band")) else float(row["watch_band"]),
            ))
    bump_data_version(engine)


# ---------- weekly manual entry ----------

def is_week_locked(engine: Engine, dataset: str, year: int, week: int) -> bool:
    with engine.connect() as conn:
        return conn.execute(select(WeekLock.lock_id).where(
            WeekLock.dataset == dataset, WeekLock.year == year, WeekLock.calendar_week == week)).first() is not None


@protected_write
def set_week_lock(engine: Engine, dataset: str, year: int, week: int, locked: bool) -> None:
    with engine.begin() as conn:
        if locked:
            conn.execute(sqlite_insert(WeekLock).values(dataset=dataset, year=year, calendar_week=week,
                                                        locked_at=datetime.now()).on_conflict_do_nothing())
        else:
            conn.execute(delete(WeekLock).where(WeekLock.dataset == dataset, WeekLock.year == year,
                                                WeekLock.calendar_week == week))


def _num(v) -> float | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    number = pd.to_numeric(v, errors="coerce")
    if pd.isna(number):
        if pd.isna(v):
            return None
        raise ValueError(f"Invalid numeric value: {v!r}.")
    return float(number)


def _whole_count(v, label: str) -> float | None:
    number = _num(v)
    if number is not None and not number.is_integer():
        raise ValueError(f"{label} must be a whole number.")
    return number


@protected_write
def save_dayforce_week(engine: Engine, year: int, week: int, rows: list[dict]) -> int:
    """Upsert rows for one week. Rows with no hours and no comment delete any existing entry."""
    from kpi.labor_efficiency import total_hours_worked

    if is_week_locked(engine, "dayforce", year, week):
        raise PermissionError(f"Dayforce week {year}-CW{week:02d} is locked.")
    saved = 0
    now = datetime.now()
    with engine.begin() as conn:
        for row in rows:
            tech_id = int(row["technician_id"])
            parts = [_num(row.get(c)) for c in ("regular_hours", "overtime_hours", "other_paid_hours", "excluded_hours")]
            override = _num(row.get("total_override"))
            comments = clean_text(row.get("comments"))
            cond = (DayforceHours.year == year, DayforceHours.calendar_week == week,
                    DayforceHours.technician_id == tech_id)
            if all(p is None for p in parts) and override is None and not comments:
                conn.execute(delete(DayforceHours).where(*cond))
                continue
            if any(p is not None and p < 0 for p in parts) or (override is not None and override < 0):
                raise ValueError(f"Negative hours are not allowed (technician id {tech_id}).")
            total = override if override is not None else total_hours_worked(*parts)
            values = dict(year=year, calendar_week=week, week_key=week_key(year, week), technician_id=tech_id,
                          shift=clean_text(row.get("shift")), regular_hours=parts[0], overtime_hours=parts[1],
                          other_paid_hours=parts[2], excluded_hours=parts[3], total_hours_worked=total,
                          total_overridden=override is not None, comments=comments, updated_at=now)
            conn.execute(sqlite_insert(DayforceHours).values(**values).on_conflict_do_update(
                index_elements=["year", "calendar_week", "technician_id"], set_=values))
            saved += 1
    bump_data_version(engine)
    return saved


@protected_write
def save_pm_status_week(engine: Engine, year: int, week: int, rows: list[dict]) -> int:
    """Upsert PM Open/Closed counts per area for one week. Blank rows delete any existing entry."""
    if is_week_locked(engine, "pm", year, week):
        raise PermissionError(f"PM week {year}-CW{week:02d} is locked.")
    saved = 0
    now = datetime.now()
    with engine.begin() as conn:
        for row in rows:
            area = clean_text(row.get("area"))
            if not area:
                continue
            pm_open = _whole_count(row.get("pm_open"), f"{area} PMs Open")
            pm_closed = _whole_count(row.get("pm_closed"), f"{area} PMs Closed")
            comments = clean_text(row.get("comments"))
            cond = (PMAreaStatus.year == year, PMAreaStatus.calendar_week == week, PMAreaStatus.area == area)
            if pm_open is None and pm_closed is None and not comments:
                conn.execute(delete(PMAreaStatus).where(*cond))
                continue
            if (pm_open or 0) < 0 or (pm_closed or 0) < 0:
                raise ValueError(f"Negative PM counts are not allowed ({area}).")
            values = dict(year=year, calendar_week=week, week_key=week_key(year, week), area=area,
                          pm_open=pm_open, pm_closed=pm_closed, comments=comments, updated_at=now)
            conn.execute(sqlite_insert(PMAreaStatus).values(**values).on_conflict_do_update(
                index_elements=["year", "calendar_week", "area"], set_=values))
            saved += 1
    bump_data_version(engine)
    return saved
