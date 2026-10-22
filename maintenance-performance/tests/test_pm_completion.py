import pandas as pd
import pytest

from kpi.pm_completion import (
    completed_pm_rows, pm_area_summary, pm_completion_pct, pm_series, technician_pm_peer_share,
)


def test_9_closed_of_10_open_is_90_pct():
    assert pm_completion_pct(9, 10) == pytest.approx(90.0)


def test_no_percentage_without_open_pms():
    assert pm_completion_pct(0, 0) is None
    assert pm_completion_pct(None, None) is None
    assert pm_completion_pct(5, None) is None
    pct = pm_series(pd.Series([9.0, None]), pd.Series([10.0, None]))
    assert pct.iloc[0] == pytest.approx(90.0) and pd.isna(pct.iloc[1])


def test_area_completion_is_closed_over_open():
    # CW41 entries: FA 33 of 36 open closed, SMT 68 of 68 open closed.
    status = pd.DataFrame({"week_key": [202641, 202641], "area": ["Final Assembly", "SMT"],
                           "pm_open": [36.0, 68.0], "pm_closed": [33.0, 68.0]})
    by_area = pm_area_summary(status, ["area"]).set_index("area")
    assert by_area.loc["Final Assembly", "pm_completion_pct"] == pytest.approx(91.6667, rel=1e-4)
    assert by_area.loc["Final Assembly", "pm_remaining"] == 3
    assert by_area.loc["SMT", "pm_completion_pct"] == pytest.approx(100.0)
    assert by_area.loc["SMT", "pm_remaining"] == 0


def test_completed_pm_rows_requires_technician_and_excludes_duplicates():
    fiix = pd.DataFrame({
        "wo": ["1", "2", "3", "4"],
        "is_pm": [True, True, True, False],
        "technician_id": pd.array([1, None, 1, 1], dtype="Int64"),
        "date_completed": pd.to_datetime(["2026-10-01"] * 4),
        "status": ["Closed", "Closed", "ZDNU-Closed Duplicate-Admin Only", "Closed"],
    })
    assert list(completed_pm_rows(fiix)["wo"]) == ["1"]


def test_only_closed_pm_wos_count_for_technicians():
    fiix = pd.DataFrame({
        "wo": ["1", "2", "3"],
        "is_pm": [True, True, True],
        "technician_id": pd.array([1, 1, 1], dtype="Int64"),
        "date_completed": pd.to_datetime(["2026-10-01", "2026-10-01", None]),
        "status": ["Closed", "Open", "Work In Progress"],
    })
    assert list(completed_pm_rows(fiix)["wo"]) == ["1"]


def _peer_roster():
    return pd.DataFrame({
        "technician_id": range(1, 10),
        "shift": ["1st"] * 6 + ["2nd", None, "1st"],
        "crew": ["Assembly"] * 5 + ["SMT", "Assembly", "Assembly", ""],
        "active": [True, True, True, False, True, True, True, True, True],
        "is_lead": [False, False, False, False, True, False, False, False, False],
        "in_department": [True] * 9,
    })


def _peer_pms():
    ids = [1] * 2 + [2] * 6 + [4] * 20 + [5] * 30 + [6] * 40 + [7] * 50
    return pd.DataFrame({"technician_id": ids, "wo": [str(i) for i in range(len(ids))]})


def test_peer_share_requires_both_shift_and_crew_and_includes_self():
    result = technician_pm_peer_share(_peer_pms(), _peer_roster()).set_index("technician_id")
    assert result.loc[1, "pm_peer_share_pct"] == pytest.approx(25.0)
    assert result.loc[2, "pm_peer_share_pct"] == pytest.approx(75.0)
    assert result.loc[3, "pm_peer_share_pct"] == pytest.approx(0.0)
    assert result.loc[1, "pm_peer_group_completed"] == 8
    assert result.loc[1, "pm_peer_count"] == 2  # Includes the peer who completed zero PMs.
    assert result.loc[[1, 2, 3], "pm_peer_share_pct"].sum() == pytest.approx(100.0)


@pytest.mark.parametrize("tech,explanation", [
    (4, "active, non-lead"), (5, "active, non-lead"),
    (6, "no other active"), (7, "no other active"),
    (8, "both be assigned"), (9, "both be assigned"),
])
def test_peer_share_explains_ineligible_technicians_and_missing_peers(tech, explanation):
    row = technician_pm_peer_share(_peer_pms(), _peer_roster()).set_index("technician_id").loc[tech]
    assert pd.isna(row["pm_peer_share_pct"])
    assert explanation in row["pm_peer_reason"]


def test_zero_group_completions_are_na_not_zero_or_infinity():
    result = technician_pm_peer_share(pd.DataFrame(columns=["technician_id", "wo"]),
                                      _peer_roster()).set_index("technician_id")
    assert result.loc[1, "pm_peer_count"] == 2
    assert pd.isna(result.loc[1, "pm_peer_share_pct"])
    assert "no closed PMs" in result.loc[1, "pm_peer_reason"]


def test_peer_designations_are_trimmed_and_case_insensitive_without_fuzzy_matching():
    roster = _peer_roster()
    roster.loc[1, ["shift", "crew"]] = [" 1ST ", " assembly "]
    result = technician_pm_peer_share(_peer_pms(), roster).set_index("technician_id")
    assert result.loc[1, "pm_peer_share_pct"] == pytest.approx(25.0)


def test_non_department_technicians_are_not_peers():
    roster = _peer_roster()
    roster.loc[1, "in_department"] = False
    result = technician_pm_peer_share(_peer_pms(), roster).set_index("technician_id")
    assert result.loc[1, "pm_peer_group_completed"] == 2
    assert result.loc[1, "pm_peer_count"] == 1
    assert result.loc[1, "pm_peer_share_pct"] == pytest.approx(100.0)
    assert pd.isna(result.loc[2, "pm_peer_share_pct"])
