"""Dubbing through the API (ADR-0024)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pipeline.s12_dub import VOICES
from visiovox_api import deps, jobqueue
from visiovox_api.config import get_settings
from visiovox_api.db import dispose_engine
from visiovox_api.firebase_auth import FirebaseIdentity
from visiovox_api.main import app
from visiovox_api.models import Dub, Project
from visiovox_api.translation_languages import BY_CODE, DUB_LANGUAGES


def test_the_api_offers_exactly_the_languages_that_have_voices() -> None:
    assert set(DUB_LANGUAGES) == set(VOICES)
    # Every one is also a translation target: dubbing translates first.
    assert all(code in BY_CODE for code in DUB_LANGUAGES)


DB_URL = get_settings().database_url
SignIn = Callable[[], dict[str, str]]


async def _reachable() -> bool:
    try:
        engine = create_async_engine(DB_URL)
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        await engine.dispose()
    except Exception:
        return False
    return True


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(jobqueue, "enqueue_dub", sent.append)
    return sent


@pytest.fixture
def as_user(monkeypatch: pytest.MonkeyPatch) -> SignIn:
    def sign_in() -> dict[str, str]:
        uid = f"uid-{uuid.uuid4().hex[:12]}"
        identity = FirebaseIdentity(
            uid=uid,
            email=f"{uid}@example.com",
            email_verified=True,
            display_name="T",
            provider="password",
        )
        monkeypatch.setattr(deps, "verify", lambda _t, _s: identity)
        return {"Authorization": "Bearer fake"}

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


async def _project(client: AsyncClient, headers: dict[str, str], ready: bool = True) -> str:
    created = await client.post(
        "/v1/projects", json={"title": "Panel", "rights_attested": True}, headers=headers
    )
    project_id: str = created.json()["id"]
    if ready:
        engine = create_async_engine(DB_URL)
        async with async_sessionmaker(engine)() as session:
            project = await session.get(Project, project_id)
            assert project is not None
            project.manifest = {"speakers": [{"id": "face_1", "ordinal": 1}]}
            project.status = "ready"
            await session.commit()
        await engine.dispose()
    return project_id


@pytest.mark.integration
async def test_a_dub_is_queued_once_and_listed(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    url = f"/v1/projects/{project_id}/dubs"
    first = await client.post(url, json={"language": "kn"}, headers=headers)
    assert first.status_code == 202
    again = await client.post(url, json={"language": "kn"}, headers=headers)
    assert again.status_code == 200
    assert len(queued) == 1
    body = (await client.get(url, headers=headers)).json()
    assert [i["language"] for i in body["items"]] == ["kn"]
    assert {"code": "hi", "name": "Hindi", "native": "हिन्दी", "group": "india"} in body["languages"]


@pytest.mark.integration
async def test_a_failed_dub_is_retried_in_place(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    url = f"/v1/projects/{project_id}/dubs"
    await client.post(url, json={"language": "hi"}, headers=headers)
    engine = create_async_engine(DB_URL)
    async with async_sessionmaker(engine)() as session:
        await session.execute(
            update(Dub).where(Dub.project_id == project_id).values(status="failed")
        )
        await session.commit()
    await engine.dispose()
    retried = await client.post(url, json={"language": "hi"}, headers=headers)
    assert retried.status_code == 202
    assert len(queued) == 2


@pytest.mark.integration
@pytest.mark.parametrize("language", ["ur", "sat", "xx"])
async def test_languages_without_voices_are_refused(
    client: AsyncClient, as_user: SignIn, queued: list[str], language: str
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    response = await client.post(
        f"/v1/projects/{project_id}/dubs", json={"language": language}, headers=headers
    )
    assert response.status_code in (400, 422)
    assert queued == []


@pytest.mark.integration
async def test_no_dub_before_processing_and_only_for_the_owner(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    owner = as_user()
    pending = await _project(client, owner, ready=False)
    early = await client.post(
        f"/v1/projects/{pending}/dubs", json={"language": "hi"}, headers=owner
    )
    assert early.status_code == 409
    ready = await _project(client, owner)
    stranger = as_user()
    response = await client.post(
        f"/v1/projects/{ready}/dubs", json={"language": "hi"}, headers=stranger
    )
    assert response.status_code == 404
    assert queued == []
