"""Source directory scanning, import tracking, duplicate prevention and database rebuild."""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from database.database import bump_data_version
from database.page_access import protected_write
from database.models import (
    DataQualityIssue,
    DayforceEntry,
    FiixWorkOrder,
    FileImportHistory,
    IgnitionCall,
    WOTypeConfig,
)
from ingestion.dayforce_loader import import_dayforce_file
from ingestion.fiix_loader import load_fiix_file
from ingestion.ignition_loader import load_ignition_file
from utils.dates import dominant_week, parse_filename_week, split_week_key
from utils.validation import Issue, LoadResult


@dataclass
class SourceSpec:
    name: str
    label: str
    table: str
    key: str
    dir_setting: str
    patterns_setting: str
    loader: Callable[[Path, dict], LoadResult] | None = None
    # Custom import (used by Dayforce, which replaces entries by date range instead of key de-duplication).
    importer: Callable | None = None
    sort_by_modified: bool = False


SOURCES = {
    "fiix": SourceSpec("fiix", "Fiix", FiixWorkOrder.__tablename__, "wo", "fiix_source_dir",
                       "fiix_file_patterns", load_fiix_file),
    "ignition": SourceSpec("ignition", "Ignition", IgnitionCall.__tablename__, "id", "ignition_source_dir",
                           "ignition_file_patterns", load_ignition_file),
    "dayforce": SourceSpec("dayforce", "Dayforce", DayforceEntry.__tablename__, "entry_id", "dayforce_source_dir",
                           "dayforce_file_patterns", importer=import_dayforce_file, sort_by_modified=True),
}


@dataclass
class ImportOutcome:
    source: str
    filename: str
    status: str
    row_count: int = 0
    inserted: int = 0
    duplicates: int = 0
    warnings: list[str] = field(default_factory=list)


def _sort_key(path: Path):
    year, week = parse_filename_week(path.name)
    return (path.parent.name, week or 0, path.name)


def scan_source_files(spec: SourceSpec, settings: dict) -> list[Path]:
    root = Path(settings.get(spec.dir_setting) or "")
    if not str(root) or not root.exists():
        return []
    files: set[Path] = set()
    for pattern in settings.get(spec.patterns_setting) or []:
        files.update(p for p in root.glob(pattern) if p.is_file() and not p.name.startswith("~$"))
    if spec.sort_by_modified:
        return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))
    return sorted(files, key=_sort_key)


def _file_identity(path: Path) -> tuple[datetime, int, str]:
    st = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return datetime.fromtimestamp(st.st_mtime).replace(microsecond=0), st.st_size, digest.hexdigest()


def _already_imported(conn: Connection, path: Path) -> bool:
    _, _, file_hash = _file_identity(path)
    return conn.execute(select(FileImportHistory.import_id).where(
        FileImportHistory.full_path == str(path.resolve()),
        FileImportHistory.file_hash == file_hash,
        FileImportHistory.status == "success",
    )).first() is not None


def _safe_already_imported(conn: Connection, path: Path) -> bool:
    # Unreachable files stay pending so the import attempt is recorded as failed.
    try:
        return _already_imported(conn, path)
    except OSError:
        return False


def pending_files(engine: Engine, settings: dict) -> dict[str, list[Path]]:
    result = {}
    with engine.connect() as conn:
        for name, spec in SOURCES.items():
            result[name] = [p for p in scan_source_files(spec, settings) if not _safe_already_imported(conn, p)]
    return result


def _existing_keys(conn: Connection, table: str, key: str, values: list) -> set:
    found: set = set()
    for i in range(0, len(values), 900):
        chunk = values[i:i + 900]
        params = {f"p{j}": v for j, v in enumerate(chunk)}
        sql = f"SELECT {key} FROM {table} WHERE {key} IN ({', '.join(':' + p for p in params)})"
        found.update(r[0] for r in conn.execute(text(sql), params))
    return found


def _write_frame(conn: Connection, table: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    chunk = max(1, 30000 // max(1, len(df.columns)))
    df.to_sql(table, conn, if_exists="append", index=False, chunksize=chunk, method="multi")


def _discover_wo_types(conn: Connection, wo_types: pd.Series) -> None:
    now = datetime.now()
    for wo_type in sorted(set(wo_types.dropna())):
        conn.execute(sqlite_insert(WOTypeConfig).values(
            wo_type=wo_type, classification="Other", counts_toward_labor=True, ftf_qualifying=False,
            is_pm=False, reviewed=False, first_seen=now).on_conflict_do_nothing())


def _table_columns(conn: Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))]


