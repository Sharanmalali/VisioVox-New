"""Password hashing for share links.

Identity is Firebase's (ADR-0016), so nothing here mints or verifies a session
token any more; the only password this service still stores is the optional one
on a share link, which belongs to a link rather than to a person.

Argon2id: memory-hard, so a leaked hash cannot be attacked with the GPU sitting
in this very machine. bcrypt would be acceptable; SHA-family hashing would not
be, at any iteration count.
"""

from __future__ import annotations

import contextlib
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-ish time verify that does not leak whether the secret exists.

    A link with no password set still performs a dummy verification, so the
    response time does not distinguish "wrong password" from "this link is not
    protected" — which would otherwise let a stranger map which links are open.
    """
    if password_hash is None:
        # Still do the work, so response time does not distinguish this case.
        with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
            _hasher.verify(_DUMMY_HASH, password)
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


_DUMMY_HASH: Final = _hasher.hash("dummy-password-for-constant-time-comparison")
