"""Reviewed, incident-based First-Time Fix reporting and classification queue."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from database import ftf_repository as repo
from database.database import set_app_setting
from kpi.ftf_incidents import (
    FAILURE_REASONS,
    STATUS_EXCLUDED,
    STATUS_FAILED,
    STATUS_NEEDS_CLASSIFICATION,
    STATUS_OPEN,
    STATUS_PENDING,
    STATUS_SUCCESS,
    STATUS_UNRESOLVED,
    evaluate_incident,
    normalize_tl,
    pooled_four_week_baseline,
    potential_repeat,
    summarize_incidents,
)
from ui.components import no_data_message, show_table
from ui.pdf_reporting import ReportMetric, ReportSection, render_pdf_download_button
from utils.dates import shift_week, week_label

MANUAL_STATUSES = [STATUS_NEEDS_CLASSIFICATION, STATUS_OPEN, STATUS_EXCLUDED]


def _text(value) -> str:
    return "" if value is None or pd.isna(value) else str(value)


def _stamp(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")


def _parse_stamp(value: str):
    value = value.strip()
    return None if not value else pd.to_datetime(value, errors="raise").to_pydatetime()


def _records(ctx):
    dept_ids = set(ctx.base.technicians.loc[ctx.base.technicians["in_department"].astype(bool),
                                             "technician_id"].astype(int))
    cohort_weeks = set(ctx.filters.week_keys)
    if ctx.filters.end_week is not None:
        cohort_weeks.update(shift_week(ctx.filters.end_week, -n) for n in range(1, 5))
    source_rows = ctx.base.fiix[ctx.base.fiix["week_key"].isin(cohort_weeks)]
    repo.sync_ftf_fiix_incidents(ctx.engine, source_rows, dept_ids,
                                 float(ctx.base.app_settings.get("ftf_verification_days", 7)), cohort_weeks)
    incidents = repo.load_ftf_incidents(ctx.engine, cohort_weeks)
    sources = repo.load_ftf_sources(ctx.engine, cohort_weeks=cohort_weeks)
    relationships = repo.load_ftf_relationships(ctx.engine, cohort_weeks=cohort_weeks)
    coverage_df = repo.load_ftf_coverage(ctx.engine)
    coverage = {r.source: r.complete_through for r in coverage_df.itertuples() if pd.notna(r.complete_through)}
    unresolved = set(relationships.loc[relationships.decision == "Unresolved", "anchor_incident_id"].astype(str)) \
        if not relationships.empty else set()
    failed = set(relationships.loc[relationships.decision == "Confirmed related repeat", "anchor_incident_id"].astype(str)) \
        if not relationships.empty else set()
    source_text = {}
    report_dims = {}
    for incident_id, group in sources.groupby("incident_id") if not sources.empty else []:
        source_text[str(incident_id)] = "; ".join(
            f"{r.source.upper()} {r.source_key} ({r.relation}; {r.event_time or 'no timestamp'}; "
            f"{r.source_file or 'unknown file'}; {r.source_description or r.ignition_machine or r.fiix_tl or 'no description'})"
            for r in group.itertuples())
        report_dims[str(incident_id)] = {
            "parent_asset": next((_text(r.parent_asset) for r in group.itertuples() if _text(r.parent_asset)), None),
            "production_group": next((_text(r.ff_group) for r in group.itertuples() if _text(r.ff_group)), None),
            "ignition_area": next((_text(r.ignition_area) for r in group.itertuples() if _text(r.ignition_area)), None),
        }
    out = incidents.copy()
    if out.empty:
        return out, coverage_df, coverage, relationships
    now = datetime.now()
    stale = out["outcome"].isna() | out["last_calculated_at"].isna() | (
        pd.to_datetime(out["updated_at"], errors="coerce") > pd.to_datetime(out["last_calculated_at"], errors="coerce"))
    if stale.any():
        calculated = out.loc[stale]
        states = [evaluate_incident(r, now, coverage,
                                    unresolved_review=str(r["incident_id"]) in unresolved,
                                    confirmed_repeat=str(r["incident_id"]) in failed)
                  for r in calculated.to_dict("records")]
        state_df = pd.DataFrame(states, index=calculated.index)
        for col in state_df.columns:
            out.loc[stale, col] = state_df[col]
        repo.store_ftf_outcomes(ctx.engine, [{"incident_id": r.incident_id, "outcome": r.status}
                                             for r in out.loc[stale, ["incident_id", "status"]].itertuples()])
    out["status"] = out["outcome"].fillna(STATUS_NEEDS_CLASSIFICATION)
    out["assessed"] = out["status"].isin([STATUS_SUCCESS, STATUS_FAILED])
    out["success"] = out["status"].eq(STATUS_SUCCESS)
    out["coverage_sufficient"] = out["assessed"]
    out["sources"] = out["incident_id"].astype(str).map(source_text).fillna("")
    out["asset_tl_key"] = out["asset_tl"].map(normalize_tl)
    for col in ("parent_asset", "production_group", "ignition_area"):
        out[col] = out["incident_id"].astype(str).map(lambda i: report_dims.get(i, {}).get(col))
    out["owner_label"] = out["technician_name"].fillna(out["repair_team"]).fillna("Needs assignment")
    return out, coverage_df, coverage, relationships


def _week_options(frame: pd.DataFrame) -> list[int]:
    values = frame["cohort_week"].dropna().astype(int).unique().tolist() if not frame.empty else []
    if not values:
        today = date.today().isocalendar()
        return [today.year * 100 + today.week]
    return sorted(values)


def _review_editor(ctx, incident: pd.Series) -> None:
    techs = ctx.base.technicians.sort_values("technician_name")
    technician_names = {int(r.technician_id): r.technician_name for r in techs.itertuples()}
    current_tech = int(incident["technician_id"]) if pd.notna(incident.get("technician_id")) else None
    tech_ids = [None] + list(technician_names)
    if current_tech is not None and current_tech not in technician_names:
        tech_ids.append(current_tech)
        technician_names[current_tech] = f"Technician #{current_tech}"
    shifts = ctx.base.shifts.name.tolist() if not ctx.base.shifts.empty else []
    if incident.get("initial_shift") and incident["initial_shift"] not in shifts:
        shifts.append(incident["initial_shift"])
    status = incident.get("manual_status") or STATUS_NEEDS_CLASSIFICATION
    if status not in MANUAL_STATUSES:
        status = STATUS_NEEDS_CLASSIFICATION
    reasons = FAILURE_REASONS
    if incident.get("failure_reason") and incident["failure_reason"] not in reasons:
        reasons = [incident["failure_reason"]] + reasons
    with st.form(f"ftf_review_{incident['incident_id']}"):
        st.caption("Source values are provisional. A reviewer must classify the failure and verify return-to-service evidence; no source text is interpreted as a confirmed diagnosis.")
        c1, c2, c3 = st.columns(3)
        with c1:
            asset_tl = st.text_input("Individual asset TL# (required)", _text(incident.get("asset_tl")),
                                     help="Only this TL# identifies the equipment. Parent asset, production line, area, and Ignition area are never substituted.")
            component = st.text_input("Component / sub-asset (optional)", _text(incident.get("component")))
            symptom = st.text_area("Reported symptom (required)", _text(incident.get("symptom")), height=90)
            failure_mode = st.text_input("Failure mode (required; reviewer classified)", _text(incident.get("failure_mode")))
        with c2:
            root_cause = st.text_area("Suspected / confirmed root cause", _text(incident.get("root_cause")), height=70)
            corrective_action = st.text_area("Corrective action", _text(incident.get("corrective_action")), height=70)
            start = st.text_input("First intervention start (YYYY-MM-DD HH:MM)", _stamp(incident.get("intervention_start")))
            end = st.text_input("First intervention end (YYYY-MM-DD HH:MM)", _stamp(incident.get("intervention_end")))
            rts = st.text_input("Return to service (YYYY-MM-DD HH:MM)", _stamp(incident.get("return_to_service")))
            return_documented = st.checkbox("Return to service is documented in the source record / notes",
                                            value=bool(incident.get("return_documented", False)))
        with c3:
            owner_type = st.radio("Initial repair owner", ["Technician", "Repair team"],
                                  index=0 if incident.get("owner_type") != "Repair team" else 1,
                                  horizontal=True)
            technician_id = st.selectbox("Primary technician", tech_ids,
                                         index=tech_ids.index(current_tech) if current_tech in tech_ids else 0,
                                         format_func=lambda i: "Not assigned" if i is None else technician_names.get(i, str(i)))
            repair_team = st.text_input("Repair team / shared repair owner", _text(incident.get("repair_team")))
            shift = st.selectbox("Initial shift", [""] + shifts,
                                 index=(([""] + shifts).index(incident.get("initial_shift"))
                                        if incident.get("initial_shift") in [""] + shifts else 0))
            department_eligible = st.checkbox("Belongs to maintenance department", bool(incident.get("department_eligible", False)))
            method = st.selectbox("Verification method", ["Calendar days"],
                                   help="Fiix and Ignition exports do not contain reliable runtime/cycle meter data. Do not infer operating hours from elapsed time.")
            verify_days = st.number_input("Verification window (calendar days)", min_value=1.0, max_value=365.0,
                                          value=float(incident.get("verification_value") or 7), step=1.0)
            manual_status = st.selectbox("Review state", MANUAL_STATUSES, index=MANUAL_STATUSES.index(status))
            failure_reason = st.selectbox("Failure reason", reasons,
                                          index=reasons.index(incident.get("failure_reason"))
                                          if incident.get("failure_reason") in reasons else len(reasons) - 1)
            excluded_reason = st.text_input("Exclusion reason", _text(incident.get("excluded_reason")))
        review_notes = st.text_area("Reviewer notes", _text(incident.get("review_notes")))
        reviewer = st.text_input("Reviewer name (authentication is not provided by this local app)",
                                 _text(incident.get("reviewer")))
        reason = st.text_input("Reason for this review / correction (required)")
        submitted = st.form_submit_button("Save reviewed incident", type="primary")
    if submitted:
        try:
            fields = {
                "asset_tl": asset_tl, "component": component, "symptom": symptom, "failure_mode": failure_mode,
                "root_cause": root_cause, "corrective_action": corrective_action,
                "intervention_start": _parse_stamp(start), "intervention_end": _parse_stamp(end),
                "return_to_service": _parse_stamp(rts), "return_documented": return_documented,
                "owner_type": owner_type, "technician_id": technician_id if owner_type == "Technician" else None,
                "repair_team": repair_team if owner_type == "Repair team" else None,
                "initial_shift": shift or None, "department_eligible": department_eligible,
                "verification_method": method, "verification_value": verify_days,
                "manual_status": manual_status, "failure_reason": failure_reason,
                "excluded_reason": excluded_reason, "review_notes": review_notes,
            }
            repo.save_ftf_incident(ctx.engine, str(incident["incident_id"]), fields, reviewer, reason)
            st.success("Incident review saved and audit history recorded.")
            st.rerun()
        except (ValueError, TypeError) as exc:
            st.error(f"Could not save review: {exc}")


def _coverage_editor(ctx, coverage_df: pd.DataFrame) -> None:
    with st.expander("Source completeness attestations", expanded=not bool(len(coverage_df) == 2)):
        st.warning("Import timestamps and latest event dates do not prove complete source coverage. FTF successes remain provisional until a reviewer attests that both source histories are complete through the verification deadline. This local app has no sign-in/role authorization; deploy behind an authorized access layer before production review.")
        current = {r.source: r for r in coverage_df.itertuples()} if not coverage_df.empty else {}
        with st.form("ftf_coverage_form"):
            source = st.selectbox("Source", ["fiix", "ignition"], format_func=str.title)
            saved = current.get(source)
            through = st.date_input("Complete through date", value=saved.complete_through if saved and pd.notna(saved.complete_through) else date.today())
            reviewer = st.text_input("Coverage reviewer", value=saved.reviewer if saved else "")
            evidence = st.text_input("Evidence / report range reviewed", value=saved.evidence if saved else "")
            save = st.form_submit_button("Save coverage attestation")
        if save:
            try:
                repo.save_ftf_coverage(ctx.engine, source, through, reviewer, evidence)
                st.success("Coverage attestation saved.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        show_table(coverage_df, {"source": "Source", "complete_through": "Complete Through", "reviewer": "Reviewer",
                                 "evidence": "Evidence", "updated_at": "Attested At",
                                 "latest_imported_week": "Latest Imported Week", "last_import": "Last Import"})


def _manual_incident_entry(ctx) -> None:
    with st.expander("Manually enter an incident not represented by an imported source record"):
        st.caption("Manual incidents still require source evidence, an individual TL#, reviewer classification, and source coverage before they can count in FTF.")
        techs = ctx.base.technicians.sort_values("technician_name")
        names = {int(r.technician_id): r.technician_name for r in techs.itertuples()}
        ids = [None] + list(names)
        shifts = [""] + ctx.base.shifts.name.tolist()
        with st.form("ftf_manual_incident_form"):
            owner_type = st.radio("Initial repair owner", ["Technician", "Repair team"], horizontal=True)
            technician_id = st.selectbox("Primary technician", ids,
                                         format_func=lambda i: "Not assigned" if i is None else names[i])
            repair_team = st.text_input("Repair team / shared owner")
            department = st.checkbox("Belongs to maintenance department")
            shift = st.selectbox("Initial shift", shifts)
            tl = st.text_input("Individual asset TL# (required)")
            component = st.text_input("Component / sub-asset")
            symptom = st.text_area("Reported symptom")
            failure_mode = st.text_input("Failure mode (reviewer classified)")
            root = st.text_input("Suspected / confirmed root cause")
            action = st.text_input("Corrective action")
            start = st.text_input("First intervention start (YYYY-MM-DD HH:MM)")
            end = st.text_input("First intervention end (YYYY-MM-DD HH:MM)")
            rts = st.text_input("Return to service (YYYY-MM-DD HH:MM)")
            documented = st.checkbox("Return to service is documented")
            reviewer = st.text_input("Reviewer name")
            reason = st.text_input("Entry reason / source evidence")
            save = st.form_submit_button("Create reviewable incident", type="primary")
        if save:
            try:
                incident_id = repo.create_manual_ftf_incident(ctx.engine, {
                    "asset_tl": tl, "component": component, "symptom": symptom, "failure_mode": failure_mode,
                    "root_cause": root, "corrective_action": action, "intervention_start": _parse_stamp(start),
                    "intervention_end": _parse_stamp(end), "return_to_service": _parse_stamp(rts),
                    "return_documented": documented, "owner_type": owner_type, "technician_id": technician_id,
                    "repair_team": repair_team, "initial_shift": shift, "department_eligible": department,
                }, reviewer, reason, float(ctx.base.app_settings.get("ftf_verification_days", 7)))
                st.success(f"Created incident {incident_id[:8]} in Needs classification.")
                st.rerun()
            except (ValueError, TypeError) as exc:
                st.error(f"Could not create incident: {exc}")


def _ignition_capture(ctx, incidents: pd.DataFrame) -> None:
    calls = ctx.inputs.calls
    if calls.empty:
        return
    linked = set(repo.load_ftf_sources(ctx.engine, source="ignition",
                                      source_keys=calls["id"].astype(str).tolist())["source_key"].astype(str))
    unlinked = calls[~calls.id.astype(str).isin(linked)]
    with st.expander(f"Add an Ignition Maintenance call as a reviewable incident ({len(unlinked):,} unlinked)"):
        st.caption("Calls are not auto-merged with Fiix WOs. First map the Ignition machine name to a verified individual TL# below; area names and parent assets cannot identify equipment.")
        mappings = repo.load_ftf_asset_mappings(ctx.engine)
        machines = sorted(set((unlinked["ign_machine_name"].fillna("").astype(str) + " " +
                               unlinked["machine_name"].fillna("").astype(str)).str.strip()) - {""})
        asset_tls = sorted(ctx.base.fiix["tl"].dropna().astype(str).str.strip().loc[lambda s: s.ne("")].unique())
        if machines and asset_tls:
            with st.form("ftf_asset_mapping_form"):
                machine = st.selectbox("Ignition machine label", machines)
                tl = st.selectbox("Verified Fiix TL#", asset_tls)
                mapping_reviewer = st.text_input("Mapping reviewer")
                mapping_reason = st.text_input("Evidence for exact equipment mapping")
                submit_mapping = st.form_submit_button("Save explicit asset mapping")
            if submit_mapping:
                try:
                    repo.save_ftf_asset_mapping(ctx.engine, machine, tl, mapping_reviewer, mapping_reason)
                    st.success("Verified mapping saved; it will only populate TL# for newly created incidents.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        show_table(mappings, {"machine_name": "Ignition Machine", "asset_tl": "Verified TL#",
                              "reviewer": "Reviewer", "reason": "Evidence", "updated_at": "Updated"})
        if not unlinked.empty:
            choices = unlinked.sort_values("call_time", ascending=False).head(300)
            call_id = st.selectbox("Maintenance Call ID", choices.id.astype(str).tolist(),
                                   format_func=lambda x: f"{x} — {choices.set_index(choices.id.astype(str)).loc[x, 'call_time']}")
            if st.button("Create incident for selected call", key="ftf_create_call"):
                call = choices[choices.id.astype(str) == call_id].iloc[0].to_dict()
                tech_id = int(call["technician_id"]) if pd.notna(call.get("technician_id")) else None
                department = bool(tech_id is not None and not call.get("non_department", False))
                repo.create_ignition_incident(ctx.engine, call, technician_id=tech_id,
                                              shift=call.get("shift"), department_eligible=department,
                                              verification_days=float(ctx.base.app_settings.get("ftf_verification_days", 7)))
                st.success(f"Ignition call {call_id} added to the classification queue.")
                st.rerun()


def _relationship_review(ctx, incidents: pd.DataFrame) -> None:
    if len(incidents) < 2:
        return
    with st.expander("Review possible repeat relationships"):
        st.caption("Suggestions compare only normalized individual TL#, failure mode, and component when present. They never auto-link or automatically confirm an incident relationship. Similar symptoms/text alone are not matching evidence.")
        query = st.text_input("Search incidents by individual TL#, failure mode, or incident ID", key="ftf_rel_search").strip().casefold()
        if not query:
            st.info("Enter an individual TL# or failure-mode term to load a manageable, same-asset review list.")
            return
        searchable = incidents.assign(
            _search=(incidents["asset_tl"].fillna("").astype(str) + " " +
                     incidents["failure_mode"].fillna("").astype(str) + " " +
                     incidents["incident_id"].astype(str)).str.casefold())
        matches = searchable[searchable["_search"].str.contains(query, regex=False)].head(500)
        if len(matches) < 2:
            st.info("Search returned fewer than two incidents.")
            return
        labels = {str(r.incident_id): f"{r.cohort_week or 'unassigned'} · TL {r.asset_tl or 'MISSING'} · {r.failure_mode or 'Needs classification'} · {r.incident_id[:8]}"
                 for r in matches.itertuples()}
        choices = list(labels)
        sources = repo.load_ftf_sources(ctx.engine)
        with st.form("ftf_relationship_form"):
            anchor_id = st.selectbox("Original incident", choices, format_func=labels.get)
            anchor_tl = normalize_tl(matches.set_index("incident_id").loc[anchor_id, "asset_tl"])
            candidates = [str(i) for i in matches.loc[matches["asset_tl"].map(normalize_tl).eq(anchor_tl),
                                                       "incident_id"] if str(i) != anchor_id]
            if not candidates:
                st.warning("No other incidents share this individual TL#. Different TL# values cannot be linked as repeats.")
                return
            candidate_id = st.selectbox("Possible later event / incident", candidates, format_func=labels.get)
            candidate_sources = sources[sources.incident_id.astype(str) == candidate_id]
            source_options = candidate_sources.index.tolist()
            source_row_index = st.selectbox(
                "Exact source event to review", source_options,
                format_func=lambda i: f"{candidate_sources.loc[i, 'source'].upper()} "
                                      f"{candidate_sources.loc[i, 'source_key']} — "
                                      f"{candidate_sources.loc[i, 'event_time'] or 'no timestamp'}")
            anchor = incidents.set_index("incident_id").loc[anchor_id].to_dict()
            candidate = incidents.set_index("incident_id").loc[candidate_id].to_dict()
            suggestion, explanation = potential_repeat(anchor, candidate)
            st.info(("Potential repeat suggestion: " if suggestion else "No repeat suggestion: ") + explanation)
            decision = st.selectbox("Reviewer decision", ["Unresolved", "Confirmed related repeat",
                                                          "Recurrence outside verification window",
                                                          "Same initial intervention", "Independent failure"])
            reason = st.text_input("Decision rationale")
            reviewer = st.text_input("Reviewer name", key="ftf_relationship_reviewer")
            submit = st.form_submit_button("Save relationship review")
        if submit:
            try:
                if candidate_sources.empty:
                    raise ValueError("The candidate must have a linked Fiix WO or Ignition call.")
                source = candidate_sources.loc[source_row_index]
                repo.save_ftf_relationship(ctx.engine, anchor_id, source.source, source.source_key,
                                           candidate_id, decision, reviewer, reason)
                st.success("Relationship decision saved to audit history.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def render(ctx) -> None:
    st.title("First-Time Fix Rate (Incident-based)")
    pdf_slot = st.container()
    st.caption("Internal policy: assess the first repair intervention by individual asset TL# and reviewer-classified failure mode. An asset-level repeat estimate is not reported as verified FTF.")
    if not ctx.base.has_data:
        no_data_message()
        render_pdf_download_button(ctx, "First-Time Fix Incidents", [], [],
                                   notices=["No data is available for the current selection."],
                                   key="pdf_ftf_empty", container=pdf_slot)
        return
    incidents, coverage_df, coverage, relationships = _records(ctx)
    with st.expander("FTF policy defaults"):
        d1, d2 = st.columns(2)
        with d1:
            window = st.number_input("Default calendar-day verification window for new incidents", min_value=1,
                                     max_value=365, value=int(ctx.base.app_settings.get("ftf_verification_days", 7)))
        with d2:
            configured_low = st.number_input("Low-volume warning threshold", min_value=1, max_value=10000,
                                             value=int(ctx.base.app_settings.get("ftf_low_volume_threshold", 10)))
        if st.button("Save FTF defaults", key="ftf_save_defaults"):
            set_app_setting(ctx.engine, "ftf_verification_days", int(window))
            set_app_setting(ctx.engine, "ftf_low_volume_threshold", int(configured_low), bump=False)
            st.success("FTF defaults saved. Existing incidents retain their policy snapshot.")
            st.rerun()
    _coverage_editor(ctx, coverage_df)
    _manual_incident_entry(ctx)
    _ignition_capture(ctx, incidents)
    _relationship_review(ctx, incidents)
    if incidents.empty:
        st.info("No reactive Fiix work orders or reviewed Ignition incidents are available yet.")
        render_pdf_download_button(ctx, "First-Time Fix Incidents", [], [],
                                   notices=["No incidents are available for the selected period."],
                                   key="pdf_ftf_no_incidents", container=pdf_slot)
        return

    weeks = _week_options(incidents)
    default_week = ctx.filters.end_week if ctx.filters.end_week in weeks else weeks[-1]
    selected_week = st.selectbox("Reporting cohort week (first intervention end)", weeks,
                                 index=weeks.index(default_week), format_func=week_label)
    focus = incidents[incidents["cohort_week"] == selected_week].copy()
    filter_values = {}
    for col, options, label in [
        ("initial_shift", sorted(focus.initial_shift.dropna().unique()), "Initial shift"),
        ("owner_label", sorted(focus.owner_label.dropna().unique()), "Primary technician / repair team"),
        ("asset_tl", sorted(focus.asset_tl.dropna().unique()), "Individual asset TL#"),
        ("component", sorted(focus.component.dropna().unique()), "Component / sub-asset"),
        ("failure_mode", sorted(focus.failure_mode.dropna().unique()), "Failure mode"),
        ("failure_reason", sorted(focus.failure_reason.dropna().unique()), "Failure reason"),
        ("parent_asset", sorted(focus.parent_asset.dropna().unique()), "Parent asset (reporting only)"),
        ("production_group", sorted(focus.production_group.dropna().unique()), "Production group (reporting only)"),
        ("ignition_area", sorted(focus.ignition_area.dropna().unique()), "Ignition area (reporting only)"),
    ]:
        selected = st.multiselect(label, options, key=f"ftf_filter_{col}")
        if selected:
            filter_values[col] = selected
            focus = focus[focus[col].isin(selected)]
    if not focus.empty:
        focus["assigned_week"] = focus["cohort_week"]
    pooled = summarize_incidents(focus)
    department_focus = focus[focus["department_eligible"].astype(bool)]
    open_unassigned = incidents[incidents["department_eligible"].astype(bool) &
                                (incidents["cohort_week"].isna() | (incidents["status"] == STATUS_OPEN))]
    baseline_frame = incidents[incidents["department_eligible"].astype(bool)].copy()
    for col, selected in filter_values.items():
        baseline_frame = baseline_frame[baseline_frame[col].isin(selected)]
    baseline = pooled_four_week_baseline(baseline_frame, selected_week)
    pooled = summarize_incidents(department_focus)
    delta = None if pooled["ftf_pct"] is None or baseline["ftf_pct"] is None else pooled["ftf_pct"] - baseline["ftf_pct"]
    low_volume = int(ctx.base.app_settings.get("ftf_low_volume_threshold", 10))
    pdf_metrics = [
        ReportMetric("Selected-week FTF", pooled["ftf_pct"], "%", previous=baseline["ftf_pct"],
                     note="Percentage-point change: " + (f"{delta:+.1f} pp" if delta is not None else "No data")),
        ReportMetric("Successful incidents", pooled["successes"], "count"),
        ReportMetric("Assessed incidents", pooled["assessed"], "count"),
        ReportMetric("Previous four completed weeks (pooled)", baseline["ftf_pct"], "%",
                     note=f"{baseline['successes']} successes / {baseline['assessed']} assessed"),
        ReportMetric("Low-volume threshold", low_volume, "count"),
    ]
    for status, label in [(STATUS_PENDING, "Pending verification"), (STATUS_OPEN, "Open / in progress"),
                          (STATUS_NEEDS_CLASSIFICATION, "Needs classification"), (STATUS_EXCLUDED, "Excluded"),
                          (STATUS_UNRESOLVED, "Unresolved review"), (STATUS_FAILED, "Known repeat failures")]:
        pdf_metrics.append(ReportMetric(label, int((department_focus.status == status).sum()), "count"))
    pdf_sections = []
    pdf_filters = {"Reporting cohort": week_label(selected_week)}
    filter_labels = {"initial_shift": "Initial shift", "owner_label": "Primary technician / repair team",
                     "asset_tl": "Individual asset TL#", "component": "Component / sub-asset",
                     "failure_mode": "Failure mode", "failure_reason": "Failure reason",
                     "parent_asset": "Parent asset", "production_group": "Production group",
                     "ignition_area": "Ignition area"}
    for column, selected in filter_values.items():
        pdf_filters[filter_labels[column]] = ", ".join(map(str, selected))
    first_row = department_focus.iloc[0] if not department_focus.empty else None
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Selected-week FTF", "N/A" if pooled["ftf_pct"] is None else f"{pooled['ftf_pct']:.1f}%",
              delta=None if delta is None else f"{delta:+.1f} pp",
              help="Reviewed incident-based rate from assessed eligible incidents only. Source work orders and calls remain candidates until classified; this is not an asset-level repeat estimate.")
    c2.metric("Successful / assessed", f"{pooled['successes']} / {pooled['assessed']}")
    c3.metric("Previous four completed weeks (pooled)", "N/A" if baseline["ftf_pct"] is None else f"{baseline['ftf_pct']:.1f}%",
              help=f"{baseline['successes']} successes / {baseline['assessed']} assessed incidents")
    c4.metric("Low-volume threshold", f"{low_volume} incidents")
    incomplete_statuses = {STATUS_PENDING, STATUS_OPEN, STATUS_NEEDS_CLASSIFICATION, STATUS_UNRESOLVED}
    cohort_provisional = department_focus["status"].isin(incomplete_statuses).any() or (
        (department_focus["status"] == STATUS_FAILED) & ~department_focus["assessed"]).any()
    c1.caption("Cohort status: Provisional" if cohort_provisional or pooled["assessed"] == 0 else "Cohort status: Finalized")
    if pooled["assessed"] < low_volume:
        st.warning(f"Low sample size: {pooled['assessed']} assessed incidents; configured threshold is {low_volume}.")
    st.caption(f"{len(department_focus):,} incident records in the selected maintenance-department cohort slice; only "
               f"{pooled['assessed']:,} are currently assessed. Excluded and unclassified records are not in the denominator.")
    c = st.columns(6)
    for col, status, label in zip(c, [STATUS_PENDING, STATUS_OPEN, STATUS_NEEDS_CLASSIFICATION, STATUS_EXCLUDED,
                                      STATUS_UNRESOLVED, STATUS_FAILED],
                                  ["Pending verification", "Open / in progress", "Needs classification", "Excluded",
                                   "Unresolved reviews", "Known repeat failures"]):
        col.metric(label, int((department_focus.status == status).sum()))
    awaiting = int(((department_focus.status == STATUS_FAILED) & ~department_focus.assessed).sum())
    if awaiting:
        st.warning(f"Known failures awaiting cohort maturity or complete source coverage: {awaiting} (visible, not yet in denominator).")
    pdf_metrics.append(ReportMetric("Known failures awaiting maturity/coverage", awaiting, "count"))

    coverage_dates = {r.source: r.complete_through for r in coverage_df.itertuples()} if not coverage_df.empty else {}
    latest_imported = {r.source: r.latest_imported_week for r in coverage_df.itertuples()
                       if hasattr(r, "latest_imported_week") and pd.notna(r.latest_imported_week)}
    st.caption(f"Latest imported week: Fiix {latest_imported.get('fiix', 'unknown')} · Ignition {latest_imported.get('ignition', 'unknown')}. "
               f"Reviewer-attested coverage: Fiix {coverage_dates.get('fiix') or 'not attested'} · Ignition {coverage_dates.get('ignition') or 'not attested'} · "
               f"Policy/window is snapshotted per incident. Operating-hour/cycle verification is unavailable because no reliable runtime/cycle source is configured. Last saved recalculation: "
               f"{department_focus['last_calculated_at'].max() if not department_focus.empty else 'not yet calculated'}.")
    pdf_filters["FTF cohort status"] = "Provisional" if cohort_provisional or pooled["assessed"] == 0 else "Finalized"
    for source in ("fiix", "ignition"):
        pdf_filters[f"{source.upper()} complete through"] = coverage_dates.get(source) or "Not attested"
    pdf_filters["Latest imported week"] = ", ".join(
        f"{source.upper()} {latest_imported.get(source, 'unknown')}" for source in ("fiix", "ignition"))

    tabs = st.tabs(["Reporting breakdown", "Incident drill-down / review", "Relationship audit history"])
    with tabs[0]:
        dimension = st.selectbox("Group incidents by", ["Department", "Initial shift", "Technician / team", "Asset TL#",
                                                        "Component / sub-asset", "Failure mode", "Parent asset (report only)",
                                                        "Production group (report only)", "Ignition area (report only)",
                                                        "Failure reason", "Reporting week"])
        key_map = {"Department": None, "Initial shift": "initial_shift", "Technician / team": "owner_label",
                   "Asset TL#": "asset_tl", "Component / sub-asset": "component", "Failure mode": "failure_mode",
                   "Parent asset (report only)": "parent_asset", "Production group (report only)": "production_group",
                   "Ignition area (report only)": "ignition_area",
                   "Failure reason": "failure_reason",
                   "Reporting week": "cohort_week"}
        frame = incidents[incidents["department_eligible"].astype(bool)].copy()
        for col, selected in filter_values.items():
            frame = frame[frame[col].isin(selected)]
        if dimension != "Reporting week":
            frame = frame[frame.cohort_week == selected_week]
        by = key_map[dimension]
        if by is None:
            groups = [("Department", frame)]
        else:
            frame[by] = frame[by].fillna("Needs classification")
            groups = list(frame.groupby(by, dropna=False))
        summaries = []
        for name, group in groups:
            result = summarize_incidents(group)
            summaries.append({"Group": week_label(name) if by == "cohort_week" and str(name).isdigit() else name,
                              "FTF %": result["ftf_pct"], "Successful": result["successes"],
                              "Assessed": result["assessed"],
                              "Pending": int((group.status == STATUS_PENDING).sum()),
                              "Needs classification": int((group.status == STATUS_NEEDS_CLASSIFICATION).sum()),
                              "Unresolved reviews": int((group.status == STATUS_UNRESOLVED).sum())})
        summary = pd.DataFrame(summaries)
        show_table(summary)
        pdf_filters["Breakdown dimension"] = dimension
        pdf_sections.append(ReportSection("Reporting breakdown", table=summary))
        st.download_button("Download breakdown CSV", summary.to_csv(index=False),
                           file_name=f"ftf_breakdown_{selected_week}.csv", mime="text/csv")

    with tabs[1]:
        if not open_unassigned.empty:
            st.markdown("**Open / unassigned first interventions (not assigned to a reporting cohort)**")
            open_columns = [c for c in ["incident_id", "asset_tl", "symptom", "failure_mode",
                                        "technician_name", "repair_team", "initial_shift", "sources"]
                            if c in open_unassigned]
            open_table = open_unassigned[open_columns]
            show_table(open_table)
            pdf_sections.append(ReportSection("Open or unassigned interventions", table=open_table))
        show_cols = ["incident_id", "cohort_week", "asset_tl", "component", "symptom", "failure_mode", "root_cause",
                     "corrective_action", "technician_name", "repair_team", "initial_shift", "intervention_start",
                     "intervention_end", "return_to_service", "verification_deadline", "verification_method",
                     "verification_value", "status", "failure_reason", "assessed", "sources"]
        table = department_focus[[col for col in show_cols if col in department_focus]].copy()
        table["cohort_week"] = table["cohort_week"].map(lambda w: week_label(w) if pd.notna(w) else "Unassigned")
        show_table(table)
        pdf_sections.append(ReportSection("Incident drill-down", table=table,
                           note="Source IDs and evidence are included; local file paths are omitted."))
        st.download_button("Download displayed incidents CSV", table.to_csv(index=False),
                           file_name=f"ftf_incidents_{selected_week}.csv", mime="text/csv")
        selectable = department_focus if not department_focus.empty else incidents
        ids = selectable["incident_id"].astype(str).tolist()
        if ids:
            labels = {str(r.incident_id): f"{r.asset_tl or 'MISSING TL'} · {r.failure_mode or 'Needs classification'} · {r.incident_id[:8]}"
                      for r in selectable.itertuples()}
            chosen = st.selectbox("Edit / classify incident", ids, format_func=labels.get, key="ftf_review_pick")
            selected_incident = selectable[selectable["incident_id"].astype(str) == chosen].iloc[0]
            _review_editor(ctx, selected_incident)
            audit = repo.load_ftf_audit(ctx.engine, chosen)
            audit_columns = {"action": "Action", "reviewer": "Reviewer", "reason": "Reason",
                             "before_json": "Before", "after_json": "After", "created_at": "Reviewed At"}
            show_table(audit, audit_columns)
            pdf_sections.append(ReportSection("Selected incident review history", table=audit,
                                               table_columns=audit_columns))

    with tabs[2]:
        relationship_columns = {"anchor_incident_id": "Original Incident", "event_source": "Source",
                                "event_key": "Record ID", "related_incident_id": "Candidate Incident",
                                "decision": "Decision", "reason": "Review Reason", "reviewer": "Reviewer",
                                "reviewed_at": "Reviewed At"}
        show_table(relationships, relationship_columns)
        pdf_sections.append(ReportSection("Relationship audit history", table=relationships,
                                           table_columns=relationship_columns))
        st.download_button("Download relationship audit CSV", relationships.to_csv(index=False),
                           file_name=f"ftf_relationship_audit_{selected_week}.csv", mime="text/csv")

    notices = ["Incident-based FTF is provisional until its verification window has elapsed and Fiix and Ignition "
               "coverage are attested through the deadline. Missing evidence, open work, exclusions, and unresolved "
               "reviews are not silently treated as successful fixes."]
    render_pdf_download_button(ctx, "First-Time Fix Incidents", pdf_metrics, pdf_sections,
                               extra_filters=pdf_filters, notices=notices,
                               key="pdf_ftf_incidents", container=pdf_slot)
