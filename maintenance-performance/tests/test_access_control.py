import pytest

from database import repository as repo
from database.database import create_db_engine, init_db
from database.repository import (
    ensure_bootstrap_administrator,
    is_active_administrator,
    load_administrative_users,
    save_administrative_users,
)
from ui.access_control import (
    AUTH_REQUIRED_SETTINGS,
    READ_ONLY_PAGES,
    allowed_pages,
    auth_is_configured,
    identity_email,
    is_admin_email,
    normalize_email,
)


def test_read_only_users_only_get_approved_dashboard_pages():
    assert allowed_pages(False) == READ_ONLY_PAGES
    assert READ_ONLY_PAGES == {"executive", "shifts", "ignition", "fiix"}


def test_admin_gets_all_pages():
    assert allowed_pages(True) is None


def test_admin_email_matching_is_case_insensitive_and_trimmed():
    assert is_admin_email(" David.Sharkey@Magna.Com ", ["david.sharkey@magna.com"])
    assert not is_admin_email("", ["david.sharkey@magna.com"])
    assert normalize_email(None) == ""


def test_identity_email_uses_available_microsoft_identity_claim():
    assert identity_email({"preferred_username": "David.Sharkey@Magna.Com"}) == "david.sharkey@magna.com"
    assert identity_email({"upn": "other@magna.com"}) == "other@magna.com"
    assert identity_email({}) == ""


def test_auth_configuration_requires_all_credentials_and_endpoints():
    assert not auth_is_configured({})
    assert auth_is_configured({"auth": {key: "configured" for key in AUTH_REQUIRED_SETTINGS}})
    assert not auth_is_configured({"auth": {"client_id": "configured"}})


def test_bootstrap_admin_is_created_once_and_matches_case_insensitively():
    engine = create_db_engine(":memory:")
    init_db(engine)
    ensure_bootstrap_administrator(engine, "David.Sharkey@Magna.Com", "David Sharkey")
    ensure_bootstrap_administrator(engine, "other@magna.com", "Other User")

    assert is_active_administrator(engine, "david.sharkey@magna.com")
    assert not is_active_administrator(engine, "other@magna.com")
    assert len(load_administrative_users(engine)) == 1


def test_admin_directory_edits_and_requires_an_active_admin():
    engine = create_db_engine(":memory:")
    init_db(engine)
    ensure_bootstrap_administrator(engine, "admin@magna.com", "Admin")
    initial = load_administrative_users(engine).to_dict("records")
    initial[0]["display_name"] = "Updated Admin"
    initial.append({"administrative_user_id": None, "email": "second@magna.com",
                    "display_name": "Second Admin", "active": True})
    save_administrative_users(engine, initial)

    users = load_administrative_users(engine)
    assert set(users["email"]) == {"admin@magna.com", "second@magna.com"}
    assert users.loc[users["email"] == "admin@magna.com", "display_name"].iloc[0] == "Updated Admin"
    with pytest.raises(ValueError, match="At least one administrative user"):
        save_administrative_users(engine, [{**row, "active": False} for row in users.to_dict("records")])


@pytest.fixture
def password_engine(tmp_path):
    engine = create_db_engine(tmp_path / "passwords.db")
    init_db(engine)
    yield engine
    engine.dispose()


