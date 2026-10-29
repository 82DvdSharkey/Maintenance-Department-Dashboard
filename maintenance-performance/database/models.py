"""SQLAlchemy ORM models for the maintenance performance database."""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Shift(Base):
    __tablename__ = "shifts"

    shift_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    sort_order = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)


class Technician(Base):
    __tablename__ = "technicians"

    technician_id = Column(Integer, primary_key=True, autoincrement=True)
    technician_name = Column(String, nullable=False, unique=True)
    shift = Column(String)
    crew = Column(String)
    department = Column(String)
    active = Column(Boolean, nullable=False, default=True)
    is_lead = Column(Boolean, default=False)
    # Set when a technician is auto-created from a new Dayforce employee; cleared by an administrator.
    needs_review = Column(Boolean, default=False)
    dayforce_name = Column(String)
    dayforce_id = Column(String)
    fiix_name = Column(String)
    ignition_name = Column(String)
    supervisor = Column(String)
    start_date = Column(Date)
    end_date = Column(Date)

    __table_args__ = (Index("ix_technicians_shift", "shift"),)


class AdministrativeUser(Base):
    __tablename__ = "administrative_users"

    administrative_user_id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, nullable=False, unique=True)
    display_name = Column(String, nullable=False, default="")
    active = Column(Boolean, nullable=False, default=True)


class TechnicianAlias(Base):
    __tablename__ = "technician_aliases"

    alias_id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)  # fiix | ignition | dayforce
    alias = Column(String, nullable=False)
    alias_key = Column(String, nullable=False)
    technician_id = Column(Integer, ForeignKey("technicians.technician_id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("source", "alias_key", name="uq_alias_source_key"),
        Index("ix_alias_technician", "technician_id"),
    )


class FileImportHistory(Base):
    __tablename__ = "file_import_history"

    import_id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    full_path = Column(String, nullable=False)
    file_modified = Column(DateTime)
    file_size = Column(Integer)
    file_hash = Column(String)
    import_datetime = Column(DateTime, nullable=False)
    row_count = Column(Integer, default=0)
    inserted_count = Column(Integer, default=0)
    duplicate_count = Column(Integer, default=0)
    filename_year = Column(Integer)
    filename_week = Column(Integer)
    year = Column(Integer)
    calendar_week = Column(Integer)
    status = Column(String, nullable=False)  # success | failed
    warnings = Column(Text)

    __table_args__ = (Index("ix_import_file_identity", "filename", "file_size", "file_modified"),)


class FiixWorkOrder(Base):
    __tablename__ = "fiix_work_orders"

    wo = Column(String, primary_key=True)
    ff_group = Column(String)
    tl = Column(String)
    parent_asset = Column(String)
    asset = Column(String)
    task_description = Column(Text)
    task_notes_completion = Column(Text)
    completion_notes = Column(Text)
    time_spent_raw = Column(String)
    time_spent_hours = Column(Float)
    completed_by_raw = Column(String)
    completed_by = Column(String)
    completed_by_key = Column(String)
    date_created_raw = Column(String)
    date_created = Column(DateTime)
    date_completed_raw = Column(String)
    date_completed = Column(DateTime)
    status = Column(String)
    wo_type = Column(String)
    year = Column(Integer)
    calendar_week = Column(Integer)
    week_key = Column(Integer)
    week_start = Column(DateTime)
    week_end = Column(DateTime)
    source_file = Column(String)
    import_id = Column(Integer, ForeignKey("file_import_history.import_id"))

    __table_args__ = (
        Index("ix_fiix_completed_by_key", "completed_by_key"),
        Index("ix_fiix_date_completed", "date_completed"),
        Index("ix_fiix_year_week", "year", "calendar_week"),
        Index("ix_fiix_week_key", "week_key"),
        Index("ix_fiix_asset", "asset"),
        Index("ix_fiix_wo_type", "wo_type"),
    )


class IgnitionCall(Base):
    __tablename__ = "ignition_calls"

    id = Column(Integer, primary_key=True, autoincrement=False)
    focus_factory = Column(String)
    ign_area_name = Column(String)
    ign_machine_name = Column(String)
    area_name = Column(String)
    machine_name = Column(String)
    run_start = Column(DateTime)
    run_end = Column(DateTime)
    downtime_id = Column(Float)
    downtime_start = Column(DateTime)
    downtime_end = Column(DateTime)
    event_description = Column(Text)
    active = Column(Integer)
    operator_name = Column(String)
    tech_name = Column(String)
    tech_name_norm = Column(String)
    tech_key = Column(String)
    call_type = Column(String)
    call_type_norm = Column(String)
    call_time = Column(DateTime)
    response_time = Column(DateTime)
    arrival_time = Column(DateTime)
    complete_time = Column(DateTime)
    raw_call_duration = Column(Float)
    raw_ack_duration = Column(Float)
    raw_response_duration = Column(Float)
    raw_repair_duration = Column(Float)
    raw_delay_until_call = Column(Float)
    delay_until_call = Column(String)
    call_duration = Column(String)
    acknowledge_duration = Column(String)
    response_duration = Column(String)
    repair_duration = Column(String)
    code_sent = Column(String)
    cancelled = Column(String)
    cancelled_user = Column(String)
    logged_out_user = Column(String)
    conveyance_part_numbers = Column(Text)
    call_duration_min = Column(Float)
    ack_duration_min = Column(Float)
    response_duration_min = Column(Float)
    repair_duration_min = Column(Float)
    extra_json = Column(Text)
    year = Column(Integer)
    calendar_week = Column(Integer)
    week_key = Column(Integer)
    week_start = Column(DateTime)
    week_end = Column(DateTime)
    source_file = Column(String)
    import_id = Column(Integer, ForeignKey("file_import_history.import_id"))

    __table_args__ = (
        Index("ix_ign_tech_key", "tech_key"),
        Index("ix_ign_call_time", "call_time"),
        Index("ix_ign_year_week", "year", "calendar_week"),
        Index("ix_ign_week_key", "week_key"),
        Index("ix_ign_area", "ign_area_name"),
        Index("ix_ign_call_type", "call_type_norm"),
    )


class DayforceHours(Base):
    __tablename__ = "dayforce_hours"

    dayforce_id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False)
    calendar_week = Column(Integer, nullable=False)
    week_key = Column(Integer, nullable=False)
    technician_id = Column(Integer, ForeignKey("technicians.technician_id"), nullable=False)
    shift = Column(String)
    regular_hours = Column(Float)
    overtime_hours = Column(Float)
    other_paid_hours = Column(Float)
    excluded_hours = Column(Float)
    total_hours_worked = Column(Float)
    total_overridden = Column(Boolean, nullable=False, default=False)
    comments = Column(Text)
    updated_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("year", "calendar_week", "technician_id", name="uq_dayforce_week_tech"),
        Index("ix_dayforce_week_key", "week_key"),
        Index("ix_dayforce_technician", "technician_id"),
    )


