import pandas as pd
import pytest

from kpi.ignition_metrics import (
    filter_maintenance_calls, seconds_to_minutes, summarize_calls, technician_call_peer_share,
)


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


@pytest.fixture
def call_peer_roster():
    return pd.DataFrame({
        "technician_id": range(1, 10),
        "shift": ["1st", " 1ST ", "1st", "2nd", "1st", "1st", "1st", "1st", None],
        "crew": ["SMT", " smt ", "SMT", "SMT", "FA", "SMT", "SMT", "SMT", "SMT"],
        "active": [True] * 6 + [False, True, True],
        "is_lead": [False] * 5 + [True, False, False, False],
        "in_department": [True] * 7 + [False, True],
    })


def test_call_peer_share_uses_only_eligible_same_shift_and_crew_including_self(call_peer_roster):
    ids = [1] * 2 + [2] * 4 + [4, 5, 6, 7, 8, None] * 10
    calls = pd.DataFrame({"id": range(len(ids)), "technician_id": ids})
    result = technician_call_peer_share(calls, call_peer_roster).set_index("technician_id")
    assert result.loc[1, "call_peer_share_pct"] == pytest.approx(100 / 3)
    assert result.loc[2, "call_peer_share_pct"] == pytest.approx(200 / 3)
    assert result.loc[3, "call_peer_share_pct"] == 0
    assert result.loc[1, "call_peer_group_completed"] == 6
    assert result.loc[1, "call_peer_count"] == 2
    assert result.loc[[1, 2, 3], "call_peer_share_pct"].sum() == pytest.approx(100)


@pytest.mark.parametrize("tech,reason", [
    (4, "no other active"), (5, "no other active"),
    (6, "active, non-lead"), (7, "active, non-lead"), (8, "active, non-lead"),
    (9, "both be assigned"),
])
def test_call_peer_share_explains_ineligible_groups(call_peer_roster, tech, reason):
    calls = pd.DataFrame({"id": [1], "technician_id": [1]})
    row = technician_call_peer_share(calls, call_peer_roster).set_index("technician_id").loc[tech]
    assert pd.isna(row["call_peer_share_pct"])
    assert reason in row["call_peer_reason"]


def test_call_peer_share_no_group_calls_is_na(call_peer_roster):
    calls = pd.DataFrame(columns=["id", "technician_id"])
    row = technician_call_peer_share(calls, call_peer_roster).iloc[0]
    assert pd.isna(row["call_peer_share_pct"])
    assert "no maintenance calls" in row["call_peer_reason"]