def test_initial_password_validation_and_failed_save_leave_setup_incomplete(password_engine):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    from database.page_access import (
        PasswordSettingsError, load_page_password, pages_unlocked, save_page_password,
    )

    session = {}
    for new, confirm in (("", ""), ("secret", ""), ("", "secret"), ("secret", "different")):
        with pytest.raises(ValueError):
            save_page_password(password_engine, session, new, confirm, is_admin=True)
        assert load_page_password(password_engine) is None
        assert pages_unlocked(password_engine, session, True)

    def fail_save(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO app_settings"):
            raise OperationalError("injected write failure", None, None)

    event.listen(password_engine, "before_cursor_execute", fail_save)
    try:
        with pytest.raises(PasswordSettingsError, match="could not be saved"):
            save_page_password(password_engine, session, "secret", "secret", is_admin=True)
    finally:
        event.remove(password_engine, "before_cursor_execute", fail_save)
    assert load_page_password(password_engine) is None
    assert pages_unlocked(password_engine, session, True)


def test_saved_password_locks_unlocks_cancels_and_requires_current_password_for_replacement(password_engine):
    from database.database import get_app_settings
    from database.page_access import (
        load_page_password, lock_pages, pages_unlocked, save_page_password, unlock_pages,
    )

    session, other_session = {}, {}
    save_page_password(password_engine, session, "original", "original", is_admin=True)
    config = load_page_password(password_engine)
    assert "original" not in str(config)
    assert "admin_page_password" not in get_app_settings(password_engine)
    assert not pages_unlocked(password_engine, session, True)
    with pytest.raises(PermissionError, match="Incorrect"):
        unlock_pages(password_engine, session, "wrong", True)
    assert not pages_unlocked(password_engine, session, True)
    unlock_pages(password_engine, session, "original", True)
    assert pages_unlocked(password_engine, session, True)
    lock_pages(session)
    assert not pages_unlocked(password_engine, session, True)
    unlock_pages(password_engine, session, "original", True)
    unlock_pages(password_engine, other_session, "original", True)
    for current in ("", "wrong"):
        with pytest.raises(PermissionError, match="current password"):
            save_page_password(password_engine, session, "replacement", "replacement", current, is_admin=True)
        assert load_page_password(password_engine) == config
    save_page_password(password_engine, session, "replacement", "replacement", "original", is_admin=True)
    assert not pages_unlocked(password_engine, session, True)
    assert not pages_unlocked(password_engine, other_session, True)
    assert load_page_password(password_engine)["salt"] != config["salt"]
    with pytest.raises(PermissionError):
        unlock_pages(password_engine, session, "original", True)
    unlock_pages(password_engine, session, "replacement", True)
    assert pages_unlocked(password_engine, session, True)
    assert not pages_unlocked(password_engine, session, False)
    with pytest.raises(PermissionError):
        save_page_password(password_engine, {}, "new", "new", "replacement", is_admin=False)


@pytest.mark.parametrize("operation", [
    lambda e: repo.save_administrative_users(e, []),
    lambda e: repo.save_technicians(e, []),
    lambda e: repo.create_technician(e, "Unauthorized"),
    lambda e: repo.mark_technicians_reviewed(e, []),
    lambda e: repo.link_new_technician(e, 1, 2),
    lambda e: repo.add_alias(e, "fiix", "Unauthorized", 1),
    lambda e: repo.delete_aliases(e, []),
    lambda e: repo.save_shifts(e, []),
    lambda e: repo.save_wo_types(e, []),
    lambda e: repo.save_kpi_targets(e, []),
    lambda e: repo.set_week_lock(e, "pm", 2026, 40, True),
    lambda e: repo.save_dayforce_week(e, 2026, 40, []),
    lambda e: repo.save_pm_status_week(e, 2026, 40, []),
])
def test_direct_repository_operations_cannot_bypass_lock(password_engine, operation):
    from database.page_access import save_page_password

    save_page_password(password_engine, {}, "secret", "secret", is_admin=True)
    with pytest.raises(PermissionError, match="locked"):
        operation(password_engine)


def test_authorized_operation_scope_is_session_local_and_cleared_after_request(password_engine, tmp_path):
    from database.database import save_settings, set_app_setting
    from database.page_access import page_operation_access, save_page_password, unlock_pages
    from ingestion.file_manager import rebuild_database

    session = {}
    repo.save_pm_status_week(password_engine, 2026, 40, [{"area": "Test", "pm_open": 5, "pm_closed": 1}])
    save_page_password(password_engine, session, "secret", "secret", is_admin=True)
    with pytest.raises(PermissionError):
        save_settings({}, tmp_path / "settings.json", engine=password_engine)
    with pytest.raises(PermissionError):
        set_app_setting(password_engine, "auto_refresh_minutes", 10)
    with pytest.raises(ValueError, match="password settings form"):
        set_app_setting(password_engine, "admin_page_password", None)
    with pytest.raises(PermissionError):
        rebuild_database(password_engine, {})
    unlock_pages(password_engine, session, "secret", True)
    with page_operation_access(password_engine, session, True):
        repo.save_pm_status_week(password_engine, 2026, 40, [{"area": "Test", "pm_open": 5, "pm_closed": 2}])
        set_app_setting(password_engine, "auto_refresh_minutes", 10, bump=False)
        save_settings({}, tmp_path / "settings.json", engine=password_engine)
        with pytest.raises(PermissionError):
            with page_operation_access(password_engine, {}, True):
                pytest.fail("A different session must remain locked.")
    with pytest.raises(PermissionError):
        repo.save_pm_status_week(password_engine, 2026, 40, [])
    assert repo.load_pm_status(password_engine)["pm_closed"].tolist() == [2]


def test_password_read_errors_and_corrupt_settings_fail_closed(password_engine, monkeypatch):
    from sqlalchemy import update
    from sqlalchemy.exc import OperationalError

    from database.models import AppSetting
    from database.page_access import (
        GRANT_KEY, PasswordSettingsError, pages_unlocked, save_page_password, unlock_pages,
    )

    session = {}
    save_page_password(password_engine, session, "secret", "secret", is_admin=True)
    unlock_pages(password_engine, session, "secret", True)
    with monkeypatch.context() as patch:
        patch.setattr(password_engine, "connect", lambda: (_ for _ in ()).throw(
            OperationalError("injected read failure", None, None)))
        with pytest.raises(PasswordSettingsError, match="Cannot read"):
            pages_unlocked(password_engine, session, True)
        assert GRANT_KEY not in session
        with pytest.raises(PasswordSettingsError):
            repo.save_shifts(password_engine, [])
    for value in ("not json", "null", "{}", '{"salt": "invalid", "hash": "invalid"}'):
        with password_engine.begin() as conn:
            conn.execute(update(AppSetting).where(AppSetting.key == "admin_page_password").values(value=value))
        with pytest.raises(PasswordSettingsError, match="invalid"):
            pages_unlocked(password_engine, session, True)
        with pytest.raises(PasswordSettingsError):
            save_page_password(password_engine, session, "new", "new", is_admin=True)


def test_password_persists_but_unlock_does_not_survive_server_restart(password_engine):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from database.page_access import save_page_password, unlock_pages

    session = {}
    save_page_password(password_engine, session, "secret", "secret", is_admin=True)
    unlock_pages(password_engine, session, "secret", True)
    result = subprocess.run(
        [sys.executable, "-c",
         "import json, sys\n"
         "from database.database import create_db_engine, init_db\n"
         "from database.page_access import load_page_password, pages_unlocked\n"
         "engine = create_db_engine(sys.argv[1])\n"
         "init_db(engine)\n"
         "assert load_page_password(engine) is not None\n"
         "assert not pages_unlocked(engine, {}, True)\n"
         "assert not pages_unlocked(engine, json.load(sys.stdin), True)\n",
         str(password_engine.url.database)],
        input=json.dumps(session), text=True, capture_output=True,
        cwd=Path(__file__).resolve().parents[1], timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def password_app(password_engine, tmp_path):
    from streamlit.testing.v1 import AppTest

    from database.database import DEFAULT_SETTINGS
    from kpi.dataset import FilterState, apply_filters
    from ui.context import DashboardContext, kpi_targets_dict, load_base

    base = load_base(password_engine, str(password_engine.url), 0)
    filters = FilterState(label="No data")
    settings = dict(DEFAULT_SETTINGS, database_path=str(password_engine.url.database))
    for source in ("fiix", "ignition", "dayforce"):
        settings[f"{source}_source_dir"] = str(tmp_path / source)
    ctx = DashboardContext(password_engine, settings, base, filters,
                           apply_filters(base, filters), kpi_targets_dict(password_engine), 0, is_admin=True)
    (tmp_path / "main.py").write_text(
        "import streamlit as st\n"
        "ctx = st.session_state['ctx']\n"
        "home = st.Page('home.py', title='Executive Dashboard', default=True)\n"
        "ctx.home_page = home\n"
        "st.navigation([home, st.Page('admin.py', title='Administration'), "
        "st.Page('data-entry.py', title='Data Entry')]).run()\n", encoding="utf-8")
    (tmp_path / "home.py").write_text("import streamlit as st\nst.title('Safe screen')\n", encoding="utf-8")
    for filename, module in (("admin.py", "administration"), ("data-entry.py", "data_entry")):
        (tmp_path / filename).write_text(
            f"import streamlit as st\nfrom pages.{module} import render\nrender(st.session_state['ctx'])\n",
            encoding="utf-8")
    app = AppTest.from_file(str(tmp_path / "main.py"), default_timeout=15)
    app.session_state["ctx"] = ctx
    app.run()
    return app


def _input(app, label, value):
    next(field for field in app.text_input if field.label == label).input(value)


def _click(app, label):
    next(button for button in app.button if button.label == label).click().run()
    assert not app.exception


def test_menu_setup_unlock_cancel_replacement_and_new_session(password_app, password_engine, tmp_path):
    from database.page_access import load_page_password, pages_unlocked

    app = password_app
    app.switch_page("data-entry.py").run()
    assert app.title[0].value == "Data Entry"
    assert any(button.key == "pm_save" for button in app.button)
    app.switch_page("admin.py").run()
    assert app.title[0].value == "Administration"
    assert any(button.key == "save_techs" for button in app.button)
    for label in ("New Password", "Confirm Password"):
        field = next(field for field in app.text_input if field.label == label)
        assert field.proto.type == field.proto.PASSWORD
    _input(app, "New Password", "secret")
    _input(app, "Confirm Password", "mismatch")
    _click(app, "Save Password")
    assert any("must match" in error.value for error in app.error)
    assert load_page_password(password_engine) is None
    _input(app, "New Password", "secret")
    _input(app, "Confirm Password", "secret")
    _click(app, "Save Password")
    assert app.title[0].value == "Safe screen"
    assert not pages_unlocked(password_engine, app.session_state, True)
    for page in ("admin.py", "data-entry.py"):
        app.switch_page(page).run()
        assert app.title[0].value == "Administration and Data Entry are locked"
        assert not app.get("data_editor")
        _input(app, "Password", "wrong")
        _click(app, "Unlock")
        assert any("Incorrect password" in error.value for error in app.error)
        _click(app, "Cancel")
        assert app.title[0].value == "Safe screen"
        assert not pages_unlocked(password_engine, app.session_state, True)
    app.switch_page("data-entry.py").run()
    _input(app, "Password", "secret")
    _click(app, "Unlock")
    assert app.title[0].value == "Data Entry"
    app.switch_page("admin.py").run()
    assert app.title[0].value == "Administration"
    _input(app, "New Password", "replacement")
    _input(app, "Confirm Password", "replacement")
    _input(app, "Current Password", "wrong")
    _click(app, "Save Password")
    assert any("current password" in error.value for error in app.error)
    _input(app, "New Password", "replacement")
    _input(app, "Confirm Password", "replacement")
    _input(app, "Current Password", "secret")
    _click(app, "Save Password")
    assert app.title[0].value == "Safe screen"
    app.switch_page("admin.py").run()
    _input(app, "Password", "replacement")
    _click(app, "Unlock")
    assert app.title[0].value == "Administration"
    from streamlit.testing.v1 import AppTest
    fresh = AppTest.from_file(str(tmp_path / "main.py"), default_timeout=15)
    fresh.session_state["ctx"] = app.session_state["ctx"]
    fresh.run().switch_page("admin.py").run()
    assert fresh.title[0].value == "Administration and Data Entry are locked"


def test_failed_password_save_shows_error_without_locking_menus(password_app, password_engine, monkeypatch):
    import ui.page_password as password_ui
    from database.page_access import PasswordSettingsError, load_page_password

    def fail_save(*args, **kwargs):
        raise PasswordSettingsError("Password could not be saved. No password change was made.")

    monkeypatch.setattr(password_ui, "save_page_password", fail_save)
    app = password_app
    app.switch_page("admin.py").run()
    _input(app, "New Password", "secret")
    _input(app, "Confirm Password", "secret")
    _click(app, "Save Password")
    assert any("could not be saved" in error.value for error in app.error)
    assert load_page_password(password_engine) is None
    app.switch_page("data-entry.py").run()
    assert app.title[0].value == "Data Entry"


@pytest.mark.parametrize("module, handler", [
    ("administration", "_sources"), ("administration", "_backup_rebuild"),
    ("data_entry", "_pm_entry"), ("data_entry", "_dayforce_csv"),
])
def test_direct_screen_handlers_are_guarded(password_app, password_engine, module, handler):
    from streamlit.testing.v1 import AppTest
    from database.page_access import save_page_password

    save_page_password(password_engine, {}, "secret", "secret", is_admin=True)
    app = AppTest.from_string(
        f"import streamlit as st\nfrom pages.{module} import {handler}\n{handler}(st.session_state['ctx'])")
    app.session_state["ctx"] = password_app.session_state["ctx"]
    app.run()
    assert not app.exception
    assert app.title[0].value == "Administration and Data Entry are locked"
    assert not app.get("data_editor")