class DayforceEntry(Base):
    """Daily time entries imported from Dayforce reports (source of imported hours worked)."""

    __tablename__ = "dayforce_entries"

    entry_id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(String, nullable=False)
    employee_name = Column(String)
    employee_key = Column(String)
    work_date = Column(Date, nullable=False)
    in_time = Column(DateTime)
    out_time = Column(DateTime)
    job = Column(String)
    pay_code = Column(String)
    pay_category = Column(String)
    normal_hours = Column(Float)
    other_hours = Column(Float)
    year = Column(Integer)
    calendar_week = Column(Integer)
    week_key = Column(Integer)
    report_start = Column(Date)
    report_end = Column(Date)
    source_file = Column(String)
    import_id = Column(Integer, ForeignKey("file_import_history.import_id"))

    __table_args__ = (
        Index("ix_dayforce_entry_emp_date", "employee_id", "work_date"),
        Index("ix_dayforce_entry_week_key", "week_key"),
    )


class PMTarget(Base):
    __tablename__ = "pm_targets"

    pm_target_id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False)
    calendar_week = Column(Integer, nullable=False)
    week_key = Column(Integer, nullable=False)
    technician_id = Column(Integer, ForeignKey("technicians.technician_id"), nullable=False)
    shift = Column(String)
    pm_target = Column(Float)
    comments = Column(Text)
    updated_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("year", "calendar_week", "technician_id", name="uq_pm_week_tech"),
        Index("ix_pm_week_key", "week_key"),
        Index("ix_pm_technician", "technician_id"),
    )


class PMAreaStatus(Base):
    """Weekly PM Open vs Closed counts per area (PM Completion denominator source)."""

    __tablename__ = "pm_area_status"

    pm_status_id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False)
    calendar_week = Column(Integer, nullable=False)
    week_key = Column(Integer, nullable=False)
    area = Column(String, nullable=False)
    pm_open = Column(Float)
    pm_closed = Column(Float)
    comments = Column(Text)
    updated_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("year", "calendar_week", "area", name="uq_pm_status_week_area"),
        Index("ix_pm_status_week_key", "week_key"),
    )


class WeekLock(Base):
    __tablename__ = "week_locks"

    lock_id = Column(Integer, primary_key=True, autoincrement=True)
    dataset = Column(String, nullable=False)  # dayforce | pm
    year = Column(Integer, nullable=False)
    calendar_week = Column(Integer, nullable=False)
    locked_at = Column(DateTime)

    __table_args__ = (UniqueConstraint("dataset", "year", "calendar_week", name="uq_week_lock"),)


class WOTypeConfig(Base):
    __tablename__ = "wo_type_config"

    wo_type = Column(String, primary_key=True)
    classification = Column(String, nullable=False, default="Other")
    counts_toward_labor = Column(Boolean, nullable=False, default=True)
    ftf_qualifying = Column(Boolean, nullable=False, default=False)
    is_pm = Column(Boolean, nullable=False, default=False)
    reviewed = Column(Boolean, nullable=False, default=False)
    first_seen = Column(DateTime)


