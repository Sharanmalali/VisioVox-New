"""Caption language choice: catalogue, validation, and the API round trip.

The load-bearing test is `test_catalogue_is_exactly_what_whisper_supports`: a
language offered but not supported would be forced on Whisper and come back as
captions in some other language, which is worse than detecting automatically.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from authstub import sign_in
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from visiovox_api.config import get_settings
from visiovox_api.db import dispose_engine
from visiovox_api.languages import CAPTION_LANGUAGES, CODES
from visiovox_api.main import app
from visiovox_api.schemas import CreateProjectRequest

INDIA_SUPPORTED = {
    "as",
    "bn",
    "gu",
    "hi",
    "kn",
    "ml",
    "mr",
    "ne",
    "pa",
    "sa",
    "sd",
    "ta",
    "te",
    "ur",
}
INDIA_UNSUPPORTED = {"or", "mai", "kok", "ks", "mni", "brx", "doi", "sat"}


def test_catalogue_is_exactly_what_whisper_supports() -> None:
    tokenizer = pytest.importorskip("faster_whisper.tokenizer")
    assert set(tokenizer._LANGUAGE_CODES) == CODES


def test_indian_languages_are_grouped_and_named_in_their_script() -> None:
    india = {lang.code: lang for lang in CAPTION_LANGUAGES if lang.group == "india"}
    assert set(india) == INDIA_SUPPORTED
    assert all(lang.native for lang in india.values())
    assert not CODES & INDIA_UNSUPPORTED


def test_english_first_then_india() -> None:
    assert CAPTION_LANGUAGES[0].code == "en"
    groups = [lang.group for lang in CAPTION_LANGUAGES[1:]]
    assert groups == sorted(groups)  # all of "india" before "world"


def test_codes_are_unique() -> None:
    assert len(CODES) == len(CAPTION_LANGUAGES)


@pytest.mark.parametrize("code", ["hi", "ta", "en", None])
def test_request_accepts_supported_or_none(code: str | None) -> None:
    body = CreateProjectRequest(title="t", rights_attested=True, language=code)
    assert body.language == code


@pytest.mark.parametrize("code", ["or", "xx", "", "HI", "hindi"])
def test_request_rejects_anything_else(code: str) -> None:
    with pytest.raises(ValidationError):
        CreateProjectRequest(title="t", rights_attested=True, language=code)


# ---- API round trip against the Compose stack ------------------------------

settings = get_settings()


async def _stack_up() -> bool:
    try:
        engine = create_async_engine(settings.database_url)
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        await engine.dispose()
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{settings.s3_endpoint_url}/minio/health/live")
            return r.status_code == 200
    except Exception:
        return False


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    if not await _stack_up():
        pytest.skip("Compose stack not running; run `make dev`")
    await dispose_engine()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        await dispose_engine()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_language_round_trips(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = sign_in(monkeypatch)
    created = await client.post(
        "/v1/projects",
        json={"title": "Tamil interview", "rights_attested": True, "language": "ta"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["language"] == "ta"
    again = await client.get(f"/v1/projects/{created.json()['id']}", headers=headers)
    assert again.json()["language"] == "ta"

    auto = await client.post(
        "/v1/projects", json={"title": "Detect it", "rights_attested": True}, headers=headers
    )
    assert auto.status_code == 201 and auto.json()["language"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unsupported_language_is_refused(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = sign_in(monkeypatch)
    refused = await client.post(
        "/v1/projects",
        json={"title": "Odia talk", "rights_attested": True, "language": "or"},
        headers=headers,
    )
    assert refused.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_limits_list_the_languages(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = sign_in(monkeypatch)
    limits = await client.get("/v1/limits", headers=headers)
    assert limits.status_code == 200, limits.text
    langs = limits.json()["caption_languages"]
    assert langs[0]["code"] == "en"
    hindi = next(lang for lang in langs if lang["code"] == "hi")
    assert hindi == {"code": "hi", "name": "Hindi", "native": "हिन्दी", "group": "india"}
