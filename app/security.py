from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from werkzeug.security import check_password_hash

_hasher = PasswordHasher()
_ARGON2_PREFIX = "$argon2"


def hash_password(password: str) -> str:
    """Hash a password with the application's Argon2 configuration.

    Args:
        password: Plain-text password.

    Returns:
        An encoded Argon2 password hash.
    """
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a password against Argon2 or a legacy Werkzeug hash.

    Args:
        password_hash: Encoded hash stored for the user.
        password: Candidate plain-text password.

    Returns:
        Whether the password matches. Malformed hashes return ``False``.
    """
    if not password_hash:
        return False
    if not password_hash.startswith(_ARGON2_PREFIX):
        try:
            return check_password_hash(password_hash, password)
        except ValueError:
            return False
    try:
        return _hasher.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError, VerificationError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """Determine whether a stored password should be upgraded.

    Args:
        password_hash: Encoded hash stored for the user.

    Returns:
        ``True`` for legacy, malformed, or outdated hashes.
    """
    if not password_hash or not password_hash.startswith(_ARGON2_PREFIX):
        return True
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
