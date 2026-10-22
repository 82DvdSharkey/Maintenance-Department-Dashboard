from datetime import datetime

import pandas as pd
import pytest

from ingestion.fiix_loader import clean_fiix
from kpi.scorecard import KPIInputs, scorecard, weekly_trend
from kpi.targets import evaluate_status
from utils.dates import parse_filename_week, production_week, shift_week, week_bounds
from utils.names import build_alias_index


@pytest.mark.parametrize("name,expected", [
    ("FiixCW40.csv", (None, 40)), ("26CW1.xls", (2026, 1)), ("IGCW36.2.xlsx", (None, 36)),
    ("CW-10-Rev.1 (80).csv", (None, 10)), ("30CW.xls", (None, 30)), ("01-2024.xls", (None, None)),
])
def test_filename_week(name, expected):
    assert parse_filename_week(name) == expected


def test_production_week_starts_sunday_2300():
    assert production_week(datetime(2026, 9, 27, 22, 59)) == (2026, 39)
    assert production_week(datetime(2026, 9, 27, 23, 0)) == (2026, 40)
    assert week_bounds(2026, 40)[0] == datetime(2026, 9, 27, 23, 0)
    assert shift_week(202601, -1) == 202552
    assert shift_week(202101, -1) == 202053  # 2020 has 53 ISO weeks


def test_fiix_cleaning_handles_header_variants_and_missing_tech():
    raw = pd.DataFrame({
        "WO#": ["1", "2", "2"], "FF Group": ["", "", ""], " TL# ": ["", "", ""], "Parent Asset": ["", "", ""],
        "Asset": ["A", "B", "B"], "Task Description": ["x", "y", "y"], "Task Notes Completion": ["", "", ""],
        "Completion Notes": ["", "", ""], "Time Spent": ["1.5", "0:30", "1"], "Completed By": ["  john  SMITH", "", ""],
        "Date Created": ["10/1/2026 8:00"] * 3, "Date Completed": ["10/1/2026 9:00"] * 3,
        "Status": ["Closed"] * 3, "WO Type": ["Reactive"] * 3,
    })
    result = clean_fiix(raw)
    df = result.frame.set_index("wo")
    assert list(df.index) == ["1", "2"]
    assert df.loc["1", "completed_by"] == "john SMITH"
    assert pd.isna(df.loc["2", "completed_by"])
    assert df.loc["2", "time_spent_hours"] == pytest.approx(0.5)
    assert any(i.issue_type == "duplicate_wo_in_file" for i in result.issues)


