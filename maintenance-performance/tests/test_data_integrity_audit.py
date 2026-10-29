import os
from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import insert, inspect, text

from database.database import create_db_engine, init_db
from database.models import FileImportHistory
from database.repository import (
    create_technician,
    load_dayforce,
    load_fiix,
    load_pm_status,
    save_dayforce_week,
    save_pm_status_week,
)
from ingestion.file_manager import SourceSpec, _already_imported, _file_identity, import_file
from kpi.labor_efficiency import qualifying_labor_rows
from utils.validation import LoadResult, to_hours


def test_invalid_dayforce_number_does_not_delete_saved_hours(tmp_path):
    engine = create_db_engine(tmp_path / "dayforce.db")
    init_db(engine)
    try:
        technician_id = create_technician(engine, "Audit Technician", dayforce_id="AUDIT-1")
        save_dayforce_week(engine, 2026, 40, [{"technician_id": technician_id, "regular_hours": 40}])

        with pytest.raises(ValueError, match="Invalid numeric value"):
            save_dayforce_week(engine, 2026, 40, [
                {"technician_id": technician_id, "regular_hours": "not-a-number"},
            ])

        saved = load_dayforce(engine)
        assert len(saved) == 1
        assert saved.iloc[0]["regular_hours"] == pytest.approx(40)
    finally:
        engine.dispose()


def test_invalid_pm_number_does_not_delete_saved_counts(tmp_path):
    engine = create_db_engine(tmp_path / "pm.db")
    init_db(engine)
    try:
        save_pm_status_week(engine, 2026, 40, [{"area": "Audit", "pm_open": 12, "pm_closed": 8}])

        with pytest.raises(ValueError, match="Invalid numeric value"):
            save_pm_status_week(engine, 2026, 40, [
                {"area": "Audit", "pm_open": "not-a-number", "pm_closed": None},
            ])

        saved = load_pm_status(engine)
        assert len(saved) == 1
        assert saved.iloc[0]["pm_open"] == pytest.approx(12)
        assert saved.iloc[0]["pm_closed"] == pytest.approx(8)
    finally:
        engine.dispose()


def test_fractional_pm_count_does_not_overwrite_saved_counts(tmp_path):
    engine = create_db_engine(tmp_path / "fractional-pm.db")
    init_db(engine)
    try:
        save_pm_status_week(engine, 2026, 40, [{"area": "Audit", "pm_open": 12, "pm_closed": 8}])

        with pytest.raises(ValueError, match="whole number"):
            save_pm_status_week(engine, 2026, 40, [
                {"area": "Audit", "pm_open": 12.5, "pm_closed": 8},
            ])

        saved = load_pm_status(engine)
        assert len(saved) == 1
        assert saved.iloc[0]["pm_open"] == pytest.approx(12)
        assert saved.iloc[0]["pm_closed"] == pytest.approx(8)
    finally:
        engine.dispose()


def test_fiix_hhmm_rejects_minutes_outside_range():
    parsed = to_hours(pd.Series(["1:59", "1:60", "1:90"]))

    assert parsed.iloc[0] == pytest.approx(1 + 59 / 60)
    assert pd.isna(parsed.iloc[1])
    assert pd.isna(parsed.iloc[2])


def test_negative_fiix_time_is_not_eligible_for_labor():
    fiix = pd.DataFrame({
        "wo": ["valid", "negative"],
        "counts_toward_labor": [True, True],
        "technician_id": pd.array([1, 1], dtype="Int64"),
        "time_spent_hours": [2.0, -1.0],
    })

    assert qualifying_labor_rows(fiix)["wo"].tolist() == ["valid"]


def test_changed_same_size_file_is_pending_even_with_same_timestamp(tmp_path):
    path = tmp_path / "FiixCW40.csv"
    path.write_bytes(b"AAAA")
    modified, size, file_hash = _file_identity(path)
    engine = create_db_engine(tmp_path / "imports.db")
    init_db(engine)
    try:
        with engine.begin() as conn:
            conn.execute(insert(FileImportHistory).values(
                source="fiix",
                filename=path.name,
                full_path=str(path.resolve()),
                file_modified=modified,
                file_size=size,
                file_hash=file_hash,
                import_datetime=datetime.now(),
                status="success",
            ))

        with engine.connect() as conn:
            assert _already_imported(conn, path)

        path.write_bytes(b"BBBB")
        os.utime(path, (path.stat().st_atime, modified.timestamp()))

        with engine.connect() as conn:
            assert not _already_imported(conn, path)
    finally:
        engine.dispose()


def test_updated_file_can_import_new_work_orders_with_unchanged_stat_identity(tmp_path):
    path = tmp_path / "FiixCW40.csv"
    path.write_bytes(b"WO01")
    engine = create_db_engine(tmp_path / "updated-file.db")
    init_db(engine)

    def loader(source, _settings):
        return LoadResult(
            frame=pd.DataFrame({"wo": [source.read_text()], "wo_type": ["Reactive"], "week_key": [202640]}),
            source_rows=1,
        )

    spec = SourceSpec("fiix", "Fiix", "fiix_work_orders", "wo", "", "", loader=loader)
    try:
        assert import_file(engine, spec, path, {}).inserted == 1
        modified = path.stat().st_mtime
        path.write_bytes(b"WO02")
        os.utime(path, (path.stat().st_atime, modified))

        outcome = import_file(engine, spec, path, {})

        assert outcome.status == "success"
        assert outcome.inserted == 1
        assert set(load_fiix(engine)["wo"]) == {"WO01", "WO02"}
    finally:
        engine.dispose()


def test_existing_import_history_schema_adds_file_hash_column(tmp_path):
    engine = create_db_engine(tmp_path / "old-schema.db")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE file_import_history (import_id INTEGER PRIMARY KEY)"))

        init_db(engine)

        columns = {column["name"] for column in inspect(engine).get_columns("file_import_history")}
        assert "file_hash" in columns
    finally:
        engine.dispose()
