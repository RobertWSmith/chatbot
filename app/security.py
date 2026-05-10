from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError
from werkzeug.security import check_password_hash

_hasher = PasswordHasher()
_ARGON2_PREFIX = "$argon2"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
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
    if not password_hash or not password_hash.startswith(_ARGON2_PREFIX):
        return True
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
