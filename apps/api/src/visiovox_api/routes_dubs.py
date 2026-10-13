"""Per-speaker dubbing (ADR-0024).

A person picks a language; the GPU worker translates the captions if they are
not translated yet, speaks every speaker's lines in a voice of their own, and
puts them over the room - the recording with only those voices removed.

The same run keeps each speaker's new voice alone (`dubs/<language>/tracks/`),
and `GET .../dubs/<language>/manifest` serves them in the original's manifest
shape, so the player offers every person - and the whole dub - exactly as it
does for the recording itself.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select

from . import jobqueue
from .deps import CurrentUser, OwnedProject, SessionDep, SettingsDep
from .models import Dub
from .routes_media import sign_manifest
from .schemas import CreateDubRequest, DubListResponse, DubResponse, TranslationLanguageOption
from .speakers import with_names
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


def has_tracks(row: Dub) -> bool:
    """Whether this dub kept each speaker's voice alone (dubs made since 2026-10-13)."""
    return bool((row.stats or {}).get("tracks"))


def dub_manifest(
    manifest: dict[str, Any],
    project_id: str,
    language: str,
    tracks: dict[str, Any],
    media_base: str,
) -> dict[str, Any]:
    """The original's manifest with every audio track swapped for the dub's.

    Each dubbed speaker plays their new voice; the "everyone" track plays the
    dub's soundtrack. The picture, faces, photos and captions stay the
    original's - the faces are the same faces. Speakers with nothing to dub
    (no captioned speech) are left out, having no dubbed voice to play.
    HLS renditions follow the worker's order: speakers by ordinal, then the mix.
    """
    base = f"{media_base.rstrip('/')}/projects/{project_id}/dubs/{language}/tracks/"
    sizes = tracks.get("bytes") or {}
    dubbed = sorted(int(o) for o in tracks.get("speakers") or [])
    streaming = tracks.get("playback_hint") == "hls"
    speakers: list[dict[str, Any]] = []
    for speaker in manifest.get("speakers") or []:
        if not isinstance(speaker, dict) or speaker.get("ordinal") not in dubbed:
            continue
        ordinal = int(speaker["ordinal"])
        audio: dict[str, Any] = {
            "faithful": {
                "url": f"{base}spk_{ordinal}.m4a",
                "bytes": int(sizes.get(str(ordinal), 0)),
            }
        }
        if streaming:
            audio["hls"] = f"{base}audio/a{dubbed.index(ordinal)}.m3u8"
        copy = {k: v for k, v in speaker.items() if k not in ("audio", "peaks_url")}
        copy["audio"] = audio
        speakers.append(copy)
    out = {
        k: v
        for k, v in manifest.items()
        if k not in ("speakers", "mixed", "master_playlist", "hls")
    }
    out["project_id"] = project_id
    out["speakers"] = speakers
    out["mixed"] = {"audio_url": f"{base}mix.m4a"}
    out["playback_hint"] = "hls" if streaming else "webaudio"
    out["warnings"] = []
    out["dub_language"] = language
    if streaming:
        out["mixed"]["hls"] = f"{base}audio/a{len(dubbed)}.m3u8"
        out["master_playlist"] = f"{base}master.m3u8"
    return out


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
    elif row.status == "failed" or (row.status == "ready" and not has_tracks(row)):
        # A failed dub is tried again; one made before each speaker's voice was
        # kept alone is made again, so it can be heard speaker by speaker.
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


@router.get("/{project_id}/dubs/{language}/manifest")
async def get_dub_manifest(
    language: str, project: OwnedProject, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """The dub as the player plays it: each speaker's dubbed voice, and the whole dub."""
    row = await session.scalar(
        select(Dub).where(Dub.project_id == project.id, Dub.language == language)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no dub in that language")
    if row.status != "ready" or project.manifest is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"the dub is {row.status}")
    if not has_tracks(row):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this dub was made before speakers were kept apart; dub it again",
        )
    manifest = dub_manifest(
        dict(project.manifest),
        project.id,
        language,
        (row.stats or {})["tracks"],
        settings.public_media_base_url,
    )
    return await sign_manifest(with_names(manifest, project.speaker_names), settings)
