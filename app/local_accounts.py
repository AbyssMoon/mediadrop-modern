from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.orm import Session

from app.models import User
from app.modern_database import get_modern_setting, set_modern_setting
from app.security import verify_legacy_password

_PASSWORD_HASHER = PasswordHasher()
_PASSWORD_KEY_PREFIX = "local_user.password.argon2."
_ENABLED_KEY_PREFIX = "local_user.enabled."
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def _password_key(user_id: int) -> str:
    return f"{_PASSWORD_KEY_PREFIX}{user_id}"


def _enabled_key(user_id: int) -> str:
    return f"{_ENABLED_KEY_PREFIX}{user_id}"


def hash_modern_password(password: str) -> str:
    """Hash a local-account password with Argon2id."""
    return _PASSWORD_HASHER.hash(password)


def set_modern_password(db: Session, user_id: int, password: str) -> None:
    set_modern_setting(db, _password_key(user_id), hash_modern_password(password))


def has_modern_password(db: Session, user_id: int) -> bool:
    return bool(get_modern_setting(db, _password_key(user_id), ""))


def verify_local_password(db: Session, user: User, password: str) -> bool:
    """Verify a local account, lazily migrating legacy SHA-1 logins to Argon2id.

    Once an Argon2 hash exists in the modern sidecar database it is authoritative.
    The legacy hash is retained only so the old MediaDrop installation can still be
    used for rollback during the migration window.
    """
    stored = get_modern_setting(db, _password_key(user.id), "")
    if stored:
        try:
            valid = _PASSWORD_HASHER.verify(stored, password)
        except (VerificationError, InvalidHashError):
            return False
        if valid and _PASSWORD_HASHER.check_needs_rehash(stored):
            set_modern_password(db, user.id, password)
        return bool(valid)

    if not verify_legacy_password(password, user.password):
        return False

    set_modern_password(db, user.id, password)
    return True


def is_local_user_enabled(db: Session, user_id: int) -> bool:
    value = get_modern_setting(db, _enabled_key(user_id), "true")
    return value.strip().lower() not in _FALSE_VALUES


def set_local_user_enabled(db: Session, user_id: int, enabled: bool) -> None:
    set_modern_setting(db, _enabled_key(user_id), enabled)
