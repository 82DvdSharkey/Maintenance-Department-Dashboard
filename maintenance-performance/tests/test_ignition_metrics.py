import pandas as pd
import pytest

from kpi.ignition_metrics import filter_maintenance_calls, seconds_to_minutes, summarize_calls


def test_seconds_conversion():
    assert seconds_to_minutes(600) == pytest.approx(10.0)
    assert seconds_to_minutes(90) == pytest.approx(1.5)


def test_null_and_negative_not_zero():
    s = seconds_to_minutes(pd.Series([60.0, None, -5.0]))
    assert s.iloc[0] == pytest.approx(1.0)
    assert pd.isna(s.iloc[1]) and pd.isna(s.iloc[2])
    assert seconds_to_minutes(None) is None


def test_maintenance_filter_trims_and_ignores_case():
    calls = pd.DataFrame({"call_type": [" Maintenance ", "MAINTENANCE", "Team Leader", "Tester Tech", None]})
    assert len(filter_maintenance_calls(calls)) == 2


def test_averages_ignore_nulls():
    calls = pd.DataFrame({"id": [1, 2], "call_duration_min": [10.0, None], "ack_duration_min": [1.0, 3.0],
                          "response_duration_min": [None, None], "repair_duration_min": [5.0, 5.0]})
    out = summarize_calls(calls).iloc[0]
    assert out["maintenance_calls"] == 2
    assert out["avg_call_duration_min"] == pytest.approx(10.0)
    assert out["avg_ack_duration_min"] == pytest.approx(2.0)
    assert pd.isna(out["avg_response_duration_min"])


def test_shift_average_weighted_by_calls_not_technician_averages():
    # Tech A: 1 call of 10 min; Tech B: 3 calls of 2 min.
    calls = pd.DataFrame({
        "id": [1, 2, 3, 4], "shift": ["1st"] * 4, "technician": ["A", "B", "B", "B"],
        "call_duration_min": [10.0, 2.0, 2.0, 2.0], "ack_duration_min": [1.0] * 4,
        "response_duration_min": [1.0] * 4, "repair_duration_min": [10.0, 2.0, 2.0, 2.0],
    })
    tech_avg = summarize_calls(calls, ["technician"])["avg_repair_duration_min"].mean()
    shift = summarize_calls(calls, ["shift"]).iloc[0]
    dept = summarize_calls(calls).iloc[0]
    assert tech_avg == pytest.approx(6.0)
    assert shift["avg_repair_duration_min"] == pytest.approx(4.0)
    assert dept["avg_repair_duration_min"] == pytest.approx(4.0)
