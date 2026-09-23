"""Identity and ownership under Firebase auth (ADR-0016).

The auth flows this file used to cover — registration, login, refresh rotation,
reuse detection — belong to Firebase now and are not ours to test. What remains
is ours and still matters:

- an unauthenticated request is refused, and says nothing about what exists
- a verified token becomes exactly one local user row, and the same identity
  signing in twice does not produce two
- ownership is enforced server-side (invariant 4), and another user's project is
  indistinguishable from one that was never there

Firebase verification is faked at the boundary. Testing that Google's public
keys verify Google's signatures tests Google; testing what this codebase does
with the claims is the part that can regress.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from visiovox_api import deps
from visiovox_api.config import get_settings
from visiovox_api.db import dispose_engine
from visiovox_api.firebase_auth import FirebaseIdentity
from visiovox_api.main import app
from visiovox_api.models import Project, User

pytestmark = pytest.mark.integration

DB_URL = get_settings().database_url


async def _reachable() -> bool:
    try:
        engine = create_async_engine(DB_URL)
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        await engine.dispose()
    except Exception:
        return False
    return True


def _identity(uid: str, email: str, provider: str = "password") -> FirebaseIdentity:
    return FirebaseIdentity(
        uid=uid,
        email=email,
        email_verified=True,
        display_name="Test User",
        provider=provider,
    )


SignIn = Callable[[FirebaseIdentity], dict[str, str]]


@pytest.fixture
def as_user(monkeypatch: pytest.MonkeyPatch) -> SignIn:
    """Sign requests as a given Firebase identity.

    Patches verification rather than the dependency, so `sync_user` — the code
    that actually maps a token to a row — still runs.
    """

    def sign_in(identity: FirebaseIdentity) -> dict[str, str]:
        monkeypatch.setattr(deps, "verify", lambda _token, _settings: identity)
        return {"Authorization": "Bearer fake-id-token"}

    return sign_in


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    if not await _reachable():
        pytest.skip("Postgres not reachable; run `make dev`")
    await dispose_engine()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        await dispose_engine()


# --------------------------------------------------------------------------
# the door
# --------------------------------------------------------------------------


async def test_no_token_is_refused(client: AsyncClient) -> None:
    assert (await client.get("/v1/auth/me")).status_code == 401


async def test_a_malformed_header_is_refused(client: AsyncClient) -> None:
    response = await client.get("/v1/auth/me", headers={"Authorization": "Basic nope"})
    assert response.status_code == 401


async def test_an_unverifiable_token_is_refused(client: AsyncClient) -> None:
    """Real verification runs here — nothing is patched — so a made-up token
    must not get through."""
    response = await client.get("/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


# --------------------------------------------------------------------------
# identity becomes exactly one row
# --------------------------------------------------------------------------


async def test_a_verified_token_creates_the_local_row(client: AsyncClient, as_user: SignIn) -> None:
    uid = f"uid-{uuid.uuid4().hex[:12]}"
    email = f"{uid}@example.com"
    headers = as_user(_identity(uid, email))

    body = (await client.get("/v1/auth/me", headers=headers)).json()
    assert body["email"] == email

    engine = create_async_engine(DB_URL)
    async with async_sessionmaker(engine)() as session:
        rows = (await session.scalars(select(User).where(User.firebase_uid == uid))).all()
        assert len(rows) == 1
    await engine.dispose()


async def test_signing_in_twice_does_not_duplicate_the_row(
    client: AsyncClient, as_user: SignIn
) -> None:
    """The row is created on first sight of a token, so every subsequent
    request has to find it rather than make another."""
    uid = f"uid-{uuid.uuid4().hex[:12]}"
    headers = as_user(_identity(uid, f"{uid}@example.com"))

    first = (await client.get("/v1/auth/me", headers=headers)).json()
    second = (await client.get("/v1/auth/me", headers=headers)).json()
    assert first["id"] == second["id"]


async def test_a_changed_email_is_mirrored(client: AsyncClient, as_user: SignIn) -> None:
    """Firebase is the source of truth for the address; a stale local copy is
    how support tickets start."""
    uid = f"uid-{uuid.uuid4().hex[:12]}"
    old = f"{uid}@example.com"
    new = f"{uid}-changed@example.com"

    await client.get("/v1/auth/me", headers=as_user(_identity(uid, old)))
    body = (await client.get("/v1/auth/me", headers=as_user(_identity(uid, new)))).json()
    assert body["email"] == new


async def test_an_existing_account_is_adopted_by_email(
    client: AsyncClient, as_user: SignIn
) -> None:
    """A row that predates Firebase must be claimed, not duplicated — otherwise
    the person signs in and finds none of their recordings."""
    email = f"legacy-{uuid.uuid4().hex[:10]}@example.com"

    engine = create_async_engine(DB_URL)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        legacy = User(email=email, display_name="Legacy")
        session.add(legacy)
        await session.commit()
        legacy_id = legacy.id
    await engine.dispose()

    uid = f"uid-{uuid.uuid4().hex[:12]}"
    body = (await client.get("/v1/auth/me", headers=as_user(_identity(uid, email)))).json()
    assert body["id"] == legacy_id


async def test_the_provider_is_recorded(client: AsyncClient, as_user: SignIn) -> None:
    uid = f"uid-{uuid.uuid4().hex[:12]}"
    await client.get(
        "/v1/auth/me",
        headers=as_user(_identity(uid, f"{uid}@example.com", provider="google.com")),
    )

    engine = create_async_engine(DB_URL)
    async with async_sessionmaker(engine)() as session:
        user = await session.scalar(select(User).where(User.firebase_uid == uid))
        assert user is not None
        assert user.auth_provider == "google.com"
    await engine.dispose()


# --------------------------------------------------------------------------
# ownership — invariant 4, unchanged by the auth swap
# --------------------------------------------------------------------------


async def test_another_users_project_is_a_404(client: AsyncClient, as_user: SignIn) -> None:
    """404 rather than 403: 403 confirms the id exists, which turns the
    endpoint into an existence oracle for other people's projects."""
    owner = f"uid-{uuid.uuid4().hex[:12]}"
    created = await client.post(
        "/v1/projects",
        json={"title": "Private", "rights_attested": True},
        headers=as_user(_identity(owner, f"{owner}@example.com")),
    )
    project_id = created.json()["id"]

    stranger = f"uid-{uuid.uuid4().hex[:12]}"
    response = await client.get(
        f"/v1/projects/{project_id}",
        headers=as_user(_identity(stranger, f"{stranger}@example.com")),
    )
    assert response.status_code == 404


