"""Administration: source paths, refresh/rebuild, technicians, name mapping, shifts, WO types, targets and settings."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from database import repository as repo
from database.database import (
    WO_CLASSIFICATIONS,
    bump_data_version,
    get_app_settings,
    resolve_db_path,
    save_settings,
    set_app_setting,
)
from ingestion.file_manager import SOURCES, pending_files, rebuild_database, refresh_weekly_data, scan_source_files
from kpi import data_quality as dq
from ui.components import show_table
from ui.page_password import protected_screen, render_password_settings
from ui.technician_reports import render_technician_reports


def _flash(message: str) -> None:
    st.session_state["admin_message"] = message
    st.rerun()


@protected_screen
def render(ctx) -> None:
    st.title("Administration")
    if msg := st.session_state.pop("admin_message", None):
        st.success(msg)
    tabs = st.tabs(["Data Sources", "Technicians", "Name Mapping", "Shifts", "WO Types",
                    "KPI Targets & Settings", "Import History", "Backup & Rebuild", "Technician Reports",
                    "Administrative Users", "Password"])
    with tabs[0]:
        _sources(ctx)
    with tabs[1]:
        _technicians(ctx)
    with tabs[2]:
        _name_mapping(ctx)
    with tabs[3]:
        _shifts(ctx)
    with tabs[4]:
        _wo_types(ctx)
    with tabs[5]:
        _targets(ctx)
    with tabs[6]:
        show_table(repo.load_import_history(ctx.engine),
                   {"import_datetime": "Imported", "source": "Source", "filename": "File", "status": "Status",
                    "row_count": "Rows", "inserted_count": "Inserted", "duplicate_count": "Duplicates",
                    "year": "Year", "calendar_week": "Data Week", "filename_week": "Filename Week",
                    "file_modified": "File Modified", "warnings": "Warnings", "full_path": "Path"})
    with tabs[7]:
        _backup_rebuild(ctx)
    with tabs[8]:
        render_technician_reports(ctx)
    with tabs[9]:
        _administrative_users(ctx)
    with tabs[10]:
        render_password_settings(ctx)


@protected_screen
def _administrative_users(ctx) -> None:
    st.caption("Administrative users are separate from the technician master. Active users have full application access.")
    users = repo.load_administrative_users(ctx.engine)
    edited = st.data_editor(
        users,
        num_rows="dynamic",
        hide_index=True,
        key="administrative_users_editor",
        column_order=["display_name", "email", "active"],
        column_config={
            "administrative_user_id": None,
            "display_name": st.column_config.TextColumn("Name", required=True),
            "email": st.column_config.TextColumn("Microsoft account email", required=True),
            "active": st.column_config.CheckboxColumn("Active", default=True),
        },
    )
    if st.button("Save administrative users", type="primary", key="save_administrative_users"):
        try:
            repo.save_administrative_users(ctx.engine, edited.to_dict("records"))
        except ValueError as exc:
            st.error(str(exc))
        else:
            _flash("Administrative users saved.")


@protected_screen
def _sources(ctx) -> None:
    s = dict(ctx.settings)
    st.caption("Point each source at its root folder. Patterns are relative to the folder; `*/*.csv` reads the "
               "year subfolders (2024, 2025, 2026, …) so a new year folder is picked up automatically.")
    with st.form("sources_form"):
        fiix_dir = st.text_input("Fiix weekly reports folder", s["fiix_source_dir"])
        fiix_pat = st.text_input("Fiix file patterns (comma separated)", ", ".join(s["fiix_file_patterns"]))
        ign_dir = st.text_input("Ignition call data folder", s["ignition_source_dir"])
        ign_pat = st.text_input("Ignition file patterns (comma separated)", ", ".join(s["ignition_file_patterns"]))
        sheet = st.text_input("Ignition worksheet", s["ignition_sheet_name"])
        df_dir = st.text_input("Dayforce reports folder", s.get("dayforce_source_dir", ""))
        df_pat = st.text_input("Dayforce file patterns (comma separated)",
                               ", ".join(s.get("dayforce_file_patterns") or ["*.xlsx"]))
        excluded = st.text_input("Dayforce job titles NOT counted as hours worked (comma separated, partial match)",
                                 ", ".join(ctx.base.app_settings.get("dayforce_excluded_jobs") or []))
        dept = st.text_input("Department name", s.get("department_name", ""))
        offset = st.number_input("Production week starts N hours before Monday 00:00", 0.0, 24.0,
                                 float(s.get("week_start_offset_hours", 1)), 0.5,
                                 help="1 = week starts Sunday 23:00. Changing this requires Rebuild Database.")
        if st.form_submit_button("Save settings", type="primary"):
            for label, path in (("Fiix", fiix_dir), ("Ignition", ign_dir), ("Dayforce", df_dir)):
                if not Path(path).exists():
                    st.warning(f"{label} folder not found: {path} (saved anyway).")
            s.update(fiix_source_dir=fiix_dir.strip(), ignition_source_dir=ign_dir.strip(),
                     dayforce_source_dir=df_dir.strip(),
                     fiix_file_patterns=[p.strip() for p in fiix_pat.split(",") if p.strip()],
                     ignition_file_patterns=[p.strip() for p in ign_pat.split(",") if p.strip()],
                     dayforce_file_patterns=[p.strip() for p in df_pat.split(",") if p.strip()],
                     ignition_sheet_name=sheet.strip() or "Dataset 1", department_name=dept.strip(),
                     week_start_offset_hours=offset)
            save_settings(s, engine=ctx.engine)
            set_app_setting(ctx.engine, "dayforce_excluded_jobs",
                            [p.strip() for p in excluded.split(",") if p.strip()])
            _flash("Settings saved.")

    pending = pending_files(ctx.engine, ctx.settings)
    cols = st.columns(len(SOURCES))
    for col, (name, spec) in zip(cols, SOURCES.items()):
        total = len(scan_source_files(spec, ctx.settings))
        col.metric(f"{spec.label} files found", total, help=f"{len(pending[name])} not yet imported")
        col.caption(f"{len(pending[name])} new file(s) pending import")
    if st.button("Refresh Weekly Data", icon=":material/sync:", key="admin_refresh"):
        bar = st.progress(0.0)
        outcomes = refresh_weekly_data(ctx.engine, ctx.settings,
                                       lambda i, n, m: bar.progress(i / max(n, 1), text=f"{i}/{n} {m}"))
        _flash(f"Processed {len(outcomes)} file(s); {sum(o.status == 'failed' for o in outcomes)} failed.")

    with st.form("auto_refresh_form"):
        minutes = st.number_input("Automatic scan interval while the dashboard is open (minutes, 0 = off)", 0, 1440,
                                  int(get_app_settings(ctx.engine).get("auto_refresh_minutes", 60)), 5)
        if st.form_submit_button("Save interval"):
            set_app_setting(ctx.engine, "auto_refresh_minutes", int(minutes), bump=False)
            _flash("Automatic scan interval saved." if minutes else "Automatic scanning turned off.")


NO_SHIFT = "(Not assigned)"
_TECH_SEARCH_COLS = ["technician_name", "fiix_name", "ignition_name", "dayforce_name", "dayforce_id", "crew",
                     "supervisor"]


def _filter_technicians(techs: pd.DataFrame, shifts: list[str]) -> tuple[pd.DataFrame, str]:
    c1, c2, c3, c4, c5 = st.columns([3, 1.2, 2, 1.5, 1.5])
    search = c1.text_input("Search", key="tf_search", placeholder="Name, Fiix/Ignition/Dayforce name, crew, supervisor")
    status = c2.selectbox("Status", ["All", "Active", "Inactive", "Leads", "Needs review"], key="tf_status")
    sel_shifts = c3.multiselect("Shift", shifts + [NO_SHIFT], key="tf_shift")
    sel_crews = c4.multiselect("Crew", sorted(techs["crew"].dropna().unique()), key="tf_crew")
    sel_sups = c5.multiselect("Supervisor", sorted(techs["supervisor"].dropna().unique()), key="tf_sup")

    mask = pd.Series(True, index=techs.index)
    if search.strip():
        text = techs[_TECH_SEARCH_COLS].fillna("").astype(str).agg(" ".join, axis=1).str.casefold()
        mask &= text.str.contains(search.strip().casefold(), regex=False)
    if status == "Leads":
        mask &= techs["is_lead"]
    elif status == "Needs review":
        mask &= techs["needs_review"]
    elif status != "All":
        mask &= techs["active"] == (status == "Active")
    if sel_shifts:
        mask &= techs["shift"].fillna(NO_SHIFT).isin(sel_shifts)
    if sel_crews:
        mask &= techs["crew"].isin(sel_crews)
    if sel_sups:
        mask &= techs["supervisor"].isin(sel_sups)
    signature = "|".join([search, status, *sel_shifts, *sel_crews, *sel_sups])
    return techs[mask], signature


@protected_screen
def _review_new_technicians(ctx, techs: pd.DataFrame) -> None:
    new = techs[techs["needs_review"]]
    if new.empty:
        return
    with st.container(border=True):
        st.markdown(f":material/person_add: **{len(new)} new technician(s) added automatically from Dayforce** — "
                    "fill in shift, crew, supervisor and Fiix / Ignition names below, then confirm. "
                    "If a person already exists under another name, link them instead.")
        show_table(new, {"technician_name": "Technician", "dayforce_name": "Dayforce Name",
                         "dayforce_id": "Dayforce ID", "is_lead": "Lead", "shift": "Shift"})
        names = dict(zip(new["technician_id"].astype(int), new["technician_name"]))
        c1, c2 = st.columns(2)
        with c1:
            pick = st.multiselect("New technicians", list(names), format_func=names.get, key="review_pick")
            if st.button("Confirm as new technician(s)", disabled=not pick, key="review_confirm",
                         help="Clears the review flag. Make sure Shift is set in the table below first."):
                repo.mark_technicians_reviewed(ctx.engine, pick)
                _flash(f"Confirmed {len(pick)} technician(s).")
        with c2:
            others = techs[~techs["needs_review"]]
            other_names = dict(zip(others["technician_id"].astype(int), others["technician_name"]))
            source = st.selectbox("Same person as an existing technician?", list(names), index=None,
                                  format_func=names.get, key="review_link_src")
            target = st.selectbox("Existing technician", sorted(other_names, key=other_names.get), index=None,
                                  format_func=other_names.get, key="review_link_dst")
            if st.button("Link and remove duplicate", disabled=source is None or target is None, key="review_link"):
                repo.link_new_technician(ctx.engine, int(source), int(target))
                _flash(f"Linked {names[source]} to {other_names[target]} (Dayforce ID moved; duplicate removed).")


@protected_screen
def _technicians(ctx) -> None:
    st.caption("Shift is assigned here only — it is never inferred from work-order times. "
               "Fiix / Ignition / Dayforce name columns are matched exactly (case-insensitive).")
    techs = repo.load_technicians(ctx.engine)
    shifts = ctx.base.shifts["name"].tolist()
    _review_new_technicians(ctx, techs)
    view, signature = _filter_technicians(techs, shifts)
    st.caption(f"Showing {len(view)} of {len(techs)} technicians. Only the rows shown are saved; "
               "new rows can be added at the bottom of the table.")
    edited = st.data_editor(
        view, num_rows="dynamic", hide_index=True,
        key=f"tech_editor_{ctx.version}_{abs(hash(signature))}",
        column_order=["technician_name", "shift", "crew", "department", "supervisor", "active", "is_lead",
                      "needs_review", "fiix_name", "ignition_name", "dayforce_name", "dayforce_id", "start_date",
                      "end_date"],
        column_config={
            "technician_name": st.column_config.TextColumn("Technician", required=True),
            "shift": st.column_config.SelectboxColumn("Shift", options=shifts),
            "crew": "Crew", "department": "Department", "supervisor": "Supervisor",
            "active": st.column_config.CheckboxColumn("Active", default=True),
            "is_lead": st.column_config.CheckboxColumn(
                "Lead", default=False, help="Leads are excluded from department and shift Labor Efficiency."),
            "needs_review": st.column_config.CheckboxColumn(
                "Needs review", default=False, help="Auto-created from Dayforce. Untick and save once details are filled in."),
            "fiix_name": "Fiix Name", "ignition_name": "Ignition Name", "dayforce_name": "Dayforce Name",
            "dayforce_id": st.column_config.TextColumn("Dayforce ID"),
            "start_date": st.column_config.DateColumn("Start Date"),
            "end_date": st.column_config.DateColumn("End Date"),
        },
    )
    if st.button("Save technicians", type="primary", key="save_techs"):
        hidden = techs[~techs["technician_id"].isin(view["technician_id"])]
        names = pd.concat([edited["technician_name"], hidden["technician_name"]]).dropna().str.strip().str.casefold()
        if names.duplicated().any():
            st.error("Technician names must be unique.")
            return
        kept = set(edited["technician_id"].dropna().astype(int))
        removed = [int(i) for i in view["technician_id"] if int(i) not in kept]
        repo.save_technicians(ctx.engine, edited.to_dict("records"), deactivate_ids=removed)
        note = f" {len(removed)} removed row(s) were set inactive (history is preserved)." if removed else ""
        _flash(f"Technicians saved.{note}")


@protected_screen
def _name_mapping(ctx) -> None:
    unmapped = dq.unmapped_names(ctx.base.fiix, ctx.base.calls, dq.counted_dayforce_entries(ctx.base))
    techs = ctx.base.technicians
    st.caption("Names in Fiix (Completed By) or Ignition (TechName) that do not match a technician. "
               "Nothing is matched automatically beyond exact, case-insensitive names.")
    if unmapped.empty:
        st.success("All Fiix and Ignition names are mapped.")
    else:
        show_table(unmapped, {"source": "Source", "name": "Name", "records": "Records",
                              "first_seen": "First Seen", "last_seen": "Last Seen"}, height=300)
        labels = {f"{r.source}: {r.name}": (r.source, r.name) for r in unmapped.itertuples()}
        picked = st.multiselect("Names to resolve", list(labels), key="map_pick")
        c1, c2 = st.columns(2)
        with c1:
            tech_ids = techs["technician_id"].astype(int).tolist()
            name_of = dict(zip(tech_ids, techs["technician_name"]))
            target = st.selectbox("Map selected names to existing technician", tech_ids, index=None,
                                  format_func=lambda i: name_of[i], key="map_target")
            if st.button("Map as alias", disabled=not picked or target is None, key="map_go"):
                for label in picked:
                    source, name = labels[label]
                    repo.add_alias(ctx.engine, source, name, target)
                _flash(f"Mapped {len(picked)} name(s) to {name_of[target]}.")
        with c2:
            st.caption("Or create one new technician per selected name (shift must then be set on the Technicians tab).")
            if st.button("Create technicians from selected names", disabled=not picked, key="map_create"):
                existing = {n.casefold(): int(i) for i, n in zip(techs["technician_id"], techs["technician_name"])}
                for label in picked:
                    source, name = labels[label]
                    tech_id = existing.get(name.casefold())
                    if tech_id is None:
                        tech_id = repo.create_technician(ctx.engine, name, **{f"{source}_name": name})
                        existing[name.casefold()] = tech_id
                    else:
                        repo.add_alias(ctx.engine, source, name, tech_id)
                _flash(f"Created/linked {len(picked)} technician(s). Assign their shifts on the Technicians tab.")

    st.divider()
    st.markdown("**Existing aliases**")
    aliases = repo.load_aliases(ctx.engine)
    if aliases.empty:
        st.caption("No aliases defined.")
        return
    view = aliases[["alias_id", "source", "alias", "technician_name"]].assign(remove=False)
    edited = st.data_editor(view, hide_index=True, disabled=["source", "alias", "technician_name"],
                            column_config={"alias_id": None, "remove": st.column_config.CheckboxColumn("Remove")},
                            key=f"alias_editor_{ctx.version}")
    ids = edited.loc[edited["remove"], "alias_id"].astype(int).tolist()
    if st.button(f"Remove {len(ids)} alias(es)", disabled=not ids, key="alias_remove"):
        repo.delete_aliases(ctx.engine, ids)
        _flash("Aliases removed.")


@protected_screen
def _shifts(ctx) -> None:
    edited = st.data_editor(repo.load_shifts(ctx.engine), num_rows="dynamic", hide_index=True,
                            key=f"shift_editor_{ctx.version}",
                            column_config={"shift_id": None, "name": st.column_config.TextColumn("Shift", required=True),
                                           "sort_order": st.column_config.NumberColumn("Sort", step=1),
                                           "active": st.column_config.CheckboxColumn("Active", default=True)})
    st.caption("Renaming a shift does not update technicians already assigned to the old name.")
    if st.button("Save shifts", type="primary", key="save_shifts"):
        repo.save_shifts(ctx.engine, edited.to_dict("records"))
        _flash("Shifts saved.")


@protected_screen
def _wo_types(ctx) -> None:
    wo = repo.load_wo_types(ctx.engine)
    new = int((~wo["reviewed"]).sum())
    if new:
        st.warning(f"{new} newly discovered WO Type(s) need review (classification defaults to Other).")
    st.caption("Counts toward labor → included in Labor Efficiency. PM → counted as completed PMs. "
               "First-Time Fix uses every WO Type containing 'Reactive'.")
    edited = st.data_editor(
        wo, hide_index=True, key=f"wo_editor_{ctx.version}", disabled=["wo_type", "first_seen"],
        column_config={
            "wo_type": "WO Type",
            "classification": st.column_config.SelectboxColumn("Classification", options=WO_CLASSIFICATIONS, required=True),
            "counts_toward_labor": st.column_config.CheckboxColumn("Counts toward labor"),
            "ftf_qualifying": None,
            "is_pm": st.column_config.CheckboxColumn("PM"),
            "reviewed": st.column_config.CheckboxColumn("Reviewed"),
            "first_seen": st.column_config.DatetimeColumn("First seen", format="YYYY-MM-DD"),
        })
    if st.button("Save WO Types", type="primary", key="save_wo"):
        repo.save_wo_types(ctx.engine, edited.to_dict("records"))
        _flash("WO Type configuration saved.")


@protected_screen
def _targets(ctx) -> None:
    st.markdown("**KPI targets**")
    st.caption("Watch band: values within this distance of target show *Watch*. "
               "Durations: lower is better. Percentages: higher is better.")
    edited = st.data_editor(
        repo.load_kpi_targets(ctx.engine), hide_index=True, key=f"target_editor_{ctx.version}",
        disabled=["kpi_key", "label", "direction", "unit"],
        column_config={"kpi_key": None, "label": "KPI", "target": st.column_config.NumberColumn("Target", format="%.2f"),
                       "watch_band": st.column_config.NumberColumn("Watch band", format="%.2f"),
                       "direction": "Better when", "unit": "Unit"})
    if st.button("Save targets", type="primary", key="save_targets"):
        repo.save_kpi_targets(ctx.engine, edited.to_dict("records"))
        _flash("Targets saved.")

    st.divider()
    s = ctx.base.app_settings
    with st.form("kpi_settings"):
        st.markdown("**First-Time Fix rules**")
        window = st.slider("Repeat failure window (days)", 1, 30, int(s.get("ftf_window_days", 3)))
        rules = {"asset_number": "Asset number (TL), exact match", "asset": "Asset name",
                 "parent_asset": "Parent Asset", "asset_then_parent": "Asset name, fall back to Parent Asset when blank"}
        rule = st.radio("Asset matching rule", list(rules), format_func=rules.get,
                        index=list(rules).index(s.get("ftf_asset_rule", "asset_number")))
        tech = st.checkbox("Only WOs completed by a technician (Completed By not blank)",
                           bool(s.get("ftf_require_technician", True)))
        dup = st.checkbox("Exclude duplicate WOs (status contains 'Duplicate')", bool(s.get("ftf_exclude_duplicates", True)))
        adm = st.checkbox("Exclude administrative closures (status contains 'Admin')",
                          bool(s.get("ftf_exclude_admin_closures", True)))
        st.caption("Qualifying WO Types: every WO Type whose name contains 'Reactive'. With asset-number matching, "
                   "TL values that are not asset numbers (PLANT4, PROD-…, Ignition_HotlineCall) are not evaluated.")
        st.markdown("**Labor efficiency & display**")
        warn = st.number_input("Labor efficiency warning threshold (%)", 0.0, 1000.0,
                               float(s.get("labor_efficiency_warning_pct", 120)), 5.0)
        max_wo = st.number_input("Max Fiix Time Spent per work order (h, 0 = no limit)", 0.0, 1000.0,
                                 float(s.get("max_time_spent_hours_per_wo", 24) or 0), 1.0,
                                 help="Larger values are treated as data errors (e.g. elapsed time), left out of "
                                      "labor hours and listed in Data Quality.")
        weeks = st.slider("Trend chart weeks", 4, 52, int(s.get("trend_weeks", 12)))
        st.markdown("**Shift Performance areas**")
        area_names = list((s.get("crew_areas") or {}).keys())
        reserved = st.multiselect("Areas reserved for future use (not reported yet)", area_names,
                                  default=[a for a in (s.get("reserved_crew_areas") or []) if a in area_names])
        if st.form_submit_button("Save settings", type="primary"):
            for key, value in (("ftf_window_days", window), ("ftf_asset_rule", rule),
                               ("ftf_require_technician", tech), ("ftf_exclude_duplicates", dup),
                               ("ftf_exclude_admin_closures", adm), ("labor_efficiency_warning_pct", warn),
                               ("max_time_spent_hours_per_wo", max_wo), ("trend_weeks", weeks),
                               ("reserved_crew_areas", reserved)):
                set_app_setting(ctx.engine, key, value, bump=False)
            bump_data_version(ctx.engine)
            _flash("KPI settings saved.")


@protected_screen
def _backup_rebuild(ctx) -> None:
    db_path = resolve_db_path(ctx.settings)
    st.markdown(f"**Database:** `{db_path}`")
    if st.button("Create backup copy", icon=":material/backup:", key="backup"):
        target = db_path.parent / "backups" / f"maintenance_metrics_{datetime.now():%Y%m%d_%H%M%S}.db"
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
        _flash(f"Backup written to {target}")

    st.divider()
    st.markdown("**Rebuild Database From Source Files**")
    st.caption("Deletes all imported Fiix work orders, Ignition calls, import history and import issues, then "
               "re-imports every file in the source folders. Technicians, aliases, Dayforce hours, PM targets, "
               "WO Type configuration, targets and settings are kept. Create a backup first.")
    confirm = st.checkbox("I understand imported data will be deleted and re-imported.", key="rebuild_ok")
    typed = st.text_input("Type REBUILD to confirm", key="rebuild_text")
    if st.button("Rebuild database", type="primary", disabled=not (confirm and typed == "REBUILD"), key="rebuild"):
        bar = st.progress(0.0)
        outcomes = rebuild_database(ctx.engine, ctx.settings,
                                    lambda i, n, m: bar.progress(i / max(n, 1), text=f"{i}/{n} {m}"))
        _flash(f"Rebuild complete: {len(outcomes)} file(s), {sum(o.status == 'failed' for o in outcomes)} failed.")
