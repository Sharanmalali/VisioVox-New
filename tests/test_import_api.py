"""Importing from a link, and processing part of it (routes_import.py)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from visiovox_api import deps, jobqueue
from visiovox_api.config import get_settings
from visiovox_api.db import dispose_engine
from visiovox_api.firebase_auth import FirebaseIdentity
from visiovox_api.main import app
from visiovox_api.models import Job, MediaImport, Project
from visiovox_api.routes_import import check_url

DB_URL = get_settings().database_url
SignIn = Callable[[], dict[str, str]]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "https://youtu.be/jNQXAC9IVRw",
        "https://m.youtube.com/shorts/abc",
        "https://vimeo.com/76979871",
    ],
)
def test_youtube_and_vimeo_links_are_accepted(url: str) -> None:
    assert check_url(url).startswith("https://")


@pytest.mark.parametrize(
    "url",
    [
        "http://minio:9000/visiovox-media/secret",  # the server's own network
        "http://169.254.169.254/latest/meta-data/",
        "https://example.com/video.mp4",
        "https://www.youtube.com.evil.example/watch?v=x",
        "https://user:pw@www.youtube.com/watch?v=x",
        "file:///etc/passwd",
        "ftp://youtube.com/x",
    ],
)
def test_anything_else_is_refused(url: str) -> None:
    with pytest.raises(HTTPException) as caught:
        check_url(url)
    assert caught.value.status_code == 400


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
def as_user(monkeypatch: pytest.MonkeyPatch) -> SignIn:
    def sign_in() -> dict[str, str]:
        uid = f"uid-{uuid.uuid4().hex[:12]}"
        identity = FirebaseIdentity(
            uid=uid, email=f"{uid}@example.com", email_verified=True, display_name="T",
            provider="password",
        )  # fmt: skip
        monkeypatch.setattr(deps, "verify", lambda _t, _s: identity)
        return {"Authorization": "Bearer fake"}

    return sign_in


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    if not await _reachable():
        pytest.skip("Postgres not reachable; run `make dev`")
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(jobqueue, "enqueue_import", lambda i: sent.append(("import", i)))
    monkeypatch.setattr(
        jobqueue, "enqueue_google_import", lambda i, g: sent.append(("google", i, g))
    )
    monkeypatch.setattr(jobqueue, "enqueue_real", lambda j: sent.append(("real", j)))
    monkeypatch.setattr(jobqueue, "enqueue_mock", lambda j: sent.append(("mock", j)))
    await dispose_engine()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            c.sent = sent  # type: ignore[attr-defined]
            yield c
    finally:
        await dispose_engine()


async def _mark_downloaded(project_id: str, duration_ms: int) -> None:
    engine = create_async_engine(DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        row = await session.scalar(select(MediaImport).where(MediaImport.project_id == project_id))
        project = await session.get(Project, project_id)
        assert row is not None and project is not None
        row.status = "ready"
        project.duration_ms = duration_ms
        await session.commit()
    await engine.dispose()


async def test_an_import_creates_a_waiting_project_and_queues_the_download(
    client: AsyncClient, as_user: SignIn
) -> None:
    headers = as_user()
    r = await client.post(
        "/v1/imports",
        json={"url": "https://youtu.be/jNQXAC9IVRw", "rights_attested": True},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert client.sent[-1][0] == "import"  # type: ignore[attr-defined]
    project = (await client.get(f"/v1/projects/{body['project_id']}", headers=headers)).json()
    assert project["status"] == "pending"
    assert project["title"] == "Imported video"

    refused = await client.post(
        "/v1/imports",
        json={"url": "https://youtu.be/x", "rights_attested": False},
        headers=headers,
    )
    assert refused.status_code == 400


async def test_part_of_an_import_is_processed(client: AsyncClient, as_user: SignIn) -> None:
    headers = as_user()
    pid = (
        await client.post(
            "/v1/imports",
            json={"url": "https://youtu.be/jNQXAC9IVRw", "rights_attested": True},
            headers=headers,
        )
    ).json()["project_id"]

    early = await client.post(f"/v1/projects/{pid}/process", json={}, headers=headers)
    assert early.status_code == 409, "not downloaded yet"

    await _mark_downloaded(pid, 120_000)
    short = await client.post(
        f"/v1/projects/{pid}/process", json={"start_ms": 10_000, "end_ms": 12_000}, headers=headers
    )
    assert short.status_code == 400

    ok = await client.post(
        f"/v1/projects/{pid}/process", json={"start_ms": 30_000, "end_ms": 90_000}, headers=headers
    )
    assert ok.status_code == 202, ok.text
    assert client.sent[-1][0] in {"real", "mock"}  # type: ignore[attr-defined]

    engine = create_async_engine(DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        project = await session.get(Project, pid)
        job = await session.scalar(select(Job).where(Job.project_id == pid))
    await engine.dispose()
    assert project is not None and job is not None
    assert (project.trim_start_ms, project.trim_end_ms) == (30_000, 90_000)
    assert project.duration_ms == 60_000
    assert project.status == "queued"

    again = await client.post(f"/v1/projects/{pid}/process", json={}, headers=headers)
    assert again.status_code == 409, "already processing"


async def test_processing_the_whole_video_sets_no_trim(
    client: AsyncClient, as_user: SignIn
) -> None:
    headers = as_user()
    pid = (
        await client.post(
            "/v1/imports",
            json={"url": "https://vimeo.com/76979871", "rights_attested": True},
            headers=headers,
        )
    ).json()["project_id"]
    await _mark_downloaded(pid, 60_000)
    r = await client.post(f"/v1/projects/{pid}/process", json={}, headers=headers)
    assert r.status_code == 202
    engine = create_async_engine(DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        project = await session.get(Project, pid)
    await engine.dispose()
    assert project is not None
    assert project.trim_start_ms is None and project.trim_end_ms is None


GOOGLE_TOKEN = "ya29." + "t" * 60


async def test_a_drive_pick_is_queued_with_its_token_which_is_never_stored(
    client: AsyncClient, as_user: SignIn
) -> None:
    headers = as_user()
    r = await client.post(
        "/v1/imports",
        json={
            "rights_attested": True,
            "google": {
                "kind": "drive",
                "access_token": GOOGLE_TOKEN,
                "file_id": "1AbCdEfGhIjKlMnOpQrS",
                "filename": "Team meeting.mp4",
            },
        },
        headers=headers,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["source"] == "drive"
    assert body["url"] == "Google Drive: Team meeting.mp4"
    kind, _, google = client.sent[-1]  # type: ignore[attr-defined]
    assert kind == "google" and google["access_token"] == GOOGLE_TOKEN

    engine = create_async_engine(DB_URL)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("select row_to_json(m)::text from media_imports m where project_id = :p"),
                {"p": body["project_id"]},
            )
        ).scalar_one()
    await engine.dispose()
    assert GOOGLE_TOKEN not in row, "the token must not be written down"


async def test_a_photos_pick_must_point_at_google(client: AsyncClient, as_user: SignIn) -> None:
    headers = as_user()
    for base_url, code in (
        ("https://lh3.googleusercontent.com/AbCd_123", 202),
        ("http://minio:9000/visiovox-media/x", 400),
    ):
        r = await client.post(
            "/v1/imports",
            json={
                "rights_attested": True,
                "google": {"kind": "photos", "access_token": GOOGLE_TOKEN, "base_url": base_url},
            },
            headers=headers,
        )
        assert r.status_code == code, (base_url, r.text)


async def test_the_photos_picker_is_proxied_and_its_errors_explained(
    client: AsyncClient, as_user: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    from visiovox_api import google_media

    async def made(token: str) -> dict[str, object]:
        assert token == GOOGLE_TOKEN
        return {
            "session_id": "sess_123456",
            "picker_uri": "https://photos.google.com/picker/x",
            "poll_seconds": 3.0,
        }

    async def polled(token: str, session_id: str) -> dict[str, object]:
        return {"done": True, "items": [{"id": "m1", "is_video": True,
                "base_url": "https://lh3.googleusercontent.com/v", "filename": "clip.mp4",
                "mime_type": "video/mp4"}]}  # fmt: skip

    monkeypatch.setattr(google_media, "create_photos_session", made)
    monkeypatch.setattr(google_media, "poll_photos_session", polled)
    headers = as_user()
    started = await client.post(
        "/v1/google/photos/sessions", json={"access_token": GOOGLE_TOKEN}, headers=headers
    )
    assert started.status_code == 201, started.text
    assert started.json()["picker_uri"].startswith("https://photos.google.com/")
    poll = await client.post(
        "/v1/google/photos/sessions/sess_123456/poll",
        json={"access_token": GOOGLE_TOKEN},
        headers=headers,
    )
    assert poll.json()["items"][0]["filename"] == "clip.mp4"

    async def disabled(token: str) -> dict[str, object]:
        raise google_media.GoogleError("Google Photos is not switched on for this site yet.", 503)

    monkeypatch.setattr(google_media, "create_photos_session", disabled)
    refused = await client.post(
        "/v1/google/photos/sessions", json={"access_token": GOOGLE_TOKEN}, headers=headers
    )
    assert refused.status_code == 503
    assert "switched on" in refused.json()["detail"]
