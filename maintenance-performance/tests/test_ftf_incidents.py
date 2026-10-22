from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from kpi.ftf_incidents import (
    STATUS_FAILED,
    STATUS_NEEDS_CLASSIFICATION,
    STATUS_PENDING,
    STATUS_SUCCESS,
    STATUS_UNRESOLVED,
    evaluate_incident,
    incident_match_key,
    normalize_tl,
    pooled_four_week_baseline,
    potential_repeat,
    summarize_incidents,
    verification_deadline,
)


NOW = datetime(2026, 10, 20, 12)
COVERAGE = {"fiix": date(2026, 11, 1), "ignition": date(2026, 11, 1)}


def incident(**overrides):
    row = {
        "asset_tl": " 000123 ", "component": "sensor A", "symptom": "No conveyance",
        "failure_mode": "sensor open circuit", "intervention_start": datetime(2026, 10, 1, 8),
        "intervention_end": datetime(2026, 10, 1, 10), "return_to_service": datetime(2026, 10, 1, 10),
        "return_documented": True, "verification_method": "Calendar days", "verification_value": 7,
        "manual_status": "Needs classification", "cohort_week": 202640,
    }
    row.update(overrides)
    row["verification_deadline"] = verification_deadline(row["return_to_service"], row["verification_method"],
                                                         row["verification_value"])
    return row


def test_tl_normalization_strips_whitespace_case_preserves_leading_zero_and_punctuation():
    assert normalize_tl("  00a-01/x ") == "00a-01/x"
    assert incident_match_key("00123", " Sensor A ", "Open Circuit") == (
        "00123", "sensor a", "open circuit")


def test_different_tl_under_same_parent_never_suggest_repeat():
    first = {"asset_tl": "000123", "component": "sensor", "failure_mode": "open circuit", "parent_asset": "Line A"}
    second = {"asset_tl": "000124", "component": "sensor", "failure_mode": "open circuit", "parent_asset": "Line A"}
    related, why = potential_repeat(first, second)
    assert not related and "Different individual asset TL#" in why


def test_same_tl_same_component_and_mode_suggests_reviewer_confirmation_only():
    related, why = potential_repeat(
        {"asset_tl": " 00A ", "component": "sensor", "failure_mode": "wire open"},
        {"asset_tl": "00a", "component": "SENSOR", "failure_mode": "WIRE OPEN"})
    assert related and "reviewer confirmation required" in why


def test_unrelated_failure_mode_on_same_tl_is_not_auto_related():
    related, why = potential_repeat(
        {"asset_tl": "001", "component": "sensor", "failure_mode": "open circuit"},
        {"asset_tl": "001", "component": "nozzle", "failure_mode": "blocked"})
    assert not related and "Different failure modes" in why


def test_missing_tl_requires_mapping_and_is_not_matched_by_parent_or_area():
    related, why = potential_repeat(
        {"asset_tl": None, "parent_asset": "Line A", "failure_mode": "jam"},
        {"asset_tl": "001", "parent_asset": "Line A", "failure_mode": "jam"})
    assert not related and "Missing individual asset TL#" in why


def test_same_tl_changed_diagnosis_does_not_force_new_identity_and_confirmed_repeat_fails():
    original = incident(failure_mode="sensor failure", root_cause="suspected sensor")
    later = incident(incident_id="later", failure_mode="damaged wiring", root_cause="confirmed wiring")
    assert normalize_tl(original["asset_tl"]) == normalize_tl(later["asset_tl"])
    result = evaluate_incident(original, NOW, COVERAGE, confirmed_repeat=True)
    assert result["status"] == STATUS_FAILED and result["assessed"]


def test_multiple_records_for_continuous_intervention_do_not_create_failed_repeat_by_themselves():
    result = evaluate_incident(incident(), NOW, COVERAGE)
    assert result["status"] == STATUS_SUCCESS and result["success"]


def test_pending_verification_and_incomplete_coverage_cannot_finalize_success():
    row = incident(return_to_service=datetime(2026, 10, 19))
    row["verification_deadline"] = verification_deadline(row["return_to_service"], "Calendar days", 7)
    assert evaluate_incident(row, NOW, COVERAGE)["status"] == STATUS_PENDING
    assert not evaluate_incident(row, NOW, COVERAGE)["assessed"]
    row = incident()
    result = evaluate_incident(row, NOW, {"fiix": date(2026, 10, 5), "ignition": date(2026, 11, 1)})
    assert result["status"] == STATUS_PENDING and not result["assessed"]


def test_unresolved_repeat_review_blocks_finalized_success():
    result = evaluate_incident(incident(), NOW, COVERAGE, unresolved_review=True)

    assert result["status"] == STATUS_UNRESOLVED
    assert not result["assessed"] and not result["success"]


