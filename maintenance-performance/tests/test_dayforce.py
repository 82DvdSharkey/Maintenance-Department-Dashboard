from datetime import date, datetime

import pandas as pd
import pytest

from ingestion.dayforce_loader import parse_dayforce_report
from kpi.dayforce import combine_hours, imported_weekly_hours, resolve_dayforce_technicians
from utils.names import flip_name


def _report() -> pd.DataFrame:
    """Minimal replica of the Dayforce print layout (column positions as in the real export)."""
    rows = [[None] * 46 for _ in range(12)]
    rows[0][1] = "Date Range: 10/4/2026 - 10/10/2026"
    hdr = {5: "Name", 6: "ID", 8: "Date", 11: "Location", 13: "Department", 16: "Job", 20: "In", 23: "Out",
           29: "Mgr", 33: "Emp", 38: "Pay Code", 41: "Pay Category", 43: "Normal", 45: "Other"}
    for c, v in hdr.items():
        rows[2][c] = v

    def put(i, **cells):
        cols = {"name": 5, "id": 6, "date": 8, "job": 16, "in_": 20, "out": 23, "code": 38, "cat": 41,
                "normal": 43, "other": 45}
        for k, v in cells.items():
            rows[i][cols[k]] = v

    put(3, name="Ropp, Nathanial A", id=10052140, cat="OT", normal=17.5)  # employee summary line
    put(4, date="Sun,Oct 4", job="0571 | Technician, Maintenance", code="Shift Prem - Night", cat="PREM", other=8)
    put(5, in_="11:00 pm", out="7:00 am", code="WRK", cat="OT", normal=8)            # Sun night -> CW41
    put(6, date="Mon,Oct 5", job="0395 | Lead, Maintenance", in_="11:00 pm", out="7:00 am", code="WRK",
        cat="REG", normal=8)
    put(7, code="OT 0.5 Memo", cat="OT 0.5 Memo", other=8)
    put(8, date="Tue,Oct 6", job="3965 | Maker, Tool & Die Sr. (Certified)", in_="11:00 pm", out="7:00 am",
        code="WRK", cat="REG", normal=8)
    put(9, date="Wed,Oct 7", job="0571 | Technician, Maintenance", code="PTO - Vacation", cat="Time Off", other=8)
    put(10, name="Agle, Jason E", id=10052019, cat="OT", normal=8)
    put(11, date="Sun,Oct 4", job="0571 | Technician, Maintenance", in_="6:30 am", out="2:30 pm", code="WRK",
        cat="OT", normal=8)                                                              # Sun day -> CW40
    return pd.DataFrame(rows)


def test_parse_entries_and_assign_weeks_by_clock_in():
    entries, start, end = parse_dayforce_report(_report())
    assert (start, end) == (date(2026, 10, 4), date(2026, 10, 10))
    assert len(entries) == 7
    ropp_sun = entries[(entries.employee_id == "10052140") & (entries.pay_code == "WRK")].iloc[0]
    assert ropp_sun["out_time"] == datetime(2026, 10, 5, 7, 0)
    assert ropp_sun["week_key"] == 202641
    agle = entries[entries.employee_id == "10052019"].iloc[0]
    assert agle["week_key"] == 202640


def test_overnight_late_out_segment_dated_next_morning():
    report = _report()
    report.iloc[7, 20], report.iloc[7, 23], report.iloc[7, 38], report.iloc[7, 41], report.iloc[7, 43] = (
        "7:00 am", "8:28 am", "Very Late Out - Approved", "REG", 1.47)
    entries, _, _ = parse_dayforce_report(report)
    late = entries[entries.pay_code == "Very Late Out - Approved"].iloc[0]
    assert late["in_time"] == datetime(2026, 10, 6, 7, 0)
    assert late["week_key"] == 202641


def test_third_shift_starting_2230_sunday_counts_in_new_week():
    report = _report()
    report.iloc[11, 20], report.iloc[11, 23] = "10:30 pm", "6:30 am"
    entries, _, _ = parse_dayforce_report(report)
    assert entries[entries.employee_id == "10052019"].iloc[0]["week_key"] == 202641


