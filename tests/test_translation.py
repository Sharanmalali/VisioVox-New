"""Translated captions (ADR-0020): languages, the S8 transform, and the API."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pipeline import s8_translate
from visiovox_api import deps, jobqueue
from visiovox_api.config import get_settings
from visiovox_api.db import dispose_engine
from visiovox_api.firebase_auth import FirebaseIdentity
from visiovox_api.languages import CODES as CAPTION_CODES
from visiovox_api.main import app
from visiovox_api.models import Project, Translation
from visiovox_api.translation_languages import BY_CODE, SOURCE_FLORES, TRANSLATION_LANGUAGES

# --------------------------------------------------------------------------
# languages
# --------------------------------------------------------------------------


def test_target_codes_are_unique_and_mapped() -> None:
    codes = [lang.code for lang in TRANSLATION_LANGUAGES]
    assert len(codes) == len(set(codes))
    assert all("_" in lang.nllb for lang in TRANSLATION_LANGUAGES)


def test_languages_the_model_cannot_write_are_not_offered() -> None:
    """Santali and Konkani came back as unknown-piece marks (2026-10-09)."""
    assert "sat" not in BY_CODE
    assert "gom" not in BY_CODE


def test_some_uncaptionable_indian_languages_are_translation_targets() -> None:
    for code in ("or", "mai", "ks", "mni"):
        assert code in BY_CODE
        assert code not in CAPTION_CODES


def test_almost_every_caption_language_can_be_a_source() -> None:
    """Whatever Whisper captioned in can be translated, bar what NLLB lacks."""
    assert CAPTION_CODES - set(SOURCE_FLORES) == {"la", "br", "haw"}


# --------------------------------------------------------------------------
# S8 with a fake model
# --------------------------------------------------------------------------


@dataclass
class _Result:
    hypotheses: list[list[str]]


class _Pieces:
    def EncodeAsPieces(self, value: str) -> list[str]:  # noqa: N802 - sentencepiece's API
        return value.split()

    def DecodePieces(self, pieces: list[str]) -> str:  # noqa: N802
        return " ".join(pieces)


@dataclass
class _Model:
    reply: Callable[[list[str]], list[str]]
    calls: list[Any] = field(default_factory=list)

    def translate_batch(self, batch: list[list[str]], **kwargs: Any) -> list[_Result]:
        self.calls.append((batch, kwargs))
        prefix = kwargs["target_prefix"][0][0]
        return [_Result([[prefix, *self.reply(src[1:-1])]]) for src in batch]


def _translator(reply: Callable[[list[str]], list[str]]) -> s8_translate.Translator:
    return s8_translate.Translator(model=_Model(reply), pieces=_Pieces())


BODY: dict[str, Any] = {
    "language": "en",
    "segments": [
        {"text": "hello there", "start_ms": 100, "end_ms": 900, "words": [{"text": "hello"}]},
        {"text": "  ", "start_ms": 1000, "end_ms": 1200, "words": []},
        {"text": "goodbye", "start_ms": 1500, "end_ms": 2000, "words": []},
    ],
}


def test_translation_keeps_every_segment_and_its_timing() -> None:
    out = s8_translate.translate_transcript(
        BODY, "eng_Latn", "hin_Deva", "hi", _translator(lambda ps: [p.upper() for p in ps])
    )
    assert out["language"] == "hi"
    assert out["source_language"] == "en"
    assert [(s["text"], s["start_ms"], s["end_ms"]) for s in out["segments"]] == [
        ("HELLO THERE", 100, 900),
        ("", 1000, 1200),
        ("GOODBYE", 1500, 2000),
    ]
    # Word order changes in translation, so word timings are not carried.
    assert all(s["words"] == [] for s in out["segments"])


def test_the_language_tags_frame_the_input_and_prime_the_output() -> None:
    translator = _translator(lambda ps: ps)
    s8_translate.translate(["a b"], "eng_Latn", "tam_Taml", translator)
    batch, kwargs = translator.model.calls[0]
    assert batch == [["eng_Latn", "a", "b", "</s>"]]
    assert kwargs["target_prefix"] == [["tam_Taml"]]


def test_same_language_is_not_sent_to_the_model() -> None:
    translator = _translator(lambda ps: ["x"])
    assert s8_translate.translate(["keep me"], "eng_Latn", "eng_Latn", translator) == ["keep me"]
    assert translator.model.calls == []


def test_output_full_of_unknown_pieces_is_an_error_not_a_caption() -> None:
    with pytest.raises(s8_translate.TranslationError):
        s8_translate.translate_transcript(
            BODY, "eng_Latn", "sat_Olck", "sat", _translator(lambda ps: [s8_translate.UNKNOWN])
        )


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

DB_URL = get_settings().database_url
MANIFEST = {"speakers": [{"id": "face_1", "ordinal": 1}, {"id": "face_2", "ordinal": 2}]}


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
    monkeypatch.setattr(jobqueue, "enqueue_translation", sent.append)
    return sent


SignIn = Callable[[], dict[str, str]]


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
            await session.commit()
        await engine.dispose()
    return project_id


async def _set_status(project_id: str, language: str, status: str) -> None:
    engine = create_async_engine(DB_URL)
    async with async_sessionmaker(engine)() as session:
        await session.execute(
            update(Translation)
            .where(Translation.project_id == project_id, Translation.language == language)
            .values(status=status)
        )
        await session.commit()
    await engine.dispose()


@pytest.mark.integration
async def test_requesting_a_translation_queues_it_once(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    url = f"/v1/projects/{project_id}/translations"

    first = await client.post(url, json={"language": "hi"}, headers=headers)
    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    again = await client.post(url, json={"language": "hi"}, headers=headers)
    assert again.status_code == 200
    assert len(queued) == 1


@pytest.mark.integration
async def test_a_ready_translation_lists_one_signed_file_per_speaker(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    await client.post(
        f"/v1/projects/{project_id}/translations", json={"language": "kn"}, headers=headers
    )
    await _set_status(project_id, "kn", "ready")

    body = (await client.get(f"/v1/projects/{project_id}/translations", headers=headers)).json()
    item = body["items"][0]
    assert item["status"] == "ready"
    assert [s["ordinal"] for s in item["speakers"]] == [1, 2]
    assert "translations/kn/spk_1.json" in item["speakers"][0]["url"]
    assert {"code": "or", "name": "Odia", "native": "ଓଡ଼ିଆ", "group": "india"} in body["languages"]


@pytest.mark.integration
async def test_a_failed_translation_is_retried_in_place(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    project_id = await _project(client, headers)
    url = f"/v1/projects/{project_id}/translations"
    await client.post(url, json={"language": "ta"}, headers=headers)
    await _set_status(project_id, "ta", "failed")
    retried = await client.post(url, json={"language": "ta"}, headers=headers)
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"
    assert len(queued) == 2


@pytest.mark.integration
async def test_unknown_languages_and_unfinished_projects_are_refused(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    headers = as_user()
    ready = await _project(client, headers)
    pending = await _project(client, headers, ready=False)
    bad = await client.post(
        f"/v1/projects/{ready}/translations", json={"language": "sat"}, headers=headers
    )
    assert bad.status_code in (400, 422)
    early = await client.post(
        f"/v1/projects/{pending}/translations", json={"language": "hi"}, headers=headers
    )
    assert early.status_code == 409
    assert queued == []


@pytest.mark.integration
async def test_only_the_owner_can_translate(
    client: AsyncClient, as_user: SignIn, queued: list[str]
) -> None:
    project_id = await _project(client, as_user())
    stranger = as_user()
    response = await client.post(
        f"/v1/projects/{project_id}/translations", json={"language": "hi"}, headers=stranger
    )
    assert response.status_code == 404
