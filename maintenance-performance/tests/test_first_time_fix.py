import pandas as pd
import pytest

from kpi.first_time_fix import (
    FTFConfig,
    averaged_ftf,
    evaluate_first_time_fix,
    ftf_pct,
    ftf_summary,
    reactive_wo_types,
)

CONFIG = FTFConfig(qualifying_types=["Reactive", "Reactive-Breakdown"], window_days=7, asset_rule="asset")


def _wo(wo, asset, created, completed, wo_type="Reactive", status="Closed", parent=None, tech="Tech A", tl=None):
    return {"wo": wo, "tl": tl, "asset": asset, "parent_asset": parent, "wo_type": wo_type, "status": status,
            "completed_by": tech,
            "date_created": pd.Timestamp(created), "date_completed": pd.Timestamp(completed)}


def _evaluate(rows, config=CONFIG):
    return evaluate_first_time_fix(pd.DataFrame(rows), config).set_index("wo")


def test_no_repeat_failure_is_success():
    ev = _evaluate([_wo("1", "Press 1", "2026-10-01 08:00", "2026-10-01 09:00"),
                    _wo("2", "Press 2", "2026-10-02 08:00", "2026-10-02 09:00")])
    assert ev["ftf_success"].all()


def test_repeat_inside_window_is_failure():
    ev = _evaluate([_wo("1", "Press 1", "2026-10-01 08:00", "2026-10-01 09:00"),
                    _wo("2", "Press 1", "2026-10-05 08:00", "2026-10-05 09:00")])
    assert not ev.loc["1", "ftf_success"]
    assert ev.loc["1", "repeat_wo"] == "2"
    assert ev.loc["2", "ftf_success"]


def test_evaluator_preserves_original_dataframe_indexes():
    fiix = pd.DataFrame([
        _wo("1", "Press 1", "2026-10-01 08:00", "2026-10-01 09:00"),
        _wo("2", "Press 1", "2026-10-05 08:00", "2026-10-05 09:00"),
    ], index=[10, 20])

    evaluated = evaluate_first_time_fix(fiix, CONFIG)

    assert evaluated.index.tolist() == [10, 20]
    assert evaluated.loc[10, "repeat_wo"] == "2"


def test_repeat_outside_window_is_success():
    ev = _evaluate([_wo("1", "Press 1", "2026-10-01 08:00", "2026-10-01 09:00"),
                    _wo("2", "Press 1", "2026-10-09 10:00", "2026-10-09 11:00")])
    assert ev.loc["1", "ftf_success"]


def test_window_is_configurable():
    rows = [_wo("1", "Press 1", "2026-10-01 08:00", "2026-10-01 09:00"),
            _wo("2", "Press 1", "2026-10-05 08:00", "2026-10-05 09:00")]
    ev = _evaluate(rows, FTFConfig(qualifying_types=["Reactive"], window_days=2, asset_rule="asset"))
    assert ev.loc["1", "ftf_success"]


def test_non_qualifying_types_and_duplicates_ignored():
    ev = _evaluate([_wo("1", "Press 1", "2026-10-01 08:00", "2026-10-01 09:00"),
                    _wo("2", "Press 1", "2026-10-02 08:00", "2026-10-02 09:00", wo_type="Preventative (PM)"),
                    _wo("3", "Press 1", "2026-10-03 08:00", "2026-10-03 09:00",
                        status="ZDNU-Closed Duplicate-Admin Only")])
    assert list(ev.index) == ["1"]
    assert ev.loc["1", "ftf_success"]


def test_parent_asset_fallback():
    rows = [_wo("1", None, "2026-10-01 08:00", "2026-10-01 09:00", parent="Line A"),
            _wo("2", None, "2026-10-02 08:00", "2026-10-02 09:00", parent="Line A")]
    assert not _evaluate(rows, FTFConfig(qualifying_types=["Reactive"], asset_rule="asset_then_parent")).loc["1", "ftf_success"]
    assert _evaluate(rows).empty


