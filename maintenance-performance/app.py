"""Maintenance Performance KPI Dashboard — Streamlit entry point.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from database import repository as repo
from database.database import get_app_settings, get_data_version, load_settings, resolve_db_path, set_app_setting
from ingestion.file_manager import refresh_weekly_data
from kpi.dataset import apply_filters
from pages import (
    administration,
    data_entry,
    data_quality,
    executive_dashboard,
    fiix_dashboard,
    ftf_dashboard,
    ignition_dashboard,
    shift_dashboard,
    technician_dashboard,
)
from ui.auto_refresh import auto_refresh
from ui.access_control import (
    auth_is_configured,
    create_page_password,
    identity_email,
    page_is_visible,
    verify_page_password,
)
from ui.context import DashboardContext, get_engine, kpi_targets_dict, load_base, render_global_filters
from ui.export_view import build_export

st.set_page_config(page_title="Maintenance Performance", page_icon=":material/engineering:", layout="wide")

AUTHENTICATION_ENABLED = False

settings = load_settings()
db_path = str(resolve_db_path(settings))
engine = get_engine(db_path)
repo.ensure_bootstrap_administrator(engine, settings.get("bootstrap_admin_email", ""), "David Sharkey")


def _auth_setup_page() -> None:
    st.title("Maintenance Performance")
    st.error("Microsoft sign-in is not configured. Add the local Streamlit auth settings described in the README.")


def _sign_in_page() -> None:
    st.title("Maintenance Performance")
    st.button("Sign in with Microsoft", on_click=st.login, type="primary")


def _identity_error_page() -> None:
    st.error("Your Microsoft account did not provide an email or user principal name. Contact an administrator.")


if AUTHENTICATION_ENABLED:
    try:
        configured_auth = auth_is_configured(st.secrets)
    except Exception:
        configured_auth = False

    if not configured_auth:
        st.navigation([st.Page(_auth_setup_page, title="Authentication Setup", url_path="auth-setup")]).run()
        st.stop()

    if not st.user.is_logged_in:
        st.navigation([st.Page(_sign_in_page, title="Sign In", url_path="sign-in")]).run()
        st.stop()

    user_email = identity_email(st.user)
    if not user_email:
        st.navigation([st.Page(_identity_error_page, title="Account Check", url_path="account-check")]).run()
        st.stop()

    is_admin = repo.is_active_administrator(engine, user_email)
else:
    user_email = "Local user"
    is_admin = True


def run_refresh() -> None:
    bar = st.sidebar.progress(0.0, text="Scanning source folders…")

    def progress(i, total, msg):
        bar.progress(i / max(total, 1), text=f"{i}/{total} {msg}")

    outcomes = refresh_weekly_data(engine, settings, progress)
    bar.empty()
    ok = sum(o.status == "success" for o in outcomes)
    failed = [o for o in outcomes if o.status == "failed"]
    inserted = sum(o.inserted for o in outcomes)
    st.session_state["refresh_message"] = (
        f"Imported {ok} new file(s), {inserted:,} new records."
        + (f" {len(failed)} file(s) failed — see Data Quality." if failed else "")
        if outcomes else "No new files found."
    )
    st.session_state.pop("export_bytes", None)


with st.sidebar:
    st.markdown("### Maintenance Performance")
    if AUTHENTICATION_ENABLED:
        st.caption(user_email)
        st.button("Sign out", on_click=st.logout, width="stretch")
    else:
        st.caption("Local access: authentication disabled")
    if not is_admin:
        st.caption("Read-only access")
    if is_admin:
        if st.button("Refresh Weekly Data", icon=":material/sync:", type="primary", width="stretch"):
            run_refresh()
        if msg := st.session_state.pop("refresh_message", None):
            st.success(msg)
        password_settings = get_app_settings(engine).get("admin_page_password")
        password_storage = "database"
        if not isinstance(password_settings, dict) or not password_settings.get("hash") or not password_settings.get("salt"):
            try:
                password_settings = st.secrets.get("admin_page_password", {})
            except Exception:
                password_settings = {}
            password_storage = "secrets"
        password_hash = str(password_settings.get("hash", "")).strip()
        password_salt = str(password_settings.get("salt", "")).strip()
        password_is_configured = bool(password_hash and password_salt)
        if st.session_state.get("admin_pages_unlocked", False):
            if st.button("Lock Administration and Data Entry", icon=":material/lock:", width="stretch"):
                st.session_state["admin_pages_unlocked"] = False
                st.rerun()
        elif not password_is_configured:
            with st.form("admin_page_password_setup_form", clear_on_submit=True):
                st.caption("Create a password of at least 12 characters. It will be saved as a salted hash in the application database.")
                new_password = st.text_input("New Administration/Data Entry password", type="password")
                confirm_password = st.text_input("Confirm password", type="password")
                submitted = st.form_submit_button("Create password and unlock", width="stretch")
            if submitted:
                try:
                    if new_password != confirm_password:
                        raise ValueError("Passwords do not match.")
                    password_settings = create_page_password(new_password)
                except ValueError as exc:
                    st.session_state["admin_page_password_error"] = str(exc)
                else:
                    set_app_setting(engine, "admin_page_password", password_settings, bump=False)
                    st.session_state["admin_pages_unlocked"] = True
                    st.session_state.pop("admin_page_password_error", None)
                    st.rerun()
            if error := st.session_state.pop("admin_page_password_error", None):
                st.sidebar.error(error)
        else:
            with st.form("admin_pages_unlock_form", clear_on_submit=True):
                page_password = st.text_input("Administration/Data Entry password", type="password")
                submitted = st.form_submit_button("Unlock protected pages", width="stretch")
            if submitted:
                if password_hash and password_salt and verify_page_password(
                        page_password, password_salt, password_hash):
                    if password_storage == "secrets":
                        set_app_setting(engine, "admin_page_password", {
                            "salt": password_salt, "hash": password_hash,
                        }, bump=False)
                    st.session_state["admin_pages_unlocked"] = True
                    st.session_state.pop("admin_page_password_error", None)
                    st.rerun()
                st.session_state["admin_page_password_error"] = "Incorrect password."
            if error := st.session_state.pop("admin_page_password_error", None):
                st.sidebar.error(error)

version = get_data_version(engine)
base = load_base(engine, db_path, version)

if is_admin:
    to_review = base.technicians.loc[base.technicians.get("needs_review", False) == True, "technician_name"].tolist()  # noqa: E712
    if to_review:
        st.sidebar.warning(
            f"**{len(to_review)} new technician(s) from Dayforce need details:** {', '.join(to_review)}. "
            "Open Administration → Technicians to set shift, Fiix/Ignition names and lead status.",
            icon=":material/person_add:")

if is_admin:
    with st.sidebar:
        auto_refresh(engine, settings, version)

with st.sidebar:
    with st.expander("Filters", expanded=True, icon=":material/filter_alt:"):
        filters = render_global_filters(base, float(settings.get("week_start_offset_hours", 1)), is_admin)

ctx = DashboardContext(engine=engine, settings=settings, base=base, filters=filters,
                       inputs=apply_filters(base, filters), targets=kpi_targets_dict(engine), version=version,
                       is_admin=is_admin)

if is_admin:
    with st.sidebar:
        with st.expander("Export to Excel", icon=":material/download:"):
            st.caption("Exports detailed KPI tables for the current filters.")
            if st.button("Prepare export", disabled=not base.has_data, width="stretch"):
                with st.spinner("Building workbook…"):
                    st.session_state["export_bytes"] = build_export(ctx)
            if data := st.session_state.get("export_bytes"):
                st.download_button("Download .xlsx", data=data,
                                   file_name=f"maintenance_kpis_{filters.label}.xlsx".replace(" ", "_"),
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   width="stretch")


def _page(module, title, icon, url, default=False):
    return st.Page(lambda: module.render(ctx), title=title, icon=icon, url_path=url, default=default)


page_entries = [
    ("executive", _page(executive_dashboard, "Executive Dashboard", ":material/dashboard:", "executive", default=True)),
    ("technicians", _page(technician_dashboard, "Technician Performance", ":material/person:", "technicians")),
    ("shifts", _page(shift_dashboard, "Shift Performance", ":material/groups:", "shifts")),
    ("ignition", _page(ignition_dashboard, "Ignition Performance", ":material/notifications_active:", "ignition")),
    ("fiix", _page(fiix_dashboard, "Fiix Performance", ":material/build:", "fiix")),
    ("ftf-incidents", _page(ftf_dashboard, "First-Time Fix Incidents", ":material/fact_check:", "ftf-incidents")),
    ("data-entry", _page(data_entry, "Data Entry", ":material/edit_note:", "data-entry")),
    ("data-quality", _page(data_quality, "Data Quality", ":material/fact_check:", "data-quality")),
    ("admin", _page(administration, "Administration", ":material/settings:", "admin")),
]
password_unlocked = st.session_state.get("admin_pages_unlocked", False)
visible_pages = [page for key, page in page_entries if page_is_visible(key, is_admin, password_unlocked)]
nav = st.navigation(visible_pages)
nav.run()
