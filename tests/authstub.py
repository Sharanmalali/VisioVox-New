"""Signing requests in tests, under Firebase identity (ADR-0016).

There is no register-or-login call any more, so a test that needs to be
somebody patches verification at the boundary and lets `sync_user` do the rest —
the mapping from claims to a local row is ours and is what can regress.
"""

from __future__ import annotations

import uuid

import pytest

from visiovox_api import deps
from visiovox_api.firebase_auth import FirebaseIdentity


def identity(
    uid: str | None = None,
    email: str | None = None,
    provider: str = "password",
) -> FirebaseIdentity:
    """A verified Firebase identity, unique by default."""
    resolved_uid = uid or f"uid-{uuid.uuid4().hex[:12]}"
    return FirebaseIdentity(
        uid=resolved_uid,
        email=email or f"{resolved_uid}@example.com",
        email_verified=True,
        display_name="Test User",
        provider=provider,
    )


def sign_in(monkeypatch: pytest.MonkeyPatch, who: FirebaseIdentity | None = None) -> dict[str, str]:
    """Headers that authenticate as `who`, creating them if not supplied."""
    # Resolved ONCE, before the lambda closes over it. Written as
    # `who or identity()` inside the lambda it is re-evaluated per call, so
    # every request arrives as a different person - a project created by one
    # user and read back by another, surfacing as a 404 nowhere near the cause.
    resolved = who or identity()
    monkeypatch.setattr(deps, "verify", lambda _token, _settings: resolved)
    return {"Authorization": "Bearer fake-id-token"}
