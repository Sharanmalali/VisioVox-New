"""Import a recording from a link, Google Drive or Google Photos instead of
uploading it (docs/28).

The worker downloads the video (yt-dlp) into the project's source slot. The
project then waits, status `pending`, while the person picks the part they
want; processing starts only when they say so, on that part alone.

Only YouTube and Vimeo links are accepted. A downloader that follows any URL
is a request-forgery tool pointed at this server's own network (the object
store, the database, the metadata endpoints of whatever it runs on), and the
extractor allow-list in the worker is the second line of the same defence.
Google picks are identified, never addressed: a Drive file id, or a Photos
item whose bytes must live on googleusercontent.com (google_media.py).
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from . import audit, google_media, jobqueue, quotas
from .deps import CurrentUser, OwnedProject, SessionDep, SettingsDep
from .models import Job, MediaImport, Project
from .schemas import (
    GoogleImport,
    ImportRequest,
    ImportResponse,
    PhotosPollResponse,
    PhotosSessionRequest,
    PhotosSessionResponse,
    ProcessRequest,
)
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
        source=row.source,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        progress=row.progress,
        source_title=row.source_title,
        error=row.error_detail,
        duration_ms=project.duration_ms if row.status == "ready" else None,
        preview_url=preview,
        downloaded_bytes=row.downloaded_bytes,
        total_bytes=row.total_bytes,
        speed_bytes_per_second=row.speed_bps,
        eta_seconds=row.eta_seconds if row.status == "downloading" else None,
    )


@router.post("/imports", status_code=status.HTTP_202_ACCEPTED)
async def start_import(
    body: ImportRequest, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> ImportResponse:
    if not body.rights_attested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="rights attestation is required"
        )
    google = check_google(body.google) if body.google is not None else None
    url = check_url(body.url) if body.url is not None else _describe(google)
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
    source = google.kind if google is not None else "link"
    row = MediaImport(project_id=project.id, user_id=user.id, url=url, source=source)
    session.add(row)
    await audit.record(
        session,
        "project.import_requested",
        user_id=user.id,
        target_type="project",
        target_id=project.id,
        detail={"source": source, "host": urlsplit(url).hostname if google is None else None},
    )
    await session.commit()
    if google is not None:
        jobqueue.enqueue_google_import(row.id, google.model_dump())
    else:
        jobqueue.enqueue_import(row.id)
    return await _response(row, project, settings)


def check_google(google: GoogleImport) -> GoogleImport:
    """A Drive file id, or a Photos item on Google's media host - nothing else."""
    if google.kind == "drive":
        if not google.file_id or not google_media.DRIVE_ID.match(google.file_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="choose a video in Google Drive"
            )
    elif not google.base_url or not google_media.PHOTOS_MEDIA.match(google.base_url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="choose a video in Google Photos"
        )
    if google.session_id is not None and not google_media.SESSION_ID.match(google.session_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bad picker session")
    return google


def _describe(google: GoogleImport | None) -> str:
    """What the import page shows as the source."""
    assert google is not None
    where = "Google Drive" if google.kind == "drive" else "Google Photos"
    return f"{where}: {google.filename or 'video'}"


@router.post("/google/photos/sessions", status_code=status.HTTP_201_CREATED)
async def start_photos_pick(body: PhotosSessionRequest, user: CurrentUser) -> PhotosSessionResponse:
    """A Google Photos picker for one video, opened by the person in a new tab."""
    try:
        made = await google_media.create_photos_session(body.access_token)
    except google_media.GoogleError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    if not made.get("session_id") or not made.get("picker_uri"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Google Photos did not open a picker"
        )
    return PhotosSessionResponse(**made)


@router.post("/google/photos/sessions/{session_id}/poll")
async def poll_photos_pick(
    session_id: str, body: PhotosSessionRequest, user: CurrentUser
) -> PhotosPollResponse:
    """Has the person picked a video yet? (The token travels in the body, not the URL.)"""
    if not google_media.SESSION_ID.match(session_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bad picker session")
    try:
        state = await google_media.poll_photos_session(body.access_token, session_id)
    except google_media.GoogleError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return PhotosPollResponse(**state)


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
