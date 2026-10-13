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
from visiovox_api.routes_dubs import dub_manifest
from visiovox_api.translation_languages import BY_CODE, DUB_LANGUAGES

MEDIA = "http://media.test/visiovox-media"
ORIGINAL = {
    "project_id": "p1",
    "duration_ms": 60_000,
    "has_video": True,
    "video": {"url": f"{MEDIA}/projects/p1/video.mp4", "width": 640, "height": 360},
    "speakers": [
        {"id": "face_1", "ordinal": 1, "label": "Speaker 1", "peaks_url": "x",
         "audio": {"faithful": {"url": f"{MEDIA}/projects/p1/spk_1_f.m4a", "bytes": 9},
                   "natural": {"url": f"{MEDIA}/projects/p1/spk_1_n.m4a", "bytes": 9}},
         "captions": {"vtt": f"{MEDIA}/projects/p1/spk_1.vtt",
                      "json": f"{MEDIA}/projects/p1/spk_1.json"}},
        {"id": "face_2", "ordinal": 2, "label": "Speaker 2",
         "audio": {"faithful": {"url": f"{MEDIA}/projects/p1/spk_2_f.m4a", "bytes": 9}},
         "captions": {"vtt": f"{MEDIA}/projects/p1/spk_2.vtt"}},
        {"id": "face_3", "ordinal": 3, "label": "Speaker 3",
         "audio": {"faithful": {"url": f"{MEDIA}/projects/p1/spk_3_f.m4a", "bytes": 9}},
         "captions": {"vtt": f"{MEDIA}/projects/p1/spk_3.vtt"}},
    ],
    "mixed": {"audio_url": f"{MEDIA}/projects/p1/mixed.m4a", "hls": "old"},
    "master_playlist": f"{MEDIA}/projects/p1/master.m3u8",
    "playback_hint": "hls",
    "warnings": ["something about the original"],
}  # fmt: skip


def test_a_dub_plays_like_the_original_with_each_speaker_s_dubbed_voice() -> None:
    tracks = {"speakers": [3, 1], "bytes": {"1": 100, "3": 300, "mix": 500},
              "playback_hint": "webaudio"}  # fmt: skip
    out = dub_manifest(ORIGINAL, "p1", "hi", tracks, MEDIA)
    base = f"{MEDIA}/projects/p1/dubs/hi/tracks/"
    # Speaker 2 had nothing to dub, so has no dubbed voice to offer.
    assert [s["ordinal"] for s in out["speakers"]] == [1, 3]
    first = out["speakers"][0]
    assert first["audio"] == {"faithful": {"url": f"{base}spk_1.m4a", "bytes": 100}}
    assert "peaks_url" not in first, "the original's waveform is not the dub's"
    # Faces, photos and captions are the original's: the same people.
    assert first["captions"] == ORIGINAL["speakers"][0]["captions"]  # type: ignore[index]
    assert out["video"] == ORIGINAL["video"]
    assert out["mixed"] == {"audio_url": f"{base}mix.m4a"}
    assert out["playback_hint"] == "webaudio" and "master_playlist" not in out
    assert out["warnings"] == [] and out["dub_language"] == "hi"
    assert ORIGINAL["speakers"][0]["audio"]["faithful"]["bytes"] == 9  # type: ignore[index]


def test_a_long_dub_streams_with_renditions_in_the_worker_s_order() -> None:
    tracks = {"speakers": [1, 3], "bytes": {}, "playback_hint": "hls"}
    out = dub_manifest(ORIGINAL, "p1", "kn", tracks, MEDIA)
    base = f"{MEDIA}/projects/p1/dubs/kn/tracks/"
    assert [s["audio"]["hls"] for s in out["speakers"]] == [
        f"{base}audio/a0.m3u8",
        f"{base}audio/a1.m3u8",
    ]
    assert out["mixed"]["hls"] == f"{base}audio/a2.m3u8"
    assert out["master_playlist"] == f"{base}master.m3u8"


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


async def _mark_ready(project_id: str, stats: dict[str, object]) -> None:
    engine = create_async_engine(DB_URL)
    async with async_sessionmaker(engine)() as session:
        await session.execute(
            update(Dub).where(Dub.project_id == project_id).values(status="ready", stats=stats)
        )
        await session.commit()
    await engine.dispose()


@pytest.mark.integration
async def test_a_dub_is_served_speaker_by_speaker(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    url = f"/v1/projects/{project_id}/dubs"
    missing = await client.get(f"{url}/hi/manifest", headers=headers)
    assert missing.status_code == 404
    await client.post(url, json={"language": "hi"}, headers=headers)
    early = await client.get(f"{url}/hi/manifest", headers=headers)
    assert early.status_code == 409

    tracks = {"speakers": [1], "bytes": {"1": 10, "mix": 20}, "playback_hint": "webaudio"}
    await _mark_ready(project_id, {"lines": 3, "tracks": tracks})
    body = (await client.get(f"{url}/hi/manifest", headers=headers)).json()
    assert [s["id"] for s in body["speakers"]] == ["face_1"]
    assert (
        f"projects/{project_id}/dubs/hi/tracks/spk_1.m4a"
        in body["speakers"][0]["audio"]["faithful"]["url"]
    )
    assert f"projects/{project_id}/dubs/hi/tracks/mix.m4a" in body["mixed"]["audio_url"]
    assert "signed_until" in body

    # Asking again for a dub that has its speakers kept apart changes nothing.
    again = await client.post(url, json={"language": "hi"}, headers=headers)
    assert again.status_code == 200 and len(queued) == 1
    stranger = as_user()  # every request from here on is someone else's
    assert (await client.get(f"{url}/hi/manifest", headers=stranger)).status_code == 404


@pytest.mark.integration
async def test_a_dub_made_before_speakers_were_kept_apart_is_made_again(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    url = f"/v1/projects/{project_id}/dubs"
    await client.post(url, json={"language": "kn"}, headers=headers)
    await _mark_ready(project_id, {"lines": 3})
    old = await client.get(f"{url}/kn/manifest", headers=headers)
    assert old.status_code == 409 and "again" in old.json()["detail"]
    redo = await client.post(url, json={"language": "kn"}, headers=headers)
    assert redo.status_code == 202 and redo.json()["status"] == "queued"
    assert len(queued) == 2
