import pandas as pd
import pytest

from kpi.labor_efficiency import (
    efficiency_series,
    labor_components,
    labor_efficiency_pct,
    labor_flags,
    total_hours_worked,
)


def test_labor_efficiency_40_over_50_is_80_pct():
    assert labor_efficiency_pct(40, 50) == pytest.approx(80.0)


def test_no_percentage_when_worked_hours_zero_or_missing():
    assert labor_efficiency_pct(10, 0) is None
    assert labor_efficiency_pct(10, None) is None
    assert pd.isna(efficiency_series(pd.Series([10.0]), pd.Series([0.0])).iloc[0])


def test_total_hours_worked_formula():
    assert total_hours_worked(40, 5, 2, 3) == pytest.approx(44.0)
    assert total_hours_worked(40, None, None, None) == pytest.approx(40.0)


def test_weeks_without_dayforce_excluded_from_ratio_numerator():
    labor = pd.DataFrame({"technician_id": [1, 1], "week_key": [202640, 202641], "time_spent_hours": [40.0, 30.0]})
    dayforce = pd.DataFrame({"technician_id": [1], "week_key": [202640], "total_hours_worked": [50.0]})
    comps = labor_components(labor, dayforce, ["technician_id", "week_key"])
    totals = comps[["logged_hours", "le_logged_hours", "worked_hours"]].sum()
    assert totals["logged_hours"] == pytest.approx(70.0)
    assert labor_efficiency_pct(totals["le_logged_hours"], totals["worked_hours"]) == pytest.approx(80.0)


def test_shift_efficiency_pools_hours():
    labor = pd.DataFrame({"technician_id": [1, 2], "shift": ["1st", "1st"], "week_key": [1, 1],
                          "time_spent_hours": [30.0, 10.0]})
    dayforce = pd.DataFrame({"technician_id": [1, 2], "shift": ["1st", "1st"], "week_key": [1, 1],
                             "total_hours_worked": [40.0, 10.0]})
    comps = labor_components(labor, dayforce, ["shift", "week_key"])
    assert labor_efficiency_pct(comps["le_logged_hours"].iloc[0], comps["worked_hours"].iloc[0]) == pytest.approx(80.0)


def test_department_ratio_only_pairs_technicians_with_their_own_dayforce_hours():
    # Tech 3 logged 100 h in Fiix but has no Dayforce hours: shown as logged, not used in the ratio.
    labor = pd.DataFrame({"technician_id": [1, 2, 3], "_all": [1, 1, 1], "week_key": [1, 1, 1],
                          "time_spent_hours": [30.0, 10.0, 100.0]})
    dayforce = pd.DataFrame({"technician_id": [1, 2], "_all": [1, 1], "week_key": [1, 1],
                             "total_hours_worked": [40.0, 10.0]})
    comps = labor_components(labor, dayforce, ["_all", "week_key"]).iloc[0]
    assert comps["logged_hours"] == pytest.approx(140.0)
    assert labor_efficiency_pct(comps["le_logged_hours"], comps["worked_hours"]) == pytest.approx(80.0)


def test_implausible_time_spent_excluded():
    from kpi.labor_efficiency import qualifying_labor_rows
    fiix = pd.DataFrame({"counts_toward_labor": [True, True], "technician_id": pd.array([1, 1], dtype="Int64"),
                         "time_spent_hours": [2.0, 3575.27]})
    assert len(qualifying_labor_rows(fiix, 24)) == 1
    assert len(qualifying_labor_rows(fiix)) == 2


def test_labor_flags():
    summary = pd.DataFrame({"technician": ["A", "B", "C"], "logged_hours": [5.0, 0.0, 70.0],
                            "worked_hours": [None, 40.0, 50.0]})
    flags = labor_flags(summary, warning_pct=120)
    assert set(flags["flag"]) == {"Fiix hours with no Dayforce hours", "Dayforce hours with no Fiix activity",
                                  "Labor efficiency above 120%"}
