"""Naming the separated speakers ("Speaker 2" -> "Priya").

Names live on the project and are applied to the manifest as it is served, so
the stored manifest — what the pipeline produced — is never rewritten.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from visiovox_api import deps
from visiovox_api.config import get_settings
from visiovox_api.db import dispose_engine
from visiovox_api.firebase_auth import FirebaseIdentity
from visiovox_api.main import app
from visiovox_api.models import Project
from visiovox_api.speakers import clean_name, speaker_ordinals, with_names

MANIFEST = {
    "project_id": "x",
    "speakers": [
        {"id": "face_1", "ordinal": 1, "label": "Speaker 1"},
        {"id": "face_2", "ordinal": 2, "label": "Speaker 2"},
    ],
}


# --------------------------------------------------------------------------
# pure
# --------------------------------------------------------------------------


def test_names_replace_labels_by_ordinal() -> None:
    named = with_names(MANIFEST, {"2": "Priya"})
    assert [s["label"] for s in named["speakers"]] == ["Speaker 1", "Priya"]


def test_the_stored_manifest_is_not_mutated() -> None:
    with_names(MANIFEST, {"1": "Arjun"})
    assert MANIFEST["speakers"][0]["label"] == "Speaker 1"  # type: ignore[index]


def test_no_names_returns_the_manifest_unchanged() -> None:
    assert with_names(MANIFEST, {}) is MANIFEST


def test_blank_names_mean_default() -> None:
    assert clean_name("   ") is None
    assert clean_name(None) is None
    assert clean_name("  Dr.   Rao ") == "Dr. Rao"


def test_ordinals_come_from_the_manifest() -> None:
    assert speaker_ordinals(MANIFEST) == {1, 2}
    assert speaker_ordinals(None) == set()


# --------------------------------------------------------------------------
# through the API
# --------------------------------------------------------------------------

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


SignIn = Callable[[str], dict[str, str]]


@pytest.fixture
def as_user(monkeypatch: pytest.MonkeyPatch) -> SignIn:
    def sign_in(uid: str) -> dict[str, str]:
        identity = FirebaseIdentity(
            uid=uid,
            email=f"{uid}@example.com",
            email_verified=True,
            display_name="Test",
            provider="password",
        )
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


async def _ready_project(client: AsyncClient, headers: dict[str, str]) -> str:
    created = await client.post(
        "/v1/projects", json={"title": "Panel", "rights_attested": True}, headers=headers
    )
    project_id: str = created.json()["id"]
    engine = create_async_engine(DB_URL)
    async with async_sessionmaker(engine)() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.manifest = MANIFEST
        project.status = "ready"
        await session.commit()
    await engine.dispose()
    return project_id


pytestmark_integration = pytest.mark.integration


@pytestmark_integration
async def test_a_name_reaches_the_project_and_the_manifest(
    client: AsyncClient, as_user: SignIn
) -> None:
    headers = as_user(f"uid-{uuid.uuid4().hex[:12]}")
    project_id = await _ready_project(client, headers)

    response = await client.put(
        f"/v1/projects/{project_id}/speakers/2/name", json={"name": " Priya "}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["speaker_names"] == {"2": "Priya"}

    manifest = (await client.get(f"/v1/projects/{project_id}/manifest", headers=headers)).json()
    assert [s["label"] for s in manifest["speakers"]] == ["Speaker 1", "Priya"]


@pytestmark_integration
async def test_clearing_a_name_restores_the_default(client: AsyncClient, as_user: SignIn) -> None:
    headers = as_user(f"uid-{uuid.uuid4().hex[:12]}")
    project_id = await _ready_project(client, headers)
    url = f"/v1/projects/{project_id}/speakers/1/name"

    await client.put(url, json={"name": "Arjun"}, headers=headers)
    cleared = await client.put(url, json={"name": None}, headers=headers)
    assert cleared.json()["speaker_names"] == {}


@pytestmark_integration
async def test_a_speaker_that_does_not_exist_is_a_404(client: AsyncClient, as_user: SignIn) -> None:
    headers = as_user(f"uid-{uuid.uuid4().hex[:12]}")
    project_id = await _ready_project(client, headers)
    response = await client.put(
        f"/v1/projects/{project_id}/speakers/5/name", json={"name": "Ghost"}, headers=headers
    )
    assert response.status_code == 404


@pytestmark_integration
async def test_only_the_owner_can_rename(client: AsyncClient, as_user: SignIn) -> None:
    project_id = await _ready_project(client, as_user(f"uid-{uuid.uuid4().hex[:12]}"))
    stranger = as_user(f"uid-{uuid.uuid4().hex[:12]}")
    response = await client.put(
        f"/v1/projects/{project_id}/speakers/1/name", json={"name": "Mine now"}, headers=stranger
    )
    assert response.status_code == 404


@pytestmark_integration
async def test_an_overlong_name_is_refused(client: AsyncClient, as_user: SignIn) -> None:
    headers = as_user(f"uid-{uuid.uuid4().hex[:12]}")
    project_id = await _ready_project(client, headers)
    response = await client.put(
        f"/v1/projects/{project_id}/speakers/1/name", json={"name": "x" * 61}, headers=headers
    )
    assert response.status_code in (400, 422)
