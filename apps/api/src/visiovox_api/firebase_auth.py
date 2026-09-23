"""Firebase identity verification (ADR-0016).

Firebase owns the user directory now: passwords, sessions, refresh rotation and
the social providers all live there. This module is the boundary where a
Firebase ID token becomes a VisioVox user.

**A local `users` row still exists**, and has to. Eight tables carry a foreign
key to `users.id` - projects, uploads, exports, shares, quotas, the audit log -
so the row is the anchor every other record hangs off. It is a *mirror* of
Firebase identity keyed by `firebase_uid`, not a second source of truth: the
email, the display name and the verification state come from the token on every
request, and nothing here mints credentials.

**What verification actually checks.** `verify_id_token` validates the RS256
signature against Google's rotating public keys, the issuer, the audience
(our project and no other), and expiry. `check_revoked=True` additionally asks
Firebase whether the session was revoked since the token was issued - one extra
round trip, taken because without it a "sign out everywhere" is advisory for up
to an hour, and revocation that does not revoke is the failure mode that makes
a stolen token worth stealing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from .config import Settings

logger = logging.getLogger(__name__)

_app: Any = None


class FirebaseError(RuntimeError):
    """Firebase is not configured, or could not be reached."""


@dataclass(frozen=True)
class FirebaseIdentity:
    """The claims we act on. Everything else in the token is ignored."""

    uid: str
    email: str | None
    email_verified: bool
    display_name: str | None
    # "password", "google.com", "github.com" — recorded on the user row so an
    # operator can answer "how did this person sign in" without a support
    # ticket becoming an investigation.
    provider: str


def _initialise(settings: Settings) -> Any:
    """Start the Admin SDK once per process.

    Credentials come from a service-account JSON file whose path is configured,
    or from the ambient Google credentials when running on Google infrastructure.
    The file is a private key: it belongs outside the repository and outside the
    image, mounted at runtime.
    """
    global _app
    if _app is not None:
        return _app

    import firebase_admin
    from firebase_admin import credentials

    if firebase_admin._apps:  # the SDK exposes no public accessor for this
        _app = firebase_admin.get_app()
        return _app

    path = settings.firebase_credentials_file
    try:
        cred = credentials.Certificate(path) if path else credentials.ApplicationDefault()
        _app = firebase_admin.initialize_app(cred, {"projectId": settings.firebase_project_id})
    except Exception as exc:
        raise FirebaseError(
            "Firebase Admin could not start. Set FIREBASE_CREDENTIALS_FILE to a "
            "service-account JSON path, or FIREBASE_PROJECT_ID with ambient "
            "Google credentials."
        ) from exc

    return _app


def verify(token: str, settings: Settings) -> FirebaseIdentity:
    """Verify an ID token and return the claims we trust.

    Raises 401 for anything wrong with the token, deliberately with one message.
    Distinguishing expired from malformed from revoked tells an attacker which
    of those they have, and the client's remedy is the same in every case: sign
    in again.
    """
    from firebase_admin import auth as fb_auth

    _initialise(settings)

    try:
        claims = fb_auth.verify_id_token(
            token,
            # Costs a round trip to Firebase. Taken so that revoking a session
            # takes effect now rather than whenever the ID token happens to
            # expire.
            check_revoked=settings.firebase_check_revoked,
        )
    except Exception as exc:
        logger.debug("firebase token rejected: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session is not valid. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    uid = claims.get("uid") or claims.get("sub")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session is not valid."
        )

    firebase_block = claims.get("firebase") or {}
    provider = str(firebase_block.get("sign_in_provider") or "unknown")

    return FirebaseIdentity(
        uid=str(uid),
        email=claims.get("email"),
        email_verified=bool(claims.get("email_verified")),
        display_name=claims.get("name"),
        provider=provider,
    )


def revoke_sessions(uid: str, settings: Settings) -> None:
    """Sign a user out everywhere.

    Only has teeth when `firebase_check_revoked` is on; otherwise already-issued
    ID tokens stay valid until they expire.
    """
    from firebase_admin import auth as fb_auth

    _initialise(settings)
    fb_auth.revoke_refresh_tokens(uid)


def delete_identity(uid: str, settings: Settings) -> None:
    """Remove the Firebase side of an account.

    The other half of erasure. Deleting the local row cascades to projects and
    media, but leaves the identity able to sign back in and be re-created on
    first request, so both halves have to happen for a deletion to mean
    anything (ADR-0016 §Consequences).
    """
    from firebase_admin import auth as fb_auth

    _initialise(settings)
    try:
        fb_auth.delete_user(uid)
    except Exception:
        # Already gone is the common case and is success, not failure.
        logger.warning("could not delete firebase user %s", uid, exc_info=True)


def reset_for_tests() -> None:
    global _app
    _app = None
