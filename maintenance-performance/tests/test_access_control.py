import pytest

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