def import_file(engine: Engine, spec: SourceSpec, path: Path, settings: dict) -> ImportOutcome:
    fn_year, fn_week = parse_filename_week(path.name)
    history = dict(source=spec.name, filename=path.name, full_path=str(path.resolve()), file_modified=None,
                   file_size=None, file_hash=None, filename_year=fn_year, filename_week=fn_week)
    warnings: list[str] = []
    try:
        history["file_modified"], history["file_size"], history["file_hash"] = _file_identity(path)
        if spec.importer:
            return spec.importer(engine, path, settings, history["file_modified"], history["file_size"],
                                 history["file_hash"])
        result = spec.loader(path, settings)
        df = result.frame
        warnings += result.warnings
        dom = dominant_week(df["week_key"]) if not df.empty else None
        data_year, data_week = split_week_key(dom) if dom else (None, None)
        if df.empty:
            warnings.append("File contains no data rows.")
        if fn_week and data_week and fn_week != data_week:
            warnings.append(f"Filename week CW{fn_week} differs from report data week CW{data_week} ({data_year}).")
        if fn_year and data_year and fn_year != data_year:
            warnings.append(f"Filename year {fn_year} differs from report data year {data_year}.")
        if df["week_key"].isna().any():
            warnings.append(f"{int(df['week_key'].isna().sum())} rows have no valid date for week assignment.")

        with engine.begin() as conn:
            import_id = conn.execute(insert(FileImportHistory).values(
                **history, import_datetime=datetime.now(), status="in_progress")).inserted_primary_key[0]
            keys = df[spec.key].tolist()
            existing = _existing_keys(conn, spec.table, spec.key, keys)
            dup_mask = df[spec.key].isin(existing)
            new = df[~dup_mask].copy()
            new["import_id"] = import_id
            new["source_file"] = path.name
            cols = [c for c in _table_columns(conn, spec.table) if c in new.columns]
            _write_frame(conn, spec.table, new[cols])
            issues = list(result.issues) + [
                Issue(spec.name, "duplicate_already_imported", str(k),
                      f"{spec.key.upper()} already imported from an earlier file; skipped.")
                for k in df.loc[dup_mask, spec.key]
            ]
            if issues:
                conn.execute(insert(DataQualityIssue), [
                    dict(source=i.source, issue_type=i.issue_type, record_key=i.record_key, description=i.description,
                         file_name=path.name, import_id=import_id, created_at=datetime.now()) for i in issues])
            if spec.name == "fiix":
                _discover_wo_types(conn, new["wo_type"])
            if dup_mask.any():
                warnings.append(f"{int(dup_mask.sum())} records already imported were skipped.")
            conn.execute(update(FileImportHistory).where(FileImportHistory.import_id == import_id).values(
                row_count=result.source_rows, inserted_count=len(new), duplicate_count=int(dup_mask.sum()),
                year=data_year, calendar_week=data_week, status="success", warnings="\n".join(warnings) or None))
        return ImportOutcome(spec.name, path.name, "success", result.source_rows, len(new), int(dup_mask.sum()), warnings)
    except Exception as exc:  # record every failure in import history rather than dropping it
        warnings.append(f"{type(exc).__name__}: {exc}")
        with engine.begin() as conn:
            conn.execute(insert(FileImportHistory).values(
                **history, import_datetime=datetime.now(), status="failed", warnings="\n".join(warnings)))
        return ImportOutcome(spec.name, path.name, "failed", warnings=warnings)


ProgressCallback = Callable[[int, int, str], None]

# Serializes manual and automatic refreshes across all sessions in the Streamlit process.
REFRESH_LOCK = threading.Lock()


def refresh_weekly_data(engine: Engine, settings: dict, progress: ProgressCallback | None = None) -> list[ImportOutcome]:
    with REFRESH_LOCK:
        return _refresh(engine, settings, progress)


def _refresh(engine: Engine, settings: dict, progress: ProgressCallback | None = None) -> list[ImportOutcome]:
    pending = pending_files(engine, settings)
    work = [(SOURCES[name], p) for name, paths in pending.items() for p in paths]
    outcomes = []
    for i, (spec, path) in enumerate(work, start=1):
        if progress:
            progress(i, len(work), f"{spec.label}: {path.parent.name}/{path.name}")
        outcomes.append(import_file(engine, spec, path, settings))
    if outcomes:
        bump_data_version(engine)
    return outcomes


@protected_write
def rebuild_database(engine: Engine, settings: dict, progress: ProgressCallback | None = None) -> list[ImportOutcome]:
    """Clear imported source data (configuration and manual entries are kept) and re-import all files."""
    with REFRESH_LOCK:
        with engine.begin() as conn:
            conn.execute(delete(DataQualityIssue))
            conn.execute(delete(FiixWorkOrder))
            conn.execute(delete(IgnitionCall))
            conn.execute(delete(DayforceEntry))
            conn.execute(delete(FileImportHistory))
        bump_data_version(engine)
        return _refresh(engine, settings, progress)
