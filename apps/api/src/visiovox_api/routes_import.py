"""Import a recording from a link instead of uploading it (docs/28).

The worker downloads the video (yt-dlp) into the project's source slot. The
project then waits, status `pending`, while the person picks the part they
want; processing starts only when they say so, on that part alone.

Only YouTube and Vimeo links are accepted. A downloader that follows any URL
is a request-forgery tool pointed at this server's own network (the object
store, the database, the metadata endpoints of whatever it runs on), and the
extractor allow-list in the worker is the second line of the same defence.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from . import audit, jobqueue, quotas
from .deps import CurrentUser, OwnedProject, SessionDep, SettingsDep
from .models import Job, MediaImport, Project
from .schemas import ImportRequest, ImportResponse, ProcessRequest
from .storage import ObjectStore, object_key

router = APIRouter(prefix="/v1", tags=["imports"])

IMPORT_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "vimeo.com",
        "www.vimeo.com",
        "player.vimeo.com",
    }
)
MIN_PART_MS = 5_000
DEFAULT_TITLE = "Imported video"


def check_url(url: str) -> str:
    """The link, normalised, or HTTP 400 saying what is accepted."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or host not in IMPORT_HOSTS or parts.username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Paste a YouTube or Vimeo link (for example https://www.youtube.com/watch?v=...).",
        )
    return parts.geturl()


async def _response(row: MediaImport, project: Project, settings: SettingsDep) -> ImportResponse:
    preview = None
    if row.status == "ready" and project.source_key:
        preview = await ObjectStore(settings).presign_get(project.source_key)
    return ImportResponse(
        project_id=project.id,
        url=row.url,
        status=row.status,  # type: ignore[arg-type]
        progress=row.progress,
        source_title=row.source_title,
        error=row.error_detail,
        duration_ms=project.duration_ms if row.status == "ready" else None,
        preview_url=preview,
    )


@router.post("/imports", status_code=status.HTTP_202_ACCEPTED)
async def start_import(
    body: ImportRequest, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> ImportResponse:
    if not body.rights_attested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="rights attestation is required"
        )
    url = check_url(body.url)
    await quotas.assert_concurrency_available(session, user.id, settings)
    # An import costs what an upload costs: a slot today and the storage behind it.
    await quotas.consume(session, user.id, quotas.UPLOADS, 1, settings)

    project = Project(
        user_id=user.id,
        title=body.title or DEFAULT_TITLE,
        status="pending",
        language=body.language,
    )
    session.add(project)
    await session.flush()
    project.source_key = object_key(user.id, project.id, "source.mp4")
    row = MediaImport(project_id=project.id, user_id=user.id, url=url)
    session.add(row)
    await audit.record(
        session,
        "project.import_requested",
        user_id=user.id,
        target_type="project",
        target_id=project.id,
        detail={"host": urlsplit(url).hostname},
    )
    await session.commit()
    jobqueue.enqueue_import(row.id)
    return await _response(row, project, settings)


@router.get("/projects/{project_id}/import")
async def get_import(
    project: OwnedProject, session: SessionDep, settings: SettingsDep
) -> ImportResponse:
    row = await session.scalar(select(MediaImport).where(MediaImport.project_id == project.id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not an import")
    return await _response(row, project, settings)


@router.post("/projects/{project_id}/process", status_code=status.HTTP_202_ACCEPTED)
async def process_part(
    body: ProcessRequest, project: OwnedProject, session: SessionDep, settings: SettingsDep
) -> dict[str, str]:
    """Start processing an imported recording, optionally only part of it."""
    row = await session.scalar(select(MediaImport).where(MediaImport.project_id == project.id))
    if row is None or row.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="the video has not finished downloading"
        )
    if project.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"this project is already {project.status}"
        )
    length = project.duration_ms or 0
    start = body.start_ms or 0
    end = min(body.end_ms if body.end_ms is not None else length, length)
    if end - start < MIN_PART_MS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose at least 5 seconds to process.",
        )
    if end - start > settings.max_duration_seconds * 1000:
        minutes = settings.max_duration_seconds // 60
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Choose at most {minutes} minutes; trim the part down and try again.",
        )
    await quotas.assert_concurrency_available(session, project.user_id, settings)

    whole = start == 0 and end == length
    project.trim_start_ms = None if whole else start
    project.trim_end_ms = None if whole else end
    project.duration_ms = end - start
    project.status = "queued"
    job = Job(project_id=project.id, status="queued", pipeline_mode=settings.pipeline_mode)
    session.add(job)
    await session.commit()
    if settings.pipeline_mode == "real":
        jobqueue.enqueue_real(job.id)
    else:
        jobqueue.enqueue_mock(job.id)
    return {
        "job_id": job.id,
        "status": "queued",
        "requested_at": dt.datetime.now(dt.UTC).isoformat(),
    }
