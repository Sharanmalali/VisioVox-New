"""Translated captions (ADR-0020).

A person picks a language; the GPU worker translates every speaker's captions
with NLLB-200 and writes one caption file per speaker. The page polls the
list until the status is "ready", then reads the signed files.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select

from . import jobqueue
from .deps import CurrentUser, OwnedProject, SessionDep, SettingsDep
from .models import Translation
from .schemas import (
    CreateTranslationRequest,
    TranslatedSpeaker,
    TranslationLanguageOption,
    TranslationListResponse,
    TranslationResponse,
)
from .speakers import speaker_ordinals
from .storage import ObjectStore
from .translation_languages import TRANSLATION_LANGUAGES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/projects", tags=["translations"])

# Enough for any real use; a ceiling on how much GPU one project can ask for.
MAX_PER_PROJECT = 12

LANGUAGES = [
    TranslationLanguageOption(code=lang.code, name=lang.name, native=lang.native, group=lang.group)
    for lang in TRANSLATION_LANGUAGES
]


def translation_key(project_id: str, language: str, ordinal: int) -> str:
    return f"projects/{project_id}/translations/{language}/spk_{ordinal}.json"


async def _response(
    row: Translation, ordinals: set[int], store: ObjectStore | None
) -> TranslationResponse:
    speakers: list[TranslatedSpeaker] = []
    if row.status == "ready" and store is not None:
        for ordinal in sorted(ordinals):
            key = translation_key(row.project_id, row.language, ordinal)
            speakers.append(TranslatedSpeaker(ordinal=ordinal, url=await store.presign_get(key)))
    return TranslationResponse(
        language=row.language, status=row.status, error=row.error_detail, speakers=speakers
    )


@router.get("/{project_id}/translations")
async def list_translations(
    project: OwnedProject, session: SessionDep, settings: SettingsDep
) -> TranslationListResponse:
    rows = (
        await session.scalars(
            select(Translation)
            .where(Translation.project_id == project.id)
            .order_by(Translation.created_at)
        )
    ).all()
    store = ObjectStore(settings) if any(r.status == "ready" for r in rows) else None
    ordinals = speaker_ordinals(project.manifest)
    return TranslationListResponse(
        items=[await _response(r, ordinals, store) for r in rows], languages=LANGUAGES
    )


@router.post("/{project_id}/translations", status_code=status.HTTP_202_ACCEPTED)
async def request_translation(
    body: CreateTranslationRequest,
    project: OwnedProject,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
) -> TranslationResponse:
    if project.status != "ready" or project.manifest is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="captions can be translated once processing has finished",
        )
    row = await session.scalar(
        select(Translation).where(
            Translation.project_id == project.id, Translation.language == body.language
        )
    )
    if row is None:
        count = await session.scalar(
            select(func.count())
            .select_from(Translation)
            .where(Translation.project_id == project.id)
        )
        if (count or 0) >= MAX_PER_PROJECT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"a recording can be translated into at most {MAX_PER_PROJECT} languages",
            )
        row = Translation(project_id=project.id, user_id=user.id, language=body.language)
        session.add(row)
    elif row.status == "failed":
        # A retry in place: the failure may have been transient.
        row.status = "queued"
        row.error_detail = None
        row.finished_at = None
    else:
        # Already queued, running or ready: asking twice changes nothing.
        response.status_code = status.HTTP_200_OK
        ordinals = speaker_ordinals(project.manifest)
        store = ObjectStore(settings) if row.status == "ready" else None
        return await _response(row, ordinals, store)
    await session.commit()

    try:
        jobqueue.enqueue_translation(row.id)
    except Exception:
        logger.warning("could not enqueue translation %s", row.id, exc_info=True)
    return await _response(row, set(), None)
