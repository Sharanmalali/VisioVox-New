"""User-study sessions through the API (docs/user-study)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from visiovox_api import deps
from visiovox_api.config import get_settings
from visiovox_api.db import dispose_engine
from visiovox_api.firebase_auth import FirebaseIdentity
from visiovox_api.main import app

pytestmark = pytest.mark.integration
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


def _session(participant: str = "P01", **extra: Any) -> dict[str, Any]:
    return {
        "participant": participant,
        "order": "visiovox-first",
        "tasks": [
            {
                "task": "T1",
                "condition": "visiovox",
                "recording": "A",
                "seconds": 42.5,
                "answer": "the budget",
            }
        ],
        "sus": [4, 2, 5, 1, 4, 2, 5, 1, 4, 2],
        "preference": "visiovox",
        **extra,
    }


async def test_a_session_is_saved_once_and_listed(client: AsyncClient, as_user: SignIn) -> None:
    headers = as_user()
    saved = await client.post("/v1/study/sessions", json=_session(), headers=headers)
    assert saved.status_code == 201, saved.text
    again = await client.post("/v1/study/sessions", json=_session(), headers=headers)
    assert again.status_code == 409
    listed = (await client.get("/v1/study/sessions", headers=headers)).json()
    assert [s["participant"] for s in listed] == ["P01"]
    assert listed[0]["data"]["tasks"][0]["seconds"] == 42.5


@pytest.mark.parametrize(
    "bad",
    [
        {"participant": "Priya Sharma"},  # a name, not a code
        {"sus": [4, 2, 5]},  # not ten answers
        {"sus": [6, 2, 5, 1, 4, 2, 5, 1, 4, 2]},  # out of range
        {"preference": "maybe"},
    ],
)
async def test_malformed_sessions_are_refused(
    client: AsyncClient, as_user: SignIn, bad: dict[str, Any]
) -> None:
    response = await client.post("/v1/study/sessions", json=_session(**bad), headers=as_user())
    assert response.status_code == 422


async def test_sessions_are_private_to_the_facilitator(
    client: AsyncClient, as_user: SignIn
) -> None:
    await client.post("/v1/study/sessions", json=_session("P07"), headers=as_user())
    other = (await client.get("/v1/study/sessions", headers=as_user())).json()
    assert other == []