def test_parent_asset_rule_matches_across_stations():
    rows = [_wo("1", "UL1 Op100", "2026-10-01 08:00", "2026-10-01 09:00", parent="Ultralign1"),
            _wo("2", "UL1 Op230", "2026-10-03 08:00", "2026-10-03 09:00", parent="Ultralign1"),
            _wo("3", "UL2 Op100", "2026-10-01 08:00", "2026-10-01 09:00", parent="Ultralign2"),
            _wo("4", "Plant 4", "2026-10-01 08:00", "2026-10-01 09:00", parent=None)]
    ev = _evaluate(rows, FTFConfig(qualifying_types=["Reactive"], asset_rule="parent_asset"))
    assert list(ev.index) == ["1", "2", "3"]
    assert not ev.loc["1", "ftf_success"] and ev.loc["1", "repeat_wo"] == "2"
    assert ev.loc["3", "ftf_success"]
    assert _evaluate(rows, FTFConfig(qualifying_types=["Reactive"], asset_rule="asset")).loc["1", "ftf_success"]


def test_default_matches_exact_asset_number_only():
    rows = [_wo("1", "UL1 Op100", "2026-10-01 08:00", "2026-10-01 09:00", tl="8671"),
            _wo("2", "UL1 Op100 renamed", "2026-10-02 08:00", "2026-10-02 09:00", tl=" 8671 "),
            _wo("3", "UL1 Op230", "2026-10-01 08:00", "2026-10-01 09:00", tl="86710"),
            _wo("4", "Hotline", "2026-10-01 08:00", "2026-10-01 09:00", tl="Ignition_HotlineCall"),
            _wo("5", "Hotline", "2026-10-01 10:00", "2026-10-01 11:00", tl="Ignition_HotlineCall"),
            _wo("6", "Plant 4", "2026-10-01 08:00", "2026-10-01 09:00", tl="PLANT4")]
    ev = _evaluate(rows, FTFConfig(qualifying_types=["Reactive"]))
    assert list(ev.index) == ["1", "2", "3"]
    assert ev.loc["1", "repeat_wo"] == "2"
    assert ev.loc["3", "ftf_success"]


def test_reactive_wo_types():
    types = ["Reactive", "Reactive-Breakdown", "Reactive-O.E.", "Corrective", "Preventative (PM)", None]
    assert reactive_wo_types(types) == ["Reactive", "Reactive-Breakdown", "Reactive-O.E."]


def test_only_technician_completed_wos_qualify():
    rows = [_wo("1", "A", "2026-10-01 08:00", "2026-10-01 09:00", tl="100"),
            _wo("2", "A", "2026-10-02 08:00", "2026-10-02 09:00", tl="100", tech=None),
            _wo("3", "A", "2026-10-03 08:00", "2026-10-03 09:00", tl="100", tech="  ")]
    ev = _evaluate(rows, FTFConfig(qualifying_types=["Reactive"]))
    assert list(ev.index) == ["1"]
    assert ev.loc["1", "ftf_success"]
    ev_all = _evaluate(rows, FTFConfig(qualifying_types=["Reactive"], require_technician=False))
    assert not ev_all.loc["1", "ftf_success"]


def test_ftf_rate():
    ev = evaluate_first_time_fix(pd.DataFrame([
        _wo("1", "Press 1", "2026-10-01 08:00", "2026-10-01 09:00"),
        _wo("2", "Press 1", "2026-10-03 08:00", "2026-10-03 09:00"),
        _wo("3", "Press 2", "2026-10-03 08:00", "2026-10-03 09:00"),
        _wo("4", "Press 3", "2026-10-03 08:00", "2026-10-03 09:00"),
    ]), CONFIG)
    summary = ftf_summary(ev, [])
    assert summary["ftf_pct"].iloc[0] == pytest.approx(75.0)
    assert ftf_pct(0, 0) is None


def test_ftf_averages_technicians_then_shifts():
    # Tech 1 (1st): 1/1 = 100%; Tech 2 (1st): 1/2 = 50%; Tech 3 (2nd): 0/1 = 0%; unassigned WO ignored.
    ev = pd.DataFrame({
        "wo": ["a", "b", "c", "d", "e"],
        "technician_id": pd.array([1, 2, 2, 3, None], dtype="Int64"),
        "shift": ["1st", "1st", "1st", "2nd", None],
        "ftf_success": [True, True, False, False, False],
    })
    tech = averaged_ftf(ev, ["technician_id"]).set_index("technician_id")["ftf_pct"]
    assert tech[1] == pytest.approx(100.0) and tech[2] == pytest.approx(50.0) and tech[3] == pytest.approx(0.0)
    shift = averaged_ftf(ev, ["shift"]).set_index("shift")["ftf_pct"]
    assert shift["1st"] == pytest.approx(75.0)  # weighted would be 66.7%
    assert shift["2nd"] == pytest.approx(0.0)
    assert averaged_ftf(ev, [])["ftf_pct"].iloc[0] == pytest.approx(37.5)