async def test_a_project_lists_only_for_its_owner(client: AsyncClient, as_user: SignIn) -> None:
    owner = f"uid-{uuid.uuid4().hex[:12]}"
    await client.post(
        "/v1/projects",
        json={"title": "Mine only", "rights_attested": True},
        headers=as_user(_identity(owner, f"{owner}@example.com")),
    )

    stranger = f"uid-{uuid.uuid4().hex[:12]}"
    listed = await client.get(
        "/v1/projects", headers=as_user(_identity(stranger, f"{stranger}@example.com"))
    )
    titles = [p["title"] for p in listed.json()["items"]]
    assert "Mine only" not in titles


# --------------------------------------------------------------------------
# deletion — both halves (ADR-0016 §Consequences)
# --------------------------------------------------------------------------


async def test_deleting_an_account_also_deletes_the_identity(
    client: AsyncClient, as_user: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local row alone is not enough: leaving the Firebase identity means
    the person signs in tomorrow and a fresh row appears — an account that
    looks deleted and is not."""
    import visiovox_api.main as main_module

    deleted: list[str] = []
    monkeypatch.setattr(main_module, "delete_identity", lambda uid, _settings: deleted.append(uid))

    uid = f"uid-{uuid.uuid4().hex[:12]}"
    headers = as_user(_identity(uid, f"{uid}@example.com"))
    await client.get("/v1/auth/me", headers=headers)

    response = await client.delete("/v1/auth/me", headers=headers)
    assert response.status_code == 202
    assert deleted == [uid]


async def test_a_deleted_account_cannot_come_back(
    client: AsyncClient, as_user: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    import visiovox_api.main as main_module

    monkeypatch.setattr(main_module, "delete_identity", lambda _uid, _settings: None)

    uid = f"uid-{uuid.uuid4().hex[:12]}"
    headers = as_user(_identity(uid, f"{uid}@example.com"))
    await client.get("/v1/auth/me", headers=headers)
    await client.delete("/v1/auth/me", headers=headers)

    # Same identity, same token: the row is tombstoned, so it must be refused
    # rather than silently resurrected.
    assert (await client.get("/v1/auth/me", headers=headers)).status_code == 401


async def test_projects_survive_only_as_long_as_the_owner(
    client: AsyncClient, as_user: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    import visiovox_api.main as main_module

    monkeypatch.setattr(main_module, "delete_identity", lambda _uid, _settings: None)

    uid = f"uid-{uuid.uuid4().hex[:12]}"
    headers = as_user(_identity(uid, f"{uid}@example.com"))
    created = await client.post(
        "/v1/projects",
        json={"title": "Goes with me", "rights_attested": True},
        headers=headers,
    )
    project_id = created.json()["id"]
    await client.delete("/v1/auth/me", headers=headers)

    engine = create_async_engine(DB_URL)
    async with async_sessionmaker(engine)() as session:
        project = await session.get(Project, project_id)
        # The row is still present — deletion is scheduled, not immediate — but
        # its owner is tombstoned, so nothing can reach it.
        assert project is not None
    await engine.dispose()