@pytest.mark.parametrize(("clock_in", "expected_week"), [("9:29 pm", 202640), ("9:30 pm", 202641),
                                                           ("9:31 pm", 202641)])
def test_sunday_clock_in_uses_930_pm_week_boundary(clock_in, expected_week):
    report = _report()
    report.iloc[11, 20], report.iloc[11, 23] = clock_in, "11:00 pm"
    entries, _, _ = parse_dayforce_report(report)
    assert entries[entries.employee_id == "10052019"].iloc[0]["week_key"] == expected_week


def test_weekly_hours_count_reg_ot_lead_but_not_tool_die_premium_or_pto():
    entries, start, end = parse_dayforce_report(_report())
    entries = entries.assign(report_start=start, report_end=end)
    entries["technician_id"] = pd.array([1] * 6 + [2], dtype="Int64")
    weekly = imported_weekly_hours(entries, ["Tool & Die"]).set_index(["technician_id", "week_key"])
    ropp = weekly.loc[(1, 202641)]
    assert ropp["overtime_hours"] == pytest.approx(8.0)   # Sunday night OT
    assert ropp["regular_hours"] == pytest.approx(8.0)    # Lead job counts; Tool & Die excluded
    assert ropp["pto_hours"] == pytest.approx(8.0)
    assert ropp["total_hours_worked"] == pytest.approx(16.0)
    # CW40 only has a Sunday fragment in this report, so it is not used until its Mon-Sat is imported.
    assert (2, 202640) not in weekly.index


def test_partial_week_used_once_previous_report_imported():
    entries, start, end = parse_dayforce_report(_report())
    entries = entries.assign(report_start=start, report_end=end, technician_id=pd.array([1] * 6 + [2], dtype="Int64"))
    earlier = pd.DataFrame({"employee_id": ["10052019"], "employee_name": ["Agle, Jason E"], "job": ["Technician"],
                            "pay_category": ["REG"], "normal_hours": [32.0], "other_hours": [0.0],
                            "week_key": [202640], "report_start": [date(2026, 9, 27)],
                            "report_end": [date(2026, 10, 3)], "technician_id": pd.array([2], dtype="Int64")})
    weekly = imported_weekly_hours(pd.concat([entries, earlier], ignore_index=True), ["Tool & Die"])
    agle = weekly.set_index(["technician_id", "week_key"]).loc[(2, 202640)]
    assert agle["total_hours_worked"] == pytest.approx(40.0)  # 32 Mon-Sat + 8 Sunday day shift


def test_resolution_by_id_then_flipped_name():
    techs = pd.DataFrame({"technician_id": [1, 2], "technician_name": ["Nate Ropp", "Jason Agle"],
                          "dayforce_id": ["10052140", None], "dayforce_name": [None, None],
                          "fiix_name": [None, None], "ignition_name": [None, None]})
    entries = pd.DataFrame({"employee_id": ["10052140", "10052019", "999"],
                            "employee_name": ["Ropp, Nathanial A", "Agle, Jason E", "Unknown, Person"]})
    ids = resolve_dayforce_technicians(entries, techs, pd.DataFrame(columns=["source", "alias", "technician_id"]))
    assert ids.tolist()[:2] == [1, 2] and pd.isna(ids.iloc[2])
    assert flip_name("Joshua, Michael T Jr") == "Michael Joshua"


def test_manual_entry_overrides_import():
    manual = pd.DataFrame({"technician_id": pd.array([1], dtype="Int64"), "week_key": pd.array([202641], dtype="Int64"),
                           "total_hours_worked": [40.0]})
    imported = pd.DataFrame({"technician_id": [1, 2], "week_key": [202641, 202641], "regular_hours": [30.0, 40.0],
                             "overtime_hours": [0.0, 8.0], "pto_hours": [0.0, 0.0], "total_hours_worked": [30.0, 48.0]})
    combined = combine_hours(manual, imported).set_index("technician_id")
    assert combined.loc[1, "total_hours_worked"] == 40.0 and combined.loc[1, "source"] == "Manual entry"
    assert combined.loc[2, "total_hours_worked"] == 48.0 and combined.loc[2, "source"] == "Dayforce import"
