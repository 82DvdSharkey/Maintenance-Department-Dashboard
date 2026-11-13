"""Access policy for administrative and read-only dashboard users."""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping

READ_ONLY_PAGES = frozenset({"executive", "shifts", "ignition", "fiix"})
PASSWORD_PROTECTED_PAGES = frozenset({"admin", "data-entry"})
PAGE_PASSWORD_ITERATIONS = 600_000
AUTH_REQUIRED_SETTINGS = frozenset({
    "redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url",
})


def normalize_email(email: str | None) -> str:
    return (email or "").strip().casefold()


def identity_email(user) -> str:
    for claim in ("preferred_username", "email", "upn"):
        value = user.get(claim) if hasattr(user, "get") else getattr(user, claim, None)
        if normalized := normalize_email(value):
            return normalized
    return ""


def auth_is_configured(secrets: Mapping) -> bool:
    auth = secrets.get("auth", {})
    return isinstance(auth, Mapping) and all(str(auth.get(key, "")).strip() for key in AUTH_REQUIRED_SETTINGS)


def is_admin_email(email: str | None, admin_emails: set[str] | list[str] | tuple[str, ...]) -> bool:
    normalized = normalize_email(email)
    return bool(normalized) and normalized in {normalize_email(item) for item in admin_emails}


def allowed_pages(is_admin: bool) -> frozenset[str] | None:
    return None if is_admin else READ_ONLY_PAGES


def page_is_visible(page_key: str, is_admin: bool, password_unlocked: bool) -> bool:
    if not is_admin:
        return page_key in READ_ONLY_PAGES
    return password_unlocked or page_key not in PASSWORD_PROTECTED_PAGES


def verify_page_password(password: str, salt_hex: str, expected_hash_hex: str) -> bool:
    if not password:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(expected_hash_hex)
    except ValueError:
        return False
    if len(salt) < 16 or len(expected_hash) != hashlib.sha256().digest_size:
        return False
    actual_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PAGE_PASSWORD_ITERATIONS
    )
    return hmac.compare_digest(actual_hash, expected_hash)