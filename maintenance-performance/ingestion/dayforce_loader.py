"""Dayforce time-entry report ingestion (print-style 'Unauthorized Records' / time-entry .xlsx exports).

Each daily entry is stored as-is. Entries are assigned by clock-in time; Sunday clock-ins at or after
9:30 PM belong to the following calendar week. Re-importing a report replaces the entries for the
employees and dates it covers.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import and_, delete, insert, update
from sqlalchemy.engine import Engine

from database.models import DayforceEntry, FileImportHistory
from utils.dates import parse_filename_week, production_week, week_key
from utils.names import clean_text, flip_name, name_key
from utils.validation import ValidationError

HEADER_LABELS = ["Name", "ID", "Date", "Job", "In", "Out", "Pay Code", "Pay Category", "Normal", "Other"]
DAYFORCE_WEEK_OFFSET_HOURS = 2.5
_RANGE = re.compile(r"Date Range:\s*(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})")


def _find_date_range(raw: pd.DataFrame) -> tuple[date, date]:
    for value in raw.iloc[:30].to_numpy().ravel():
        if isinstance(value, str) and (m := _RANGE.search(value)):
            start, end = (datetime.strptime(v, "%m/%d/%Y").date() for v in m.groups())
            return start, end
    raise ValidationError("Report 'Date Range' header not found.")


def _find_header(raw: pd.DataFrame) -> tuple[int, dict[str, int]]:
    for i in range(min(len(raw), 200)):
        labels = {str(v).strip(): c for c, v in raw.iloc[i].items() if isinstance(v, str)}
        if all(label in labels for label in HEADER_LABELS):
            return i, {label: labels[label] for label in HEADER_LABELS}
    raise ValidationError(f"Time-entry header row not found (expected columns: {', '.join(HEADER_LABELS)}).")


def _entry_date(text: str, start: date, end: date) -> date:
    """Report dates look like 'Sun,Oct 4' with no year; pick the year that falls inside the report range."""
    month_day = text.split(",", 1)[-1].strip()
    for year in {start.year, end.year}:
        d = datetime.strptime(f"{month_day} {year}", "%b %d %Y").date()
        if start - timedelta(days=1) <= d <= end + timedelta(days=1):
            return d
    raise ValueError(f"Date '{text}' is outside the report range {start} - {end}.")


def _clock(d: date, value) -> datetime | None:
    s = clean_text(value)
    if not s:
        return None
    return datetime.combine(d, datetime.strptime(s.lower().replace(" ", ""), "%I:%M%p").time())


def _num(value) -> float:
    v = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(v) else float(v)


def parse_dayforce_report(raw: pd.DataFrame) -> tuple[pd.DataFrame, date, date]:
    start, end = _find_date_range(raw)
    header_row, col = _find_header(raw)
    rows = []
    name = emp_id = job = None
    work_date = None
    prev_in = None
    for i in range(header_row + 1, len(raw)):
        r = raw.iloc[i]
        if any(isinstance(v, str) and v.strip().startswith("Total") for v in r.to_numpy()):
            continue
        if pd.notna(r[col["Name"]]) and pd.notna(r[col["ID"]]):
            name = clean_text(r[col["Name"]])
            emp_id = str(r[col["ID"]]).split(".")[0].strip()
            work_date, job = None, None
            continue
        if pd.notna(r[col["Date"]]):
            work_date = _entry_date(str(r[col["Date"]]), start, end)
            job = clean_text(r[col["Job"]])
        pay_code = clean_text(r[col["Pay Code"]])
        if not pay_code or work_date is None or name is None:
            continue  # summary block or layout row
        if pd.notna(r[col["Job"]]):
            job = clean_text(r[col["Job"]])
        in_time, out_time = _clock(work_date, r[col["In"]]), _clock(work_date, r[col["Out"]])
        if in_time and out_time and out_time <= in_time:
            out_time += timedelta(days=1)
        # A later segment of an overnight shift (e.g. 7:00-8:28 am late-out after 11 pm-7 am) is on the next day.
        if in_time and prev_in and prev_in[0] == (emp_id, work_date) and in_time < prev_in[1]:
            in_time += timedelta(days=1)
            out_time = out_time + timedelta(days=1) if out_time else None
        if in_time:
            prev_in = ((emp_id, work_date), in_time)
        rows.append(dict(employee_id=emp_id, employee_name=name, employee_key=name_key(name), work_date=work_date,
                         in_time=in_time, out_time=out_time, job=job, pay_code=pay_code,
                         pay_category=clean_text(r[col["Pay Category"]]),
                         normal_hours=_num(r[col["Normal"]]), other_hours=_num(r[col["Other"]])))
    df = pd.DataFrame(rows)
    if df.empty:
        return df, start, end
    df["week_key"] = [week_key(*production_week(_clock_in_anchor(r), DAYFORCE_WEEK_OFFSET_HOURS))
                      for r in df.itertuples()]
    df["year"], df["calendar_week"] = df["week_key"] // 100, df["week_key"] % 100
    return df, start, end


def _clock_in_anchor(r) -> datetime:
    """Clock-in time, or noon of work date for unpunched (PTO/premium) lines."""
    if pd.notna(r.in_time):
        return r.in_time
    return datetime.combine(r.work_date, time(12))


def import_dayforce_file(engine: Engine, path: Path, settings: dict, modified: datetime, size: int, file_hash: str):
    from ingestion.file_manager import ImportOutcome

    fn_year, fn_week = parse_filename_week(path.name)
    raw = pd.read_excel(path, sheet_name=0, header=None)
    entries, start, end = parse_dayforce_report(raw)
    warnings = [f"Report date range {start:%m/%d/%Y} - {end:%m/%d/%Y}; {entries['employee_id'].nunique() if not entries.empty else 0} employees."]
    if entries.empty:
        warnings.append("No time entries found.")
    with engine.begin() as conn:
        import_id = conn.execute(insert(FileImportHistory).values(
            source="dayforce", filename=path.name, full_path=str(path.resolve()), file_modified=modified,
            file_size=size, file_hash=file_hash,
            filename_year=fn_year, filename_week=fn_week, import_datetime=datetime.now(),
            status="in_progress")).inserted_primary_key[0]
        replaced = 0
        if not entries.empty:
            for emp in entries["employee_id"].unique():
                replaced += conn.execute(delete(DayforceEntry).where(and_(
                    DayforceEntry.employee_id == emp, DayforceEntry.work_date >= start,
                    DayforceEntry.work_date <= end))).rowcount
            records = entries.assign(source_file=path.name, import_id=import_id, report_start=start,
                                     report_end=end).to_dict("records")
            for rec in records:
                for k in ("in_time", "out_time"):
                    rec[k] = None if pd.isna(rec[k]) else pd.Timestamp(rec[k]).to_pydatetime()
                rec["week_key"], rec["year"], rec["calendar_week"] = int(rec["week_key"]), int(rec["year"]), int(rec["calendar_week"])
            conn.execute(insert(DayforceEntry), records)
        if replaced:
            warnings.append(f"Replaced {replaced} previously imported entries for the same employees and dates.")
        weeks = sorted(entries["week_key"].unique()) if not entries.empty else []
        conn.execute(update(FileImportHistory).where(FileImportHistory.import_id == import_id).values(
            row_count=len(raw), inserted_count=len(entries), duplicate_count=replaced,
            year=int(weeks[-1]) // 100 if weeks else None, calendar_week=int(weeks[-1]) % 100 if weeks else None,
            status="success", warnings="\n".join(warnings)))
    created = create_technicians_for_new_employees(engine, entries)
    if created:
        note = "New Dayforce employees added as technicians (review on Administration → Technicians): " + ", ".join(created)
        warnings.append(note)
        with engine.begin() as conn:
            conn.execute(update(FileImportHistory).where(FileImportHistory.import_id == import_id)
                         .values(warnings="\n".join(warnings)))
    return ImportOutcome("dayforce", path.name, "success", len(raw), len(entries), replaced, warnings)


def create_technicians_for_new_employees(engine: Engine, entries: pd.DataFrame) -> list[str]:
    """Add Dayforce employees that match no technician as new technicians flagged 'needs review'."""
    from database import repository as repo
    from kpi.dayforce import resolve_dayforce_technicians

    if entries.empty:
        return []
    techs, aliases = repo.load_technicians(engine), repo.load_aliases(engine)
    employees = entries.drop_duplicates("employee_id")[["employee_id", "employee_name"]]
    unmatched = employees[resolve_dayforce_technicians(employees, techs, aliases).isna().to_numpy()]
    taken = set(techs["technician_name"].str.casefold())
    created = []
    for emp in unmatched.itertuples(index=False):
        name = flip_name(emp.employee_name) or emp.employee_name
        if name.casefold() in taken:
            name = f"{name} ({emp.employee_id})"
        jobs = entries.loc[entries["employee_id"] == emp.employee_id, "job"].dropna().astype(str)
        repo._insert_technician(engine, name, dayforce_id=emp.employee_id, dayforce_name=emp.employee_name,
                               active=True, needs_review=True,
                               is_lead=bool(jobs.str.contains("Lead", case=False, regex=False).any()))
        taken.add(name.casefold())
        created.append(name)
    return created
