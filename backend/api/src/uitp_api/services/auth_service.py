"""
uitp_api.services.auth_service
================================
Password hashing (Argon2id) and session management.

SECURITY RULES enforced here:
  - Argon2id is the only accepted hash algorithm (ADR-009).
  - Timing-safe comparison via argon2-cffi's verify().
  - Never log, return, or store plaintext passwords.
  - Never log the session ID or its contents.
"""
from __future__ import annotations

import logging

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

logger = logging.getLogger(__name__)

# Argon2id with OWASP-recommended parameters (2024):
#   memory_cost=65536 KiB (64 MiB), time_cost=3, parallelism=4
_HASHER = PasswordHasher(
    memory_cost=65536,
    time_cost=3,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(plain: str) -> str:
    """Return an Argon2id hash of the given plaintext password.

    The returned string is safe to store in the database.
    Never pass this value to logs or error messages.
    """
    if not plain:
        raise ValueError("Password must not be empty.")
    return _HASHER.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if `plain` matches `hashed`; False otherwise.

    Uses timing-safe comparison internally.
    Logs at DEBUG on failure — never logs the password itself.
    """
    try:
        return _HASHER.verify(hashed, plain)
    except VerifyMismatchError:
        logger.debug("Password verification failed (mismatch).")
        return False
    except (VerificationError, InvalidHashError) as exc:
        logger.warning("Password verification error: %s", type(exc).__name__)
        return False


def needs_rehash(hashed: str) -> bool:
    """Return True if the stored hash should be upgraded to current params."""
    return _HASHER.check_needs_rehash(hashed)