def test_fiix_renamed_headers_mapped_by_position():
    cols = ["WO#", "FF Group", "TL#", "Parent Asset", "Asset", "Task Description", "Task Notes Completion",
            "Completion Notes", "Time Spent on Reactive W/Os", "Completed By", "Date Created", "Date Completed",
            "Status", "26CW07 Reactive W/Os", "", "Row Labels"]
    row = ["1", "FA", "7", "Line", "A", "x", "", "", "0.5", "Tech", "2/10/2026 8:00", "2/10/2026 9:00",
           "Closed", "Reactive", "", "204B"]
    blank = ["", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Grand Total"]
    result = clean_fiix(pd.DataFrame([row, blank], columns=cols))
    assert list(result.frame["wo_type"]) == ["Reactive"]
    assert result.frame["time_spent_hours"].iloc[0] == pytest.approx(0.5)
    assert not result.issues
    assert any("column position" in w for w in result.warnings)


def test_fiix_unrecognized_layout_rejected():
    from utils.validation import ValidationError
    with pytest.raises(ValidationError):
        clean_fiix(pd.DataFrame({"WO": ["1"], "Asset": ["A"]}))


def test_ambiguous_alias_not_resolved():
    techs = pd.DataFrame({"technician_id": [1, 2], "technician_name": ["John Smith", "Jon Smith"],
                          "fiix_name": ["J Smith", "J Smith"], "ignition_name": [None, None],
                          "dayforce_name": [None, None]})
    index, ambiguous = build_alias_index(techs, pd.DataFrame(columns=["source", "alias", "technician_id"]), "fiix")
    assert "j smith" in ambiguous and "j smith" not in index
    assert index["john smith"] == 1


def test_status_directions():
    assert evaluate_status(85, 80, "higher", 5) == "On Target"
    assert evaluate_status(77, 80, "higher", 5) == "Watch"
    assert evaluate_status(70, 80, "higher", 5) == "Below Target"
    assert evaluate_status(4, 5, "lower", 1) == "On Target"
    assert evaluate_status(5.5, 5, "lower", 1) == "Watch"
    assert evaluate_status(9, 5, "lower", 1) == "Above Target"


def _inputs():
    labor = pd.DataFrame({"technician_id": pd.array([1, 2], dtype="Int64"), "shift": ["1st", "1st"],
                          "week_key": [202640, 202640], "time_spent_hours": [30.0, 10.0], "wo": ["a", "b"]})
    dayforce = pd.DataFrame({"technician_id": pd.array([1, 2], dtype="Int64"), "shift": ["1st", "1st"],
                             "week_key": [202640, 202640], "total_hours_worked": [40.0, 10.0]})
    ftf = pd.DataFrame({"technician_id": pd.array([1, None], dtype="Int64"), "shift": ["1st", None],
                        "week_key": [202640, 202640], "wo": ["a", "c"], "ftf_success": [True, False],
                        "repeat_failure": [False, True]})
    pm_done = pd.DataFrame({"technician_id": pd.array([1] * 9, dtype="Int64"), "shift": ["1st"] * 9,
                            "week_key": [202640] * 9, "wo": [str(i) for i in range(9)]})
    pm_status = pd.DataFrame({"week_key": [202640, 202640], "area": ["Final Assembly", "SMT"],
                              "pm_open": [10.0, 10.0], "pm_closed": [9.0, 7.0]})
    calls = pd.DataFrame({"id": [1, 2, 3, 4], "technician_id": pd.array([1, 2, 2, None], dtype="Int64"),
                          "shift": ["1st", "1st", "1st", None], "week_key": [202640] * 4,
                          "call_duration_min": [10.0, 2.0, 2.0, 6.0], "ack_duration_min": [1.0] * 4,
                          "response_duration_min": [1.0] * 4, "repair_duration_min": [10.0, 2.0, 2.0, 6.0]})
    return KPIInputs(fiix=labor, labor_rows=labor, dayforce=dayforce, ftf=ftf, pm_done=pm_done,
                     pm_status=pm_status, calls=calls)


def test_scorecard_levels():
    inputs = _inputs()
    shift = scorecard(inputs, ["shift"]).iloc[0]
    assert shift["labor_efficiency_pct"] == pytest.approx(80.0)
    assert pd.isna(shift["pm_completion_pct"])  # PM completion is area/department only
    assert shift["pm_completed"] == 9  # Fiix PM WOs completed (informational)
    assert shift["maintenance_calls"] == 3
    assert shift["avg_repair_duration_min"] == pytest.approx(14.0 / 3)
    dept = scorecard(inputs, []).iloc[0]
    assert dept["pm_completion_pct"] == pytest.approx(80.0)  # (9 + 7) closed / 20 open
    assert dept["maintenance_calls"] == 4  # includes the unassigned call
    assert dept["ftf_pct"] == pytest.approx(100.0)  # only technician-attributed WOs count toward FTF
    tech = scorecard(inputs, ["technician_id"]).set_index("technician_id")
    assert tech.loc[1, "labor_efficiency_pct"] == pytest.approx(75.0)


def test_leads_and_inactive_excluded_from_department_and_shift_labor_only():
    inputs = _inputs()
    inputs.labor_rows = inputs.labor_rows.assign(le_excluded=[False, True])  # tech 2: 10 logged / 10 worked
    inputs.dayforce = inputs.dayforce.assign(le_excluded=[False, True])
    dept = scorecard(inputs, []).iloc[0]
    assert dept["labor_efficiency_pct"] == pytest.approx(75.0)  # 30 / 40, tech 2 left out
    shift = scorecard(inputs, ["shift"]).iloc[0]
    assert shift["labor_efficiency_pct"] == pytest.approx(75.0)
    tech = scorecard(inputs, ["technician_id"]).set_index("technician_id")
    assert tech.loc[2, "labor_efficiency_pct"] == pytest.approx(100.0)  # still shown individually


def test_inactive_technician_kept_until_end_date_week():
    from datetime import date
    from kpi.dataset import labor_excluded, technician_in_scope
    techs = pd.DataFrame({"technician_id": pd.array([1, 2, 3, 4], dtype="Int64"),
                          "active": [True, False, False, True], "is_lead": [False, False, False, True],
                          "end_date": [None, date(2026, 10, 7), None, None]})  # 10/7/2026 is in CW41
    rows = pd.DataFrame({"technician_id": pd.array([1, 2, 2, 3, 4], dtype="Int64"),
                         "week_key": [202642, 202641, 202642, 202641, 202641]})
    assert technician_in_scope(rows, techs).tolist() == [True, True, False, False, True]
    assert labor_excluded(rows, techs).tolist() == [False, False, True, True, True]
    techs["in_department"] = [False, True, True, True]  # tech 1 has no Dayforce records
    assert technician_in_scope(rows, techs).tolist() == [False, True, False, False, True]


def test_department_is_only_dayforce_mapped_technicians():
    from kpi.dataset import department_membership
    techs = pd.DataFrame({"technician_id": [1, 2, 3], "crew": [None, "Test Technician", "  "]})
    assert department_membership(techs, {1}).tolist() == [True, False, False]
    assert department_membership(techs, set()).tolist() == [True, True, True]


def test_smt_cw39_chart_normalization_preserves_source_trend():
    from pages.shift_dashboard import _smooth_smt_spike_for_chart

    trend = pd.DataFrame({"week_key": [202638, 202639, 202640, 202641],
                          "worked_hours": [40.0, 8.0, 40.0, 40.0],
                          "le_logged_hours": [20.0, 34.7, 30.0, 20.0],
                          "labor_efficiency_pct": [50.0, 433.75, 75.0, 50.0],
                          "labor_efficiency_pct_r4": [50.0, 113.5, 95.0, 87.4]})

    adjusted = _smooth_smt_spike_for_chart(trend, 100.0)

    assert adjusted.loc[1, "labor_efficiency_pct"] == pytest.approx(100.0)
    assert adjusted.loc[1, "le_logged_hours"] == pytest.approx(8.0)
    assert adjusted.loc[3, "labor_efficiency_pct_r4"] == pytest.approx(60.9375)
    assert trend.loc[1, "labor_efficiency_pct"] == pytest.approx(433.75)


def test_crew_area_groups_team_leads_with_their_area():
    from kpi.dataset import crew_area_technician_ids
    techs = pd.DataFrame({"technician_id": [1, 2, 3, 4, 5, 6],
                          "crew": ["Final Assembly", "Team Lead Final Assembly", "SMT", "Team Lead SMT",
                                   "Test Technician", None]})
    assert crew_area_technician_ids(techs, "Final Assembly") == [1, 2]
    assert crew_area_technician_ids(techs, "SMT") == [3, 4]
    assert crew_area_technician_ids(techs, "Test") == [5]


def test_weekly_trend_rolling():
    trend = weekly_trend(_inputs(), [202639, 202640])
    assert pd.isna(trend.loc[0, "labor_efficiency_pct"])
    assert trend.loc[1, "labor_efficiency_pct_r4"] == pytest.approx(80.0)
    assert trend.loc[1, "pm_completion_pct"] == pytest.approx(80.0)
    assert pd.isna(trend.loc[0, "pm_completion_pct"])


def test_weekly_trend_pm_completion_kept_separate_by_area():
    trend = weekly_trend(_inputs(), [202639, 202640], pm_areas=["Final Assembly", "SMT", "New Area"])
    assert trend.loc[1, "pm_completion_pct__final_assembly"] == pytest.approx(90.0)
    assert trend.loc[1, "pm_completion_pct__smt"] == pytest.approx(70.0)
    assert trend.loc[1, "pm_completion_pct__smt_r4"] == pytest.approx(70.0)
    assert pd.isna(trend.loc[0, "pm_completion_pct__smt"])
    assert trend["pm_completion_pct__new_area"].isna().all()
