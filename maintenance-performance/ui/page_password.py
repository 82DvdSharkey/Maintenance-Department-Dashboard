"""Streamlit entry and operation guards for the two password-protected menus."""
from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Callable, Concatenate, ParamSpec, TypeVar

import streamlit as st

from database.page_access import (
    PasswordSettingsError,
    load_page_password,
    lock_pages,
    page_operation_access,
    pages_unlocked,
    save_page_password,
    unlock_pages,
)

if TYPE_CHECKING:
    from ui.context import DashboardContext

P = ParamSpec("P")
T = TypeVar("T")


def _safe_screen(ctx: DashboardContext) -> None:
    if ctx.home_page is None:
        raise RuntimeError("The safe navigation page has not been configured.")
    st.switch_page(ctx.home_page)


def require_page_access(ctx: DashboardContext) -> None:
    try:
        if pages_unlocked(ctx.engine, st.session_state, ctx.is_admin):
            return
    except PasswordSettingsError as exc:
        st.error(str(exc))
        st.stop()
    if not ctx.is_admin:
        st.error("Administrator access is required.")
        st.stop()
    st.title("Administration and Data Entry are locked")
    with st.form("protected_pages_unlock", clear_on_submit=True):
        password = st.text_input("Password", type="password")
        unlock = st.form_submit_button("Unlock")
        cancel = st.form_submit_button("Cancel")
    if cancel:
        lock_pages(st.session_state)
        _safe_screen(ctx)
        st.stop()
    if unlock:
        try:
            unlock_pages(ctx.engine, st.session_state, password, ctx.is_admin)
        except (PermissionError, PasswordSettingsError) as exc:
            st.error(str(exc))
        else:
            st.rerun()
    st.stop()


def protected_screen(
    function: Callable[Concatenate[DashboardContext, P], T],
) -> Callable[Concatenate[DashboardContext, P], T | None]:
    @wraps(function)
    def guarded(ctx: DashboardContext, *args: P.args, **kwargs: P.kwargs) -> T | None:
        require_page_access(ctx)
        try:
            with page_operation_access(ctx.engine, st.session_state, ctx.is_admin):
                return function(ctx, *args, **kwargs)
        except (PermissionError, PasswordSettingsError) as exc:
            st.error(str(exc))
            st.stop()
    return guarded


@protected_screen
def render_password_settings(ctx: DashboardContext) -> None:
    st.subheader("Administration and Data Entry password")
    configured = load_page_password(ctx.engine) is not None
    st.caption("One shared password protects both menus. Saving locks both menus immediately.")
    with st.form("page_password_settings", clear_on_submit=True):
        current = st.text_input("Current Password", type="password") if configured else ""
        new = st.text_input("New Password", type="password")
        confirm = st.text_input("Confirm Password", type="password")
        submitted = st.form_submit_button("Save Password", type="primary")
    if submitted:
        try:
            save_page_password(ctx.engine, st.session_state, new, confirm, current, is_admin=ctx.is_admin)
        except (ValueError, PermissionError, PasswordSettingsError) as exc:
            st.error(str(exc))
        else:
            st.session_state["page_password_message"] = "Password saved. Administration and Data Entry are locked."
            _safe_screen(ctx)
