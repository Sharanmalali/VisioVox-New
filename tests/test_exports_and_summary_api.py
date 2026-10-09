"""Export clips, burned-in captions and summaries through the API (ADR-0021/0022)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from visiovox_api import deps, jobqueue
from visiovox_api import routes_exports as exports_module
from visiovox_api.config import get_settings
from visiovox_api.db import dispose_engine
from visiovox_api.firebase_auth import FirebaseIdentity
from visiovox_api.main import app
from visiovox_api.models import Project, Translation

pytestmark = pytest.mark.integration

DB_URL = get_settings().database_url
MANIFEST = {"speakers": [{"id": "face_1", "ordinal": 1}, {"id": "face_2", "ordinal": 2}]}
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
    monkeypatch.setattr(exports_module, "_enqueue_export", sent.append)
    monkeypatch.setattr(jobqueue, "enqueue_summary", sent.append)
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
            project.manifest = MANIFEST
            project.status = "ready"
            project.height = 720
            project.width = 1280
            project.duration_ms = 60_000
            await session.commit()
        await engine.dispose()
    return project_id


def _export(**extra: object) -> dict[str, object]:
    return {"kind": "video", "speaker_ordinal": 1, "quality": "720p", **extra}


# --------------------------------------------------------------------------
# clips and captions
# --------------------------------------------------------------------------


async def test_a_clip_with_captions_is_queued_once(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    body = _export(start_ms=10_000, end_ms=25_000, captions="original")
    url = f"/v1/projects/{project_id}/exports"

    first = await client.post(url, json=body, headers=headers)
    assert first.status_code == 202, first.text
    assert (first.json()["start_ms"], first.json()["end_ms"], first.json()["captions"]) == (
        10_000,
        25_000,
        "original",
    )
    again = await client.post(url, json=body, headers=headers)
    assert again.json()["id"] == first.json()["id"]
    whole = await client.post(url, json=_export(), headers=headers)
    assert whole.json()["id"] != first.json()["id"]
    assert len(queued) == 2


@pytest.mark.parametrize(
    "body",
    [
        _export(start_ms=1000),  # one end only
        _export(start_ms=1000, end_ms=1200),  # shorter than half a second
        _export(start_ms=50_000, end_ms=90_000),  # past the end
        {"kind": "audio", "speaker_ordinal": 1, "captions": "original"},  # no picture
        _export(captions="hi"),  # not translated yet
    ],
)
async def test_impossible_exports_are_refused(
    client: AsyncClient, as_user: SignIn, queued: list[str], body: dict[str, object]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    response = await client.post(f"/v1/projects/{project_id}/exports", json=body, headers=headers)
    assert response.status_code == 400, response.text
    assert queued == []


async def test_captions_in_a_ready_translation_are_accepted(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    engine = create_async_engine(DB_URL)
    async with async_sessionmaker(engine)() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        session.add(
            Translation(
                project_id=project_id, user_id=project.user_id, language="hi", status="ready"
            )
        )
        await session.commit()
    await engine.dispose()
    response = await client.post(
        f"/v1/projects/{project_id}/exports", json=_export(captions="hi"), headers=headers
    )
    assert response.status_code == 202, response.text


async def test_a_download_link_waits_for_the_render(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    created = await client.post(
        f"/v1/projects/{project_id}/exports", json=_export(), headers=headers
    )
    link = await client.get(
        f"/v1/projects/{project_id}/exports/{created.json()['id']}/link", headers=headers
    )
    assert link.status_code == 409


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------


async def test_a_summary_is_queued_and_reported(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    url = f"/v1/projects/{project_id}/summary"

    assert (await client.get(url, headers=headers)).json()["status"] == "none"
    asked = await client.post(url, headers=headers)
    assert asked.status_code == 202
    assert asked.json()["status"] == "queued"
    # Asking again while it is being written does not queue a second one.
    await client.post(url, headers=headers)
    assert len(queued) == 1
    assert (await client.get(url, headers=headers)).json()["status"] == "queued"


async def test_no_summary_before_processing_finishes(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers, ready=False)
    response = await client.post(f"/v1/projects/{project_id}/summary", headers=headers)
    assert response.status_code == 409
    assert queued == []


async def test_only_the_owner_sees_a_summary(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    project_id = await _project(client, as_user())
    stranger = as_user()
    response = await client.get(f"/v1/projects/{project_id}/summary", headers=stranger)
    assert response.status_code == 404
