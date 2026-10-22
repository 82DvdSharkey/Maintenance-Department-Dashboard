import pandas as pd

from database.database import create_db_engine, init_db
from database import repository as repo
from ingestion.dayforce_loader import create_technicians_for_new_employees


def _engine(tmp_path):
    eng = create_db_engine(tmp_path / "t.db")
    init_db(eng)
    return eng


def _entries(rows):
    return pd.DataFrame(rows, columns=["employee_id", "employee_name", "job"])


def test_new_dayforce_employee_auto_created_and_flagged(tmp_path):
    eng = _engine(tmp_path)
    repo.create_technician(eng, "Jason Agle")
    entries = _entries([
        ("10052019", "Agle, Jason E", "0571 | Technician, Maintenance"),       # existing (flipped name match)
        ("10099999", "Newman, Pat Q", "0395 | Lead, Maintenance"),              # new lead
        ("10088888", "Doe, Jane", "0571 | Technician, Maintenance"),           # new technician
    ])
    created = create_technicians_for_new_employees(eng, entries)
    assert sorted(created) == ["Jane Doe", "Pat Newman"]
    techs = repo.load_technicians(eng).set_index("technician_name")
    assert techs.loc["Pat Newman", "needs_review"] and techs.loc["Pat Newman", "is_lead"]
    assert techs.loc["Pat Newman", "dayforce_id"] == "10099999"
    assert techs.loc["Jane Doe", "active"] and not techs.loc["Jane Doe", "is_lead"]
    assert not techs.loc["Jason Agle", "needs_review"]
    assert create_technicians_for_new_employees(eng, entries) == []  # matched by Dayforce ID on re-import


def test_link_new_technician_moves_dayforce_id_and_removes_duplicate(tmp_path):
    eng = _engine(tmp_path)
    existing = repo.create_technician(eng, "Tony Ballou")
    create_technicians_for_new_employees(eng, _entries([("10054837", "Ballou, Anthony W", "Technician")]))
    new_id = int(repo.load_technicians(eng).set_index("technician_name").loc["Anthony Ballou", "technician_id"])
    repo.link_new_technician(eng, new_id, existing)
    techs = repo.load_technicians(eng)
    assert techs["technician_name"].tolist() == ["Tony Ballou"]
    assert techs.iloc[0]["dayforce_id"] == "10054837"
