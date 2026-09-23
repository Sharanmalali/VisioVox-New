# ADR-0016 — Firebase Authentication replaces self-hosted auth

- **Status:** Accepted
- **Date:** 2026-09-23
- **Supersedes:** [ADR-0014](./0014-authentication.md)
- **Related:** [`15-security.md`](../15-security.md) §2, [`16-privacy-compliance.md`](../16-privacy-compliance.md), [`11-api-spec.md`](../11-api-spec.md) §2, FR-ACC-01…09, NFR-PRIV-04

## Context

ADR-0014 chose self-hosted authentication and it was built: Argon2id hashing,
rotating refresh tokens with family reuse detection, a sessions table, and an
httpOnly refresh cookie. It worked and was verified in a browser.

Two things were still missing, and one of them mattered:

- **OIDC was never implemented.** ADR-0014 listed "email/password and OIDC
  login" as a requirement. `GOOGLE_CLIENT_ID` and `GITHUB_CLIENT_ID` sat in
  `.env.example` as placeholders for a month. Social sign-in is what most people
  expect to see, and it did not exist.
- **MFA was never implemented** either, and building it well is not a weekend.

The project owner decided that owning authentication was not where the
remaining time should go, and that a provider handling sign-in, social
providers, password reset and MFA was worth the trade this ADR records.

## Decision

**Firebase Authentication owns identity.** Passwords, sessions, token issuance,
refresh rotation, password reset and the social providers all move to Firebase.

The API verifies Firebase ID tokens and does nothing else about authentication:

| Concern | Before | Now |
|---|---|---|
| Passwords | Argon2id in `users.password_hash` | Firebase |
| Sessions | `sessions` table | Firebase |
| Refresh + rotation | `refresh_tokens`, family reuse detection | Firebase |
| Token lifetime | 10 min access, 30 d refresh | 1 h ID token, renewed by the SDK |
| Social sign-in | none | Google, GitHub |
| Password reset | none | Firebase |
| User directory | `users` | Firebase, mirrored into `users` |

**A local `users` row still exists and is not optional.** Eight tables carry a
foreign key to `users.id` — projects, upload sessions, exports, share links,
usage counters and the audit log. The row is created on the first authenticated
request, keyed by `firebase_uid`, and its email and display name are refreshed
from the token's claims on every request. It is a mirror, not a second source of
truth.

Removed: `auth_service.py`, `authguard.py`, the `sessions` and `refresh_tokens`
tables, `users.password_hash`, and the `/v1/auth/register|login|refresh|logout`
routes. `GET /v1/auth/me` survives, because the client still needs to know who
it is without calling Firebase.

Kept: `security.py`'s password hashing, which share links use for their own
optional passwords and which has nothing to do with user identity.

## Rationale

The honest version: this buys social sign-in, password reset and MFA that
nobody has to build or maintain, at the cost of the two properties ADR-0014 was
written to protect. That ADR's reasoning was not wrong — the circumstances
changed. It is a student project with a deadline, and auth is a domain where the
work never looks finished.

Firebase's free tier covers this: unlimited email/password and social sign-in,
no card. Cost was never the reason ADR-0014 rejected managed auth.

## Consequences

### What is worse now, stated plainly

**Account deletion is a two-system operation.** This is the property ADR-0014
was most concerned with, and the concern was correct. NFR-PRIV-04 requires
erasure to cascade to biometric derivatives with a verifiable receipt. Deleting
the local row cascades to projects and media as before, but leaves the Firebase
identity able to sign in and be re-created on the next request — so both halves
must happen, and a failure between them leaves an account that looks deleted and
is not. `firebase_auth.delete_identity` is the second half; anything that
deletes a user must call it, and the deletion receipt is only true once it has.

**A third party holds the user directory.** Google can see who uses this and
when they sign in. For a tool whose privacy claim is about voice and face data,
that is a smaller exposure than it first sounds — no media ever reaches Firebase
— but it is not nothing, and the privacy documentation says so rather than
omitting it.

**Reuse detection is gone.** Firebase rotates refresh tokens but does not
publish a stolen-token signal the way the old implementation did. What replaces
it is `check_revoked=True` on every verification: one extra round trip per
request, in exchange for revocation taking effect immediately rather than
whenever the ID token happens to expire. Turning that off makes "sign out
everywhere" advisory for up to an hour.

**Sign-in depends on a service being up.** Firebase down means nobody signs in.
Already-signed-in sessions keep working until their ID token expires.

### What is better

Social sign-in exists. Password reset exists. MFA is available without new code.
Token expiry, renewal and rotation are no longer this project's problem, and
roughly 400 lines of security-critical code and two tables are gone.

### What did not change

The API still authorises every request itself. Invariant 4 — every artifact
access ownership-checked server-side — is untouched: Firebase says *who* you
are, and this codebase decides what that entitles you to. Share links keep their
own tokens and their own revocation, which never depended on user auth.

**No credential is persisted in the browser.** The ID token lives in memory for
its hour, and Firebase persistence is set to `browserSessionPersistence` rather
than the default IndexedDB — the same reasoning that kept tokens out of
localStorage applies to any store a script on the origin can read, and this app
renders text derived from arbitrary user media.