def test_missing_failure_details_or_undocumented_return_cannot_silently_succeed():
    assert evaluate_incident(incident(failure_mode=None), NOW, COVERAGE)["status"] == STATUS_NEEDS_CLASSIFICATION
    assert evaluate_incident(incident(return_documented=False), NOW, COVERAGE)["status"] == STATUS_NEEDS_CLASSIFICATION


def test_non_calendar_verification_has_no_fabricated_deadline():
    assert verification_deadline(datetime(2026, 1, 1), "Operating hours", 100) is None


def test_distinct_assessed_incidents_pool_counts_and_return_85_percent():
    frame = pd.DataFrame({"assessed": [True] * 40, "success": [True] * 34 + [False] * 6,
                          "cohort_week": [202641] * 40})
    assert summarize_incidents(frame) == {"assessed": 40, "successes": 34, "ftf_pct": 85.0}


def test_same_parent_and_similar_fault_on_different_tl_numbers_are_separate_assets():
    nozzle = {"asset_tl": "000120", "parent_asset": "Conveyor Line 1",
              "component": "sensor", "failure_mode": "sensor open circuit"}
    other_asset = {"asset_tl": "000121", "parent_asset": "Conveyor Line 1",
                   "component": "sensor", "failure_mode": "sensor open circuit"}
    related, why = potential_repeat(nozzle, other_asset)
    assert not related
    assert "Different individual asset TL#" in why


def test_four_week_baseline_pools_counts_and_zero_denominator_is_na():
    frame = pd.DataFrame({"assessed": [True] * 5, "success": [True, False, True, True, False],
                          "cohort_week": [202637, 202638, 202639, 202640, 202641]})
    result = pooled_four_week_baseline(frame, 202641)
    assert result["weeks"] == [202637, 202638, 202639, 202640]
    assert (result["successes"], result["assessed"], result["ftf_pct"]) == (3, 4, 75.0)
    empty = pooled_four_week_baseline(frame.iloc[0:0], 202641)
    assert empty["ftf_pct"] is None


def test_outside_verification_window_does_not_change_window_result_without_relationship_review():
    row = incident()
    later = row["verification_deadline"] + timedelta(seconds=1)
    assert later > row["verification_deadline"]
    assert evaluate_incident(row, NOW, COVERAGE)["status"] == STATUS_SUCCESS


def test_three_reviewed_followup_wos_are_one_failed_incident_and_import_is_idempotent(tmp_path):
    from database.database import create_db_engine, init_db
    from database import ftf_repository as repo
    from database.models import FiixWorkOrder

    engine = create_db_engine(tmp_path / "ftf_review.db")
    init_db(engine)
    from database import repository as base_repo
    technician_id = base_repo.create_technician(engine, "Test Technician", active=True,
                                                dayforce_id="DF-1", crew="SMT", shift="1st Shift")
    starts = [datetime(2026, 10, 1, 8), datetime(2026, 10, 2, 8),
              datetime(2026, 10, 3, 8), datetime(2026, 10, 4, 8)]
    rows = pd.DataFrame({
        "wo": ["original", "followup-1", "followup-2", "followup-3"],
        "wo_type": ["Reactive"] * 4, "tl": ["000123"] * 4, "task_description": ["Sensor failure"] * 4,
        "date_created": starts, "date_completed": [s + timedelta(hours=1) for s in starts],
        "technician_id": [technician_id] * 4, "shift": ["1st Shift"] * 4,
    })
    with engine.begin() as conn:
        conn.execute(FiixWorkOrder.__table__.insert(), [{"wo": f"db-{i}", "date_created": starts[i],
                                                        "tl": "000123"} for i in range(4)])
    assert repo.sync_ftf_fiix_incidents(engine, rows.iloc[:1], {technician_id}) == 1
    assert repo.sync_ftf_fiix_incidents(engine, rows.iloc[:1], {technician_id}) == 0
    source_map = repo.load_ftf_sources(engine).set_index("source_key").incident_id
    original_id = str(source_map["original"])
    repo.save_ftf_incident(engine, original_id, {
        "asset_tl": "000123", "symptom": "Sensor failure", "failure_mode": "sensor open circuit",
        "intervention_start": starts[0], "intervention_end": starts[0] + timedelta(hours=1),
        "return_to_service": datetime(2026, 10, 1, 10), "return_documented": True,
        "verification_method": "Calendar days", "verification_value": 7,
    }, "Reviewer", "Verified the initial repair and return-to-service evidence")
    assert repo.sync_ftf_fiix_incidents(engine, rows.iloc[1:], {technician_id}) == 3
    source_map = repo.load_ftf_sources(engine).set_index("source_key").incident_id
    for key in ("followup-1", "followup-2", "followup-3"):
        repo.save_ftf_relationship(engine, original_id, "fiix", key, str(source_map[key]),
                                   "Confirmed related repeat", "Reviewer", "Same unresolved sensor failure")

    assert repo.sync_ftf_fiix_incidents(engine, rows, {technician_id}) == 0
    final_incidents = repo.load_ftf_incidents(engine)
    anchor = final_incidents[final_incidents.incident_id == original_id].iloc[0].to_dict()
    result = evaluate_incident(anchor, NOW, COVERAGE, confirmed_repeat=True)
    linked = repo.load_ftf_sources(engine)
    reviews = repo.load_ftf_relationships(engine)
    assert len(final_incidents) == 4
    assert int((linked.incident_id.astype(str) == original_id).sum()) == 4
    assert anchor["failure_mode"] == "sensor open circuit"
    assert anchor["cohort_week"] == 202640
    assert len(reviews) == 3 and reviews.decision.eq("Confirmed related repeat").all()
    assert result["status"] == STATUS_FAILED and result["assessed"]
    outcomes = []
    for row in final_incidents.to_dict("records"):
        is_anchor = row["incident_id"] == original_id
        state = evaluate_incident(row, NOW, COVERAGE, confirmed_repeat=is_anchor)
        outcomes.append(state)
    assert sum(state["assessed"] for state in outcomes) == 1
    assert sum(state["success"] for state in outcomes) == 0


