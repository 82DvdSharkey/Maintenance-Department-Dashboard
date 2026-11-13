"""Server-side password policy for Administration and Data Entry."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Callable, Concatenate, ParamSpec, TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from database.models import AppSetting

PASSWORD_KEY = "admin_page_password"
GRANT_KEY = "_protected_pages_grant"
ITERATIONS = 600_000
_PROCESS_SECRET = secrets.token_bytes(32)
_operation_access: ContextVar[tuple[Engine, MutableMapping, bool] | None] = ContextVar(
    "protected_operation_access", default=None
)
P = ParamSpec("P")
T = TypeVar("T")


class PasswordSettingsError(RuntimeError):
    pass


def _read_password(conn: Connection) -> dict | None:
    row = conn.execute(select(AppSetting.value).where(AppSetting.key == PASSWORD_KEY)).first()
    if row is None:
        return None
    try:
        config = json.loads(row[0])
        if not isinstance(config, dict):
            raise ValueError
        salt = bytes.fromhex(config["salt"])
        password_hash = bytes.fromhex(config["hash"])
        if (len(salt) < 16 or len(password_hash) != 32
                or config.get("algorithm", "pbkdf2_sha256") != "pbkdf2_sha256"
                or config.get("iterations", ITERATIONS) != ITERATIONS):
            raise ValueError
    except (ValueError, TypeError, KeyError) as exc:
        raise PasswordSettingsError("Password settings are invalid. Protected access remains locked.") from exc
    return config


def load_page_password(engine: Engine) -> dict | None:
    try:
        with engine.connect() as conn:
            return _read_password(conn)
    except SQLAlchemyError as exc:
        raise PasswordSettingsError("Cannot read password settings. Protected access remains locked.") from exc


def _matches(password: str, config: dict) -> bool:
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(config["salt"]), ITERATIONS
    )
    return bool(password) and hmac.compare_digest(actual.hex(), config["hash"])


def _grant(engine: Engine, config: dict) -> str:
    payload = f"{engine.url}:{json.dumps(config, sort_keys=True)}".encode("utf-8")
    return hmac.new(_PROCESS_SECRET, payload, hashlib.sha256).hexdigest()


def lock_pages(session: MutableMapping) -> None:
    session.pop(GRANT_KEY, None)


def pages_unlocked(engine: Engine, session: MutableMapping, is_admin: bool) -> bool:
    try:
        config = load_page_password(engine)
    except PasswordSettingsError:
        lock_pages(session)
        raise
    if not is_admin:
        lock_pages(session)
        return False
    if config is None:
        return True
    return hmac.compare_digest(str(session.get(GRANT_KEY, "")), _grant(engine, config))


def unlock_pages(engine: Engine, session: MutableMapping, password: str, is_admin: bool) -> None:
    lock_pages(session)
    config = load_page_password(engine)
    if not is_admin:
        raise PermissionError("Administrator access is required.")
    if config is not None:
        if not _matches(password, config):
            raise PermissionError("Incorrect password.")
        session[GRANT_KEY] = _grant(engine, config)


def save_page_password(engine: Engine, session: MutableMapping, new_password: str,
                       confirm_password: str, current_password: str = "", *, is_admin: bool) -> None:
    if not is_admin:
        raise PermissionError("Administrator access is required.")
    if not new_password or not confirm_password:
        raise ValueError("New Password and Confirm Password are both required.")
    if new_password != confirm_password:
        raise ValueError("New Password and Confirm Password must match.")
    try:
        with engine.begin() as conn:
            # Serialize setup/replacement so concurrent sessions cannot overwrite a newly saved password.
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            existing = _read_password(conn)
            if existing is not None and not _matches(current_password, existing):
                raise PermissionError("The current password is required and must be correct.")
            salt = secrets.token_bytes(32)
            config = {
                "algorithm": "pbkdf2_sha256",
                "iterations": ITERATIONS,
                "salt": salt.hex(),
                "hash": hashlib.pbkdf2_hmac(
                    "sha256", new_password.encode("utf-8"), salt, ITERATIONS
                ).hex(),
            }
            conn.execute(sqlite_insert(AppSetting).values(key=PASSWORD_KEY, value=json.dumps(config))
                         .on_conflict_do_update(index_elements=["key"], set_={"value": json.dumps(config)}))
    except SQLAlchemyError as exc:
        raise PasswordSettingsError("Password could not be saved. No password change was made.") from exc
    lock_pages(session)


@contextmanager
def page_operation_access(engine: Engine, session: MutableMapping, is_admin: bool):
    if not pages_unlocked(engine, session, is_admin):
        raise PermissionError("Administration and Data Entry are locked.")
    token = _operation_access.set((engine, session, is_admin))
    try:
        yield
    finally:
        _operation_access.reset(token)


def require_operation_access(engine: Engine) -> None:
    access = _operation_access.get()
    if access is not None and access[0] is engine:
        if pages_unlocked(engine, access[1], access[2]):
            return
    elif load_page_password(engine) is None:
        return
    raise PermissionError("Administration and Data Entry are locked.")


def protected_write(function: Callable[Concatenate[Engine, P], T]) -> Callable[Concatenate[Engine, P], T]:
    @wraps(function)
    def guarded(engine: Engine, *args: P.args, **kwargs: P.kwargs) -> T:
        require_operation_access(engine)
        return function(engine, *args, **kwargs)
    return guarded
