"""Per-speaker dubbing (ADR-0024).

A person picks a language; the GPU worker translates the captions if they are
not translated yet, speaks every speaker's lines in a voice of their own, and
puts them over the room - the recording with only those voices removed.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select

from . import jobqueue
from .deps import CurrentUser, OwnedProject, SessionDep, SettingsDep
from .models import Dub
from .schemas import CreateDubRequest, DubListResponse, DubResponse, TranslationLanguageOption
from .storage import ObjectStore
from .translation_languages import BY_CODE, DUB_LANGUAGES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/projects", tags=["dubs"])

# Each dub is minutes of GPU; a ceiling per project.
MAX_PER_PROJECT = 6

LANGUAGES = [
    TranslationLanguageOption(
        code=BY_CODE[c].code, name=BY_CODE[c].name, native=BY_CODE[c].native, group=BY_CODE[c].group
    )
    for c in DUB_LANGUAGES
]


def dub_key(project_id: str, language: str) -> str:
    return f"projects/{project_id}/dubs/{language}/dub.mp4"


async def _response(row: Dub, store: ObjectStore | None) -> DubResponse:
    url = None
    if row.status == "ready" and store is not None:
        url = await store.presign_get(
            dub_key(row.project_id, row.language), download_as=f"dub-{row.language}.mp4"
        )
    return DubResponse(
        language=row.language, status=row.status, error=row.error_detail, stats=row.stats, url=url
    )


@router.get("/{project_id}/dubs")
async def list_dubs(
    project: OwnedProject, session: SessionDep, settings: SettingsDep
) -> DubListResponse:
    rows = (
        await session.scalars(
            select(Dub).where(Dub.project_id == project.id).order_by(Dub.created_at)
        )
    ).all()
    store = ObjectStore(settings) if any(r.status == "ready" for r in rows) else None
    return DubListResponse(items=[await _response(r, store) for r in rows], languages=LANGUAGES)


@router.post("/{project_id}/dubs", status_code=status.HTTP_202_ACCEPTED)
async def request_dub(
    body: CreateDubRequest,
    project: OwnedProject,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
) -> DubResponse:
    if project.status != "ready" or project.manifest is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a recording can be dubbed once processing has finished",
        )
    row = await session.scalar(
        select(Dub).where(Dub.project_id == project.id, Dub.language == body.language)
    )
    if row is None:
        count = await session.scalar(
            select(func.count()).select_from(Dub).where(Dub.project_id == project.id)
        )
        if (count or 0) >= MAX_PER_PROJECT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"a recording can be dubbed into at most {MAX_PER_PROJECT} languages",
            )
        row = Dub(project_id=project.id, user_id=user.id, language=body.language)
        session.add(row)
    elif row.status == "failed":
        row.status = "queued"
        row.error_detail = None
        row.finished_at = None
    else:
        response.status_code = status.HTTP_200_OK
        store = ObjectStore(settings) if row.status == "ready" else None
        return await _response(row, store)
    await session.commit()
    try:
        jobqueue.enqueue_dub(row.id)
    except Exception:
        logger.warning("could not enqueue dub %s", row.id, exc_info=True)
    return await _response(row, None)