class KPITarget(Base):
    __tablename__ = "kpi_targets"

    kpi_key = Column(String, primary_key=True)
    label = Column(String, nullable=False)
    target = Column(Float)
    watch_band = Column(Float)
    direction = Column(String, nullable=False)  # higher | lower
    unit = Column(String)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(Text)


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"

    issue_id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)
    issue_type = Column(String, nullable=False)
    record_key = Column(String)
    description = Column(Text)
    file_name = Column(String)
    import_id = Column(Integer, ForeignKey("file_import_history.import_id"))
    created_at = Column(DateTime)

    __table_args__ = (Index("ix_dq_issue_type", "issue_type"),)


class FTFIncident(Base):
    """Manually classified first-repair incident; TL is the sole equipment identity."""

    __tablename__ = "ftf_incidents"

    incident_id = Column(String, primary_key=True)
    asset_tl = Column(String)
    asset_tl_key = Column(String, index=True)
    component = Column(String)
    symptom = Column(Text)
    failure_mode = Column(Text)
    root_cause = Column(Text)
    corrective_action = Column(Text)
    intervention_start = Column(DateTime)
    intervention_end = Column(DateTime)
    return_to_service = Column(DateTime)
    return_documented = Column(Boolean, nullable=False, default=False)
    owner_type = Column(String, nullable=False, default="Technician")
    technician_id = Column(Integer, ForeignKey("technicians.technician_id"))
    repair_team = Column(String)
    department_eligible = Column(Boolean, nullable=False, default=False)
    initial_shift = Column(String)
    verification_method = Column(String, nullable=False, default="Calendar days")
    verification_value = Column(Float, nullable=False, default=7.0)
    verification_deadline = Column(DateTime)
    policy_snapshot = Column(String)
    manual_status = Column(String, nullable=False, default="Needs classification")
    outcome = Column(String)
    failure_reason = Column(String)
    excluded_reason = Column(Text)
    reviewer = Column(String)
    reviewed_at = Column(DateTime)
    review_notes = Column(Text)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    last_calculated_at = Column(DateTime)
    cohort_week = Column(Integer, index=True)

    __table_args__ = (Index("ix_ftf_cohort_status", "cohort_week", "manual_status"),)


class FTFIncidentSource(Base):
    """Idempotent Fiix/ Ignition record link; imports never overwrite incident classifications."""

    __tablename__ = "ftf_incident_sources"

    source_link_id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(String, ForeignKey("ftf_incidents.incident_id"), nullable=False)
    source = Column(String, nullable=False)
    source_key = Column(String, nullable=False)
    relation = Column(String, nullable=False, default="Initial intervention")
    linked_by = Column(String)
    linked_at = Column(DateTime, nullable=False)
    __table_args__ = (UniqueConstraint("source", "source_key", name="uq_ftf_source_record"),
                      Index("ix_ftf_source_incident", "incident_id"),
                      Index("ix_ftf_source_source_key", "source", "source_key"),
                      Index("ix_ftf_source_incident_source", "incident_id", "source"))


class FTFRelationshipReview(Base):
    """Audited reviewer decision linking a possible follow-up to an incident."""

    __tablename__ = "ftf_relationship_reviews"

    review_id = Column(Integer, primary_key=True, autoincrement=True)
    anchor_incident_id = Column(String, ForeignKey("ftf_incidents.incident_id"), nullable=False)
    event_source = Column(String, nullable=False)
    event_key = Column(String, nullable=False)
    related_incident_id = Column(String, ForeignKey("ftf_incidents.incident_id"))
    decision = Column(String, nullable=False)
    reason = Column(Text, nullable=False)
    reviewer = Column(String, nullable=False)
    reviewed_at = Column(DateTime, nullable=False)
    __table_args__ = (UniqueConstraint("anchor_incident_id", "event_source", "event_key", name="uq_ftf_relationship"),
                      Index("ix_ftf_relationship_anchor", "anchor_incident_id"))


class FTFReviewAudit(Base):
    __tablename__ = "ftf_review_audit"

    audit_id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(String, ForeignKey("ftf_incidents.incident_id"), nullable=False)
    action = Column(String, nullable=False)
    reviewer = Column(String, nullable=False)
    reason = Column(Text)
    before_json = Column(Text)
    after_json = Column(Text)
    created_at = Column(DateTime, nullable=False)
    __table_args__ = (Index("ix_ftf_audit_incident", "incident_id"),)


class FTFSourceCoverage(Base):
    """Reviewer-attested complete-through date; import timestamps alone do not prove coverage."""

    __tablename__ = "ftf_source_coverage"

    source = Column(String, primary_key=True)
    complete_through = Column(Date)
    reviewer = Column(String)
    evidence = Column(Text)
    updated_at = Column(DateTime)


class FTFAssetMapping(Base):
    """Explicit verified Ignition machine name to individual Fiix asset TL# mapping."""

    __tablename__ = "ftf_asset_mappings"

    machine_key = Column(String, primary_key=True)
    machine_name = Column(String, nullable=False)
    asset_tl = Column(String, nullable=False)
    asset_tl_key = Column(String, nullable=False, index=True)
    reviewer = Column(String, nullable=False)
    reason = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False)
