"""Persistence functions for reviewed, incident-based FTF records."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime

import pandas as pd
from sqlalchemy import select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from database.database import bump_data_version, read_df
from database.models import (
    FTFAssetMapping,
    FTFIncident,
    FTFIncidentSource,
    FTFRelationshipReview,
    FTFReviewAudit,
    FTFSourceCoverage,
)
from kpi.ftf_incidents import cohort_week, normalize_tl, verification_deadline
from utils.names import clean_text, name_key


def load_ftf_incidents(engine: Engine, cohort_weeks: set[int] | None = None) -> pd.DataFrame:
    sql = """SELECT i.*, t.technician_name, 0 AS source_count
        FROM ftf_incidents i LEFT JOIN technicians t ON t.technician_id=i.technician_id
        """
    params = {}
    if cohort_weeks is not None:
        ordered = sorted(cohort_weeks)
        params = {f"wk{i}": value for i, value in enumerate(ordered)}
        placeholders = ", ".join(f":wk{i}" for i in range(len(ordered))) or "NULL"
        sql += f" WHERE i.cohort_week IN ({placeholders}) OR i.manual_status='Open / in progress'"
    sql += " ORDER BY i.cohort_week DESC, i.created_at DESC"
    return read_df(engine, sql, params,
                   ["intervention_start", "intervention_end", "return_to_service", "verification_deadline",
                    "reviewed_at", "created_at", "updated_at", "last_calculated_at"])


def store_ftf_outcomes(engine: Engine, updates: list[dict]) -> None:
    if not updates:
        return
    now = datetime.now()
    with engine.begin() as conn:
        for start in range(0, len(updates), 500):
            batch = updates[start:start + 500]
            stmt = text("UPDATE ftf_incidents SET outcome=:outcome, last_calculated_at=:calculated "
                        "WHERE incident_id=:incident_id")
            conn.execute(stmt, [{"incident_id": r["incident_id"], "outcome": r["outcome"], "calculated": now}
                                for r in batch])


def load_ftf_sources(engine: Engine, incident_ids: list[str] | None = None,
                     source: str | None = None, source_keys: list[str] | None = None,
                     cohort_weeks: set[int] | None = None) -> pd.DataFrame:
    sql = """SELECT s.*,
        CASE WHEN s.source='fiix' THEN f.source_file ELSE c.source_file END AS source_file,
        CASE WHEN s.source='fiix' THEN f.date_completed ELSE c.call_time END AS event_time,
        CASE WHEN s.source='fiix' THEN f.task_description ELSE c.event_description END AS source_description,
        CASE WHEN s.source='fiix' THEN f.tl ELSE NULL END AS fiix_tl,
        CASE WHEN s.source='fiix' THEN f.parent_asset ELSE NULL END AS parent_asset,
        CASE WHEN s.source='fiix' THEN f.ff_group ELSE NULL END AS ff_group,
        CASE WHEN s.source='ignition' THEN c.ign_machine_name ELSE NULL END AS ignition_machine,
        CASE WHEN s.source='ignition' THEN c.ign_area_name ELSE NULL END AS ignition_area
        FROM ftf_incident_sources s
        LEFT JOIN fiix_work_orders f ON s.source='fiix' AND s.source_key=f.wo
        LEFT JOIN ignition_calls c ON s.source='ignition' AND c.id=CAST(s.source_key AS INTEGER)
        """
    params = {}
    has_where = False
    if incident_ids is not None:
        params = {f"id{i}": value for i, value in enumerate(incident_ids)}
        placeholders = ", ".join(f":id{i}" for i in range(len(incident_ids))) or "NULL"
        sql += f" WHERE s.incident_id IN ({placeholders})"
        has_where = True
    if cohort_weeks is not None:
        where = " AND " if has_where else " WHERE "
        sql += where + "s.incident_id IN (SELECT incident_id FROM ftf_incidents " \
               "WHERE cohort_week IN (" + ",".join(str(int(w)) for w in cohort_weeks) + "))"
        has_where = True
    if source is not None:
        params["source"] = source
        sql += " AND " if has_where else " WHERE "
        sql += "s.source=:source"
        has_where = True
    if source_keys is not None:
        params.update({f"key{i}": str(value) for i, value in enumerate(source_keys)})
        placeholders = ", ".join(f":key{i}" for i in range(len(source_keys))) or "NULL"
        sql += " AND " if has_where else " WHERE "
        sql += f"s.source_key IN ({placeholders})"
    sql += " ORDER BY s.source, s.source_key"
    return read_df(engine, sql, params, ["linked_at", "event_time"])


def load_ftf_relationships(engine: Engine, incident_ids: list[str] | None = None,
                           cohort_weeks: set[int] | None = None) -> pd.DataFrame:
    sql, params = "SELECT * FROM ftf_relationship_reviews", {}
    if incident_ids is not None:
        params = {f"id{i}": value for i, value in enumerate(incident_ids)}
        placeholders = ", ".join(f":id{i}" for i in range(len(incident_ids))) or "NULL"
        sql += f" WHERE anchor_incident_id IN ({placeholders})"
    if cohort_weeks is not None:
        ordered = sorted(cohort_weeks)
        params.update({f"wk{i}": value for i, value in enumerate(ordered)})
        placeholders = ", ".join(f":wk{i}" for i in range(len(ordered))) or "NULL"
        sql += (" AND " if " WHERE " in sql else " WHERE ") + (
            f"anchor_incident_id IN (SELECT incident_id FROM ftf_incidents WHERE cohort_week IN ({placeholders}) "
            "OR manual_status='Open / in progress')")
    sql += " ORDER BY reviewed_at DESC"
    return read_df(engine, sql, params, ["reviewed_at"])


def load_ftf_audit(engine: Engine, incident_id: str | None = None) -> pd.DataFrame:
    if incident_id:
        return read_df(engine, "SELECT * FROM ftf_review_audit WHERE incident_id=:id ORDER BY created_at DESC",
                       {"id": incident_id}, ["created_at"])
    return read_df(engine, "SELECT * FROM ftf_review_audit ORDER BY created_at DESC", parse_dates=["created_at"])


def load_ftf_coverage(engine: Engine) -> pd.DataFrame:
    coverage = read_df(engine, "SELECT * FROM ftf_source_coverage ORDER BY source",
                       parse_dates=["complete_through", "updated_at"])
    history = read_df(engine, """SELECT source, MAX(COALESCE(year * 100 + calendar_week, 0)) AS latest_imported_week,
                                MAX(import_datetime) AS last_import
                                FROM file_import_history WHERE source IN ('fiix','ignition') AND status='success'
                                GROUP BY source""", parse_dates=["last_import"])
    if history.empty:
        return coverage
    if coverage.empty:
        coverage = pd.DataFrame({"source": history["source"], "complete_through": pd.NaT,
                                 "reviewer": None, "evidence": None, "updated_at": pd.NaT})
    return coverage.merge(history, on="source", how="outer")


def load_ftf_asset_mappings(engine: Engine) -> pd.DataFrame:
    return read_df(engine, "SELECT * FROM ftf_asset_mappings ORDER BY machine_name")


def create_manual_ftf_incident(engine: Engine, fields: dict, reviewer: str, reason: str,
                               verification_days: float = 7.0) -> str:
    reviewer, reason = clean_text(reviewer), clean_text(reason)
    if not reviewer or not reason:
        raise ValueError("Reviewer and reason are required.")
    now = datetime.now()
    incident_id = str(uuid.uuid4())
    tl = clean_text(fields.get("asset_tl"))
    end = _dt(fields.get("intervention_end"))
    start = _dt(fields.get("intervention_start"))
    rts = _dt(fields.get("return_to_service"))
    owner_type = clean_text(fields.get("owner_type")) or "Repair team"
    values = dict(
        incident_id=incident_id, asset_tl=tl, asset_tl_key=normalize_tl(tl),
        component=clean_text(fields.get("component")), symptom=clean_text(fields.get("symptom")),
        failure_mode=clean_text(fields.get("failure_mode")), root_cause=clean_text(fields.get("root_cause")),
        corrective_action=clean_text(fields.get("corrective_action")), intervention_start=start,
        intervention_end=end, return_to_service=rts, return_documented=bool(fields.get("return_documented", False)),
        owner_type=owner_type, technician_id=fields.get("technician_id") if owner_type == "Technician" else None,
        repair_team=clean_text(fields.get("repair_team")) if owner_type == "Repair team" else None,
        initial_shift=clean_text(fields.get("initial_shift")), department_eligible=bool(fields.get("department_eligible")),
        verification_method="Calendar days", verification_value=float(verification_days),
        verification_deadline=verification_deadline(rts, "Calendar days", verification_days),
        policy_snapshot=f"Calendar days; {float(verification_days):g} days (internal policy)",
        manual_status="Needs classification", created_at=now, updated_at=now, last_calculated_at=now,
        cohort_week=cohort_week(end))
    with engine.begin() as conn:
        conn.execute(FTFIncident.__table__.insert().values(**values))
        conn.execute(FTFReviewAudit.__table__.insert().values(
            incident_id=incident_id, action="Manual incident created", reviewer=reviewer, reason=reason,
            after_json=_json_safe(values), created_at=now))
    bump_data_version(engine)
    return incident_id


def sync_ftf_fiix_incidents(engine: Engine, fiix: pd.DataFrame,
                            department_technician_ids: set[int] | None = None,
                            verification_days: float = 7.0,
                            cohort_weeks: set[int] | None = None) -> int:
    """Create one reviewable incident per previously unseen reactive Fiix WO; never overwrite reviews."""
    if fiix.empty:
        return 0
    qualifying = fiix[fiix["wo_type"].fillna("").astype(str).str.contains("reactive", case=False, regex=False)]
    if cohort_weeks is not None:
        qualifying = qualifying[qualifying["week_key"].isin(cohort_weeks)]
    if qualifying.empty:
        return 0
    now = datetime.now()
    count = 0
    records = [(row, clean_text(row.get("wo"))) for row in qualifying.to_dict("records")]
    source_keys = sorted({wo for _, wo in records if wo})
    with engine.begin() as conn:
        known = set()
        for start in range(0, len(source_keys), 500):
            known.update(conn.execute(select(FTFIncidentSource.source_key).where(
                FTFIncidentSource.source == "fiix",
                FTFIncidentSource.source_key.in_(source_keys[start:start + 500]))).scalars())
        incidents, sources = [], []
        for row, wo in records:
            if not wo or wo in known:
                continue
            incident_id = str(uuid.uuid4())
            completed = _dt(row.get("date_completed"))
            created = _dt(row.get("date_created"))
            tl = clean_text(row.get("tl"))
            tech_id = int(row["technician_id"]) if pd.notna(row.get("technician_id")) else None
            department_eligible = bool(tech_id is not None and department_technician_ids is not None
                                       and tech_id in department_technician_ids)
            incidents.append(dict(
                incident_id=incident_id, asset_tl=tl, asset_tl_key=normalize_tl(tl),
                symptom=clean_text(row.get("task_description")), intervention_start=created,
                intervention_end=completed, owner_type="Technician", technician_id=tech_id,
                initial_shift=clean_text(row.get("shift")), department_eligible=department_eligible,
                verification_method="Calendar days",
                verification_value=float(verification_days),
                policy_snapshot=f"Calendar days; {float(verification_days):g} days (internal policy)",
                manual_status="Needs classification" if completed else "Open / in progress",
                created_at=now, updated_at=now, last_calculated_at=now, cohort_week=cohort_week(completed)))
            sources.append(dict(
                incident_id=incident_id, source="fiix", source_key=wo, relation="Initial intervention",
                linked_at=now))
            known.add(wo)
            count += 1
        if incidents:
            for start in range(0, len(incidents), 500):
                conn.execute(FTFIncident.__table__.insert(), incidents[start:start + 500])
                conn.execute(FTFIncidentSource.__table__.insert(), sources[start:start + 500])
    if count:
        bump_data_version(engine)
    return count


def create_ignition_incident(engine: Engine, call: dict, asset_tl: str | None = None,
                             technician_id: int | None = None, shift: str | None = None,
                             department_eligible: bool = False, verification_days: float = 7.0) -> str:
    call_id = str(call["id"])
    now = datetime.now()
    incident_id = str(uuid.uuid4())
    start = _dt(call.get("call_time"))
    with engine.begin() as conn:
        existing = conn.execute(select(FTFIncidentSource.incident_id).where(
            FTFIncidentSource.source == "ignition", FTFIncidentSource.source_key == call_id)).scalar_one_or_none()
        if existing:
            return str(existing)
        mapping = None
        machine = clean_text(call.get("ign_machine_name")) or clean_text(call.get("machine_name"))
        machine_key = name_key(machine) if machine else None
        if machine_key:
            mapping = conn.execute(select(FTFAssetMapping).where(FTFAssetMapping.machine_key == machine_key)).scalar_one_or_none()
        tl = asset_tl or (mapping.asset_tl if mapping else None)
        conn.execute(FTFIncident.__table__.insert().values(
            incident_id=incident_id, asset_tl=clean_text(tl), asset_tl_key=normalize_tl(tl),
            symptom=clean_text(call.get("event_description")), intervention_start=start,
            owner_type="Technician" if technician_id else "Repair team", technician_id=technician_id,
            repair_team=None if technician_id else "Needs assignment", initial_shift=shift,
            department_eligible=bool(department_eligible),
            verification_method="Calendar days", verification_value=float(verification_days),
            policy_snapshot=f"Calendar days; {float(verification_days):g} days (internal policy)",
            manual_status="Needs classification", created_at=now, updated_at=now, last_calculated_at=now,
            cohort_week=cohort_week(start)))
        conn.execute(FTFIncidentSource.__table__.insert().values(
            incident_id=incident_id, source="ignition", source_key=call_id,
            relation="Initial intervention", linked_at=now))
    bump_data_version(engine)
    return incident_id


def save_ftf_incident(engine: Engine, incident_id: str, fields: dict, reviewer: str, reason: str) -> None:
    reviewer = clean_text(reviewer)
    reason = clean_text(reason)
    if not reviewer or not reason:
        raise ValueError("Reviewer name and a review reason are required.")
    allowed = {"asset_tl", "component", "symptom", "failure_mode", "root_cause", "corrective_action",
               "intervention_start", "intervention_end", "return_to_service", "return_documented",
               "owner_type", "technician_id",
               "repair_team", "initial_shift", "verification_method", "verification_value", "manual_status",
               "failure_reason", "excluded_reason", "review_notes", "department_eligible"}
    values = {key: fields.get(key) for key in allowed if key in fields}
    for key in ("asset_tl", "component", "symptom", "failure_mode", "root_cause", "corrective_action",
                "owner_type", "repair_team", "initial_shift", "verification_method", "manual_status",
                "failure_reason", "excluded_reason", "review_notes"):
        if key in values:
            values[key] = clean_text(values[key])
    for key in ("intervention_start", "intervention_end", "return_to_service"):
        if key in values:
            values[key] = _dt(values[key])
    if values.get("technician_id") in ("", 0):
        values["technician_id"] = None
    tl = values.get("asset_tl")
    if "asset_tl" in values:
        values["asset_tl_key"] = normalize_tl(tl)
    method = values.get("verification_method")
    number = values.get("verification_value")
    if "verification_method" in values or "verification_value" in values or "return_to_service" in values:
        current = read_df(engine, "SELECT return_to_service, verification_method, verification_value FROM ftf_incidents WHERE incident_id=:id",
                          {"id": incident_id}, parse_dates=["return_to_service"]).iloc[0]
        rts = values.get("return_to_service", current["return_to_service"])
        method = values.get("verification_method", current["verification_method"])
        number = values.get("verification_value", current["verification_value"])
        deadline = verification_deadline(rts, method, float(number or 7))
        values["verification_deadline"] = deadline
        values["policy_snapshot"] = f"{method}; {float(number or 7):g} {method.casefold()} (internal policy)"
    values["reviewer"], values["reviewed_at"], values["updated_at"] = reviewer, datetime.now(), datetime.now()
    values["last_calculated_at"] = values["updated_at"]
    if values.get("intervention_end") is not None:
        values["cohort_week"] = cohort_week(values["intervention_end"])
    before = read_df(engine, "SELECT * FROM ftf_incidents WHERE incident_id=:id", {"id": incident_id}).iloc[0].to_dict()
    with engine.begin() as conn:
        conn.execute(update(FTFIncident).where(FTFIncident.incident_id == incident_id).values(**values))
        conn.execute(FTFReviewAudit.__table__.insert().values(
            incident_id=incident_id, action="Incident reviewed/updated", reviewer=reviewer, reason=reason,
            before_json=_json_safe(before), after_json=_json_safe(values), created_at=datetime.now()))
    bump_data_version(engine)


def save_ftf_coverage(engine: Engine, source: str, complete_through, reviewer: str, evidence: str) -> None:
    reviewer, evidence = clean_text(reviewer), clean_text(evidence)
    if source not in {"fiix", "ignition"} or not reviewer or not evidence:
        raise ValueError("Choose a source and provide reviewer and evidence for the coverage attestation.")
    day = pd.Timestamp(complete_through).date() if complete_through else None
    values = dict(source=source, complete_through=day, reviewer=reviewer, evidence=evidence, updated_at=datetime.now())
    with engine.begin() as conn:
        conn.execute(sqlite_insert(FTFSourceCoverage).values(**values).on_conflict_do_update(
            index_elements=["source"], set_={k: v for k, v in values.items() if k != "source"}))
        conn.execute(update(FTFIncident).values(last_calculated_at=datetime.now()))
    bump_data_version(engine)


def save_ftf_asset_mapping(engine: Engine, machine_name: str, tl: str, reviewer: str, reason: str) -> None:
    machine, tl, reviewer, reason = map(clean_text, (machine_name, tl, reviewer, reason))
    key = name_key(machine) if machine else None
    tl_key = normalize_tl(tl)
    if not key or not tl_key or not reviewer or not reason:
        raise ValueError("Machine name, TL#, reviewer and mapping reason are required.")
    values = dict(machine_key=key, machine_name=machine, asset_tl=tl, asset_tl_key=tl_key,
                  reviewer=reviewer, reason=reason, updated_at=datetime.now())
    with engine.begin() as conn:
        conn.execute(sqlite_insert(FTFAssetMapping).values(**values).on_conflict_do_update(
            index_elements=["machine_key"], set_={k: v for k, v in values.items() if k != "machine_key"}))
    bump_data_version(engine)


def save_ftf_relationship(engine: Engine, anchor_id: str, event_source: str, event_key: str,
                          related_incident_id: str, decision: str, reviewer: str, reason: str) -> None:
    reviewer, reason = clean_text(reviewer), clean_text(reason)
    if not reviewer or not reason:
        raise ValueError("Reviewer name and a reason are required.")
    decisions = {"Confirmed related repeat", "Recurrence outside verification window", "Same initial intervention",
                 "Independent failure", "Unresolved"}
    if event_source not in {"fiix", "ignition"} or decision not in decisions:
        raise ValueError("Invalid source or relationship decision.")
    incidents = read_df(engine, "SELECT * FROM ftf_incidents WHERE incident_id IN (:a,:b)",
                        {"a": anchor_id, "b": related_incident_id})
    if len(incidents) != 2 and anchor_id != related_incident_id:
        raise ValueError("Both incidents must exist.")
    by_id = {str(r["incident_id"]): r.to_dict() for _, r in incidents.iterrows()}
    a, b = by_id[anchor_id], by_id[related_incident_id]
    same_tl = normalize_tl(a.get("asset_tl")) is not None and normalize_tl(a.get("asset_tl")) == normalize_tl(b.get("asset_tl"))
    if decision in {"Confirmed related repeat", "Recurrence outside verification window", "Same initial intervention"} and not same_tl:
        raise ValueError("A repeat or cross-source link requires the same normalized individual asset TL#. Parent/area matching is prohibited.")
    if decision == "Confirmed related repeat":
        deadline = pd.to_datetime(a.get("verification_deadline"), errors="coerce")
        table = "fiix_work_orders" if event_source == "fiix" else "ignition_calls"
        key = "wo" if event_source == "fiix" else "id"
        ts = "date_created" if event_source == "fiix" else "call_time"
        event = read_df(engine, f"SELECT {ts} AS event_time FROM {table} WHERE {key}=:key",
                        {"key": event_key}, parse_dates=["event_time"])
        event_time = event.iloc[0]["event_time"] if not event.empty else pd.NaT
        if pd.notna(deadline) and pd.notna(event_time) and event_time > deadline:
            raise ValueError("This event is after the saved verification deadline. Record it as recurrence outside the window instead.")
    now = datetime.now()
    old = read_df(engine, "SELECT * FROM ftf_relationship_reviews WHERE anchor_incident_id=:a AND event_source=:s AND event_key=:k",
                  {"a": anchor_id, "s": event_source, "k": str(event_key)})
    values = dict(anchor_incident_id=anchor_id, event_source=event_source, event_key=str(event_key),
                  related_incident_id=related_incident_id, decision=decision, reason=reason, reviewer=reviewer,
                  reviewed_at=now)
    with engine.begin() as conn:
        if old.empty:
            conn.execute(FTFRelationshipReview.__table__.insert().values(**values))
        else:
            conn.execute(update(FTFRelationshipReview).where(
                FTFRelationshipReview.review_id == int(old.iloc[0]["review_id"])).values(**values))
        conn.execute(FTFReviewAudit.__table__.insert().values(
            incident_id=anchor_id, action=f"Relationship review: {decision}", reviewer=reviewer, reason=reason,
            before_json=_json_safe(old.iloc[0].to_dict() if not old.empty else {}),
            after_json=_json_safe(values), created_at=now))
        conn.execute(update(FTFIncident).where(FTFIncident.incident_id == anchor_id).values(last_calculated_at=now))
        conn.execute(update(FTFIncident).where(FTFIncident.incident_id == related_incident_id).values(
            last_calculated_at=now))
        if decision in {"Confirmed related repeat", "Recurrence outside verification window", "Same initial intervention"}:
            conn.execute(update(FTFIncident).where(FTFIncident.incident_id == related_incident_id).values(
                manual_status="Excluded",
                excluded_reason=(f"Confirmed follow-up to incident {anchor_id}" if decision in {
                    "Confirmed related repeat", "Recurrence outside verification window"}
                                 else f"Duplicate source record for incident {anchor_id}"), updated_at=now))
            conn.execute(update(FTFIncidentSource).where(
                FTFIncidentSource.source == event_source,
                FTFIncidentSource.source_key == str(event_key)).values(
                    incident_id=anchor_id,
                    relation=(decision if decision in {"Confirmed related repeat", "Recurrence outside verification window"}
                              else "Same initial intervention")))
        elif decision == "Unresolved":
            conn.execute(update(FTFIncident).where(FTFIncident.incident_id == related_incident_id).values(
                manual_status="Excluded", excluded_reason=f"Unresolved event review for incident {anchor_id}",
                updated_at=now))
            conn.execute(update(FTFIncidentSource).where(
                FTFIncidentSource.source == event_source,
                FTFIncidentSource.source_key == str(event_key)).values(
                    incident_id=anchor_id, relation="Unresolved possible repeat"))
        elif decision == "Independent failure":
            conn.execute(update(FTFIncidentSource).where(
                FTFIncidentSource.source == event_source,
                FTFIncidentSource.source_key == str(event_key)).values(
                    incident_id=related_incident_id, relation="Independent failure"))
            conn.execute(update(FTFIncident).where(FTFIncident.incident_id == related_incident_id).values(
                manual_status="Needs classification", excluded_reason=None, updated_at=now))
    bump_data_version(engine)


def _dt(value):
    if value is None or pd.isna(value) or value == "":
        return None
    return pd.Timestamp(value).to_pydatetime()


def _json_safe(value):
    def convert(v):
        if v is None or v is pd.NaT:
            return None
        if isinstance(v, (datetime, date, pd.Timestamp)):
            return v.isoformat()
        if pd.isna(v):
            return None
        if hasattr(v, "item"):
            return v.item()
        return v
    return json.dumps(value, default=convert, ensure_ascii=False)
