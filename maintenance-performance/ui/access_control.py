"""Access policy for administrative and read-only dashboard users."""
from __future__ import annotations

from collections.abc import Mapping

READ_ONLY_PAGES = frozenset({"executive", "shifts", "ignition", "fiix"})
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