def test_ignition_source_detail_joins_by_integer_call_id(tmp_path):
    from database.database import create_db_engine, init_db
    from database import ftf_repository as repo
    from database.models import IgnitionCall

    engine = create_db_engine(tmp_path / "ftf_ignition_source.db")
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(IgnitionCall.__table__.insert().values(
            id=42, source_file="ignition.xlsx", call_time=datetime(2026, 10, 1, 8),
            event_description="Sensor reset", ign_machine_name="MX-42", ign_area_name="Final Assembly"))
    incident_id = repo.create_ignition_incident(
        engine, {"id": 42, "call_time": datetime(2026, 10, 1, 8), "event_description": "Sensor reset"})

    source = repo.load_ftf_sources(engine, incident_ids=[incident_id]).iloc[0]

    assert source["source_key"] == "42"
    assert source["source_file"] == "ignition.xlsx"
    assert source["ignition_machine"] == "MX-42"
    assert source["ignition_area"] == "Final Assembly"


def test_reviewed_cross_source_same_intervention_keeps_one_reporting_incident(tmp_path):
    from database.database import create_db_engine, init_db
    from database import ftf_repository as repo

    engine = create_db_engine(tmp_path / "ftf_cross_source.db")
    init_db(engine)
    event_time = datetime(2026, 10, 1, 8)
    fiix_rows = pd.DataFrame({
        "wo": ["WO-1"], "wo_type": ["Reactive"], "tl": ["000123"],
        "task_description": ["Sensor reset"], "date_created": [event_time],
        "date_completed": [event_time + timedelta(hours=1)],
    })
    assert repo.sync_ftf_fiix_incidents(engine, fiix_rows) == 1
    anchor_id = repo.load_ftf_sources(engine, source="fiix").iloc[0]["incident_id"]
    ignition_id = repo.create_ignition_incident(
        engine, {"id": 42, "call_time": event_time, "event_description": "Sensor reset"},
        asset_tl="000123")

    repo.save_ftf_relationship(engine, str(anchor_id), "ignition", "42", ignition_id,
                               "Same initial intervention", "Reviewer", "Both sources describe the same repair")

    incidents = repo.load_ftf_incidents(engine)
    merged_sources = repo.load_ftf_sources(engine, incident_ids=[str(anchor_id)])
    excluded = incidents[incidents.manual_status == "Excluded"]
    assert len(incidents) == 2
    assert len(excluded) == 1
    assert excluded.iloc[0]["incident_id"] == ignition_id
    assert set(merged_sources.source) == {"fiix", "ignition"}
    assert merged_sources.incident_id.astype(str).nunique() == 1


def test_manual_shared_team_incident_has_one_team_owner_and_one_count(tmp_path):
    from database.database import create_db_engine, init_db
    from database import ftf_repository as repo

    engine = create_db_engine(tmp_path / "ftf_team_owner.db")
    init_db(engine)
    incident_id = repo.create_manual_ftf_incident(engine, {
        "asset_tl": "000123", "owner_type": "Repair team", "repair_team": "Final Assembly response team",
        "technician_id": 17, "initial_shift": "1st Shift", "department_eligible": True,
    }, "Reviewer", "Shared troubleshooting effort")

    incidents = repo.load_ftf_incidents(engine)
    owner = incidents.iloc[0]
    summary = summarize_incidents(incidents.assign(assessed=True, success=False))

    assert len(incidents) == 1 and owner.incident_id == incident_id
    assert owner.owner_type == "Repair team" and owner.repair_team == "Final Assembly response team"
    assert pd.isna(owner.technician_id) and owner.initial_shift == "1st Shift"
    assert summary["assessed"] == 1 and summary["successes"] == 0
