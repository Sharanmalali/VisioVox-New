"""VisioVox control-plane API.

Routes are thin: validation via Pydantic, authorisation via dependencies, and
the actual work in service modules. That keeps the ownership check (invariant
4) visible in each handler's signature rather than buried in its body.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    FastAPI,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from . import audit, idempotency, logredact, problems
from .config import get_settings
from .db import dispose_engine
from .deps import CurrentUser, OwnedProject, SessionDep, SettingsDep
from .firebase_auth import delete_identity
from .middleware import (
    CorrelationIdMiddleware,
    GlobalRateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from .models import Job, JobStage, Project
from .quotas import QuotaExceededError
from .ratelimit import RateLimiter
from .redis_client import close_redis, get_redis
from .routes_dubs import router as dubs_router
from .routes_exports import router as exports_router
from .routes_hls import router as hls_router
from .routes_import import router as import_router
from .routes_media import limits_router
from .routes_media import router as media_router
from .routes_share import router as share_router
from .routes_study import router as study_router
from .routes_summaries import router as summaries_router
from .routes_translations import router as translations_router
from .schemas import (
    CreateProjectRequest,
    ErrorResponse,
    JobResponse,
    JobStageResponse,
    ProjectListResponse,
    ProjectResponse,
    RenameSpeakerRequest,
    UserResponse,
)
from .speakers import clean_name, speaker_ordinals

API_PREFIX = "/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # At startup rather than import: uvicorn configures its loggers first.
    logredact.install()
    yield
    await dispose_engine()
    await close_redis()


app = FastAPI(
    title="VisioVox API",
    version="0.1.0",
    description="Control plane for audio-visual target speaker extraction.",
    lifespan=lifespan,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)

_settings = get_settings()
_redis = get_redis(_settings)
_limiter = RateLimiter(_redis, _settings)

# Every error leaves as RFC 9457 Problem Details with a correlation id
# (docs/11 §1), including the ones FastAPI would otherwise format itself.
app.add_exception_handler(HTTPException, problems.http_exception_handler)
app.add_exception_handler(RequestValidationError, problems.validation_exception_handler)
app.add_exception_handler(Exception, problems.unhandled_exception_handler)


async def _quota_handler(request: Request, exc: Exception) -> JSONResponse:
    """A quota refusal must say what ran out and when it comes back.

    "Too many requests" with no number is the least actionable error a product
    can return; the user cannot tell whether to retry in a minute or next month.
    """
    assert isinstance(exc, QuotaExceededError)
    return problems.problem(
        request,
        429,
        f"You have used {exc.used} of {exc.limit} {exc.metric.replace('_', ' ')} for this period.",
        code="QUOTA_EXCEEDED",
        title="Quota exceeded",
        headers={"Retry-After": str(exc.resets_in_seconds)},
        metric=exc.metric,
        used=exc.used,
        limit=exc.limit,
    )


app.add_exception_handler(QuotaExceededError, _quota_handler)

# Starlette wraps later-added middleware AROUND earlier ones, so this list reads
# inside-out. The resulting order is:
#   CORS -> security headers -> correlation id -> global limit -> routes
# Security headers sit outside the limiter so a 429 carries them too, and the
# correlation id is set before anything that might need to log or render it.
app.add_middleware(GlobalRateLimitMiddleware, settings=_settings, limiter=_limiter)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware, settings=_settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_settings.next_public_app_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Correlation-Id",
        "RateLimit-Limit",
        "RateLimit-Remaining",
        "RateLimit-Reset",
    ],
)

health = APIRouter(tags=["health"])
auth = APIRouter(prefix=f"{API_PREFIX}/auth", tags=["auth"])
projects = APIRouter(prefix=f"{API_PREFIX}/projects", tags=["projects"])


@health.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@health.get("/readyz")
async def readyz(session: SessionDep) -> dict[str, str]:
    """Readiness means the database answers, not merely that the process is up."""
    await session.execute(select(1))
    return {"status": "ready"}


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


@auth.delete("/me", status_code=status.HTTP_202_ACCEPTED)
async def delete_account(
    user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> dict[str, str]:
    """Delete the account, both halves of it.

    The local row cascades to projects, uploads, exports and shares. The
    Firebase identity has to go separately, or the person signs in again
    tomorrow and a fresh row is created — an account that looks deleted and is
    not (ADR-0016 §Consequences).

    Firebase first: if it fails, the local row is still here and the operation
    can be retried. The other order leaves an identity with nothing behind it
    and no record of what it owned.
    """
    if user.firebase_uid:
        delete_identity(user.firebase_uid, settings)

    user.deleted_at = dt.datetime.now(dt.UTC)
    await audit.record(
        session,
        "account.deleted",
        user_id=user.id,
        target_type="user",
        target_id=user.id,
    )
    await session.commit()
    return {"status": "deletion_scheduled", "user_id": user.id}


@auth.get("/me")
async def me(user: CurrentUser) -> UserResponse:
    """Who the caller is, from their Firebase token.

    The row is created or refreshed by the dependency, so this doubles as the
    call a freshly signed-in client makes to materialise itself server-side.
    """
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        plan=user.plan,
        retention_days=user.retention_days,
        allow_training_use=user.allow_training_use,
        persist_voiceprints=user.persist_voiceprints,
        created_at=user.created_at,
    )


app.include_router(auth)


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------


def _project_response(p: Project) -> ProjectResponse:
    return ProjectResponse(
        id=p.id,
        title=p.title,
        status=p.status,
        duration_ms=p.duration_ms,
        has_video=p.has_video,
        language=p.language,
        speaker_count=p.speaker_count,
        overlap_ratio=p.overlap_ratio,
        difficulty=p.difficulty,
        warnings=list(p.warnings or []),
        created_at=p.created_at,
        speaker_names=dict(p.speaker_names or {}),
    )


@projects.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: CreateProjectRequest,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    if not body.rights_attested:
        # FR-UPL-08: refuse rather than record a false attestation.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="rights attestation is required",
        )

    # A dropped connection on this endpoint costs a duplicate project, and the
    # next call creates a duplicate multipart upload behind it.
    key = idempotency.validate_key(idempotency_key, required=False)
    fp = idempotency.fingerprint(body.model_dump())
    if key:
        replay = await idempotency.begin(
            _redis,
            settings,
            user_id=user.id,
            endpoint="create_project",
            client_key=key,
            body_fingerprint=fp,
        )
        if replay is not None:
            response.status_code = replay.status_code
            return replay.body

    try:
        project = Project(
            user_id=user.id, title=body.title, status="pending", language=body.language
        )
        session.add(project)
        await session.commit()
    except Exception:
        # Otherwise the key stays parked as in-flight for 24 h and a transient
        # error becomes a day-long outage for this one operation.
        if key:
            await idempotency.release(
                _redis, user_id=user.id, endpoint="create_project", client_key=key
            )
        raise

    payload = _project_response(project)
    if key:
        await idempotency.complete(
            _redis,
            user_id=user.id,
            endpoint="create_project",
            client_key=key,
            body_fingerprint=fp,
            status_code=status.HTTP_201_CREATED,
            body=payload.model_dump(mode="json"),
        )
    return payload


@projects.get("")
async def list_projects(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ProjectListResponse:
    rows = (
        await session.scalars(
            select(Project)
            .where(Project.user_id == user.id, Project.deleted_at.is_(None))
            .order_by(Project.created_at.desc())
            .limit(limit)
        )
    ).all()
    return ProjectListResponse(items=[_project_response(p) for p in rows])


@projects.get("/{project_id}")
async def get_project(project: OwnedProject) -> ProjectResponse:
    return _project_response(project)


@projects.put("/{project_id}/speakers/{ordinal}/name")
async def rename_speaker(
    project: OwnedProject,
    session: SessionDep,
    body: RenameSpeakerRequest,
    ordinal: Annotated[int, Path(ge=1, le=8)],
) -> ProjectResponse:
    """Name a speaker ("Speaker 2" -> "Priya"), or clear the name."""
    if ordinal not in speaker_ordinals(project.manifest):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such speaker")
    names = dict(project.speaker_names or {})
    name = clean_name(body.name)
    if name is None:
        names.pop(str(ordinal), None)
    else:
        names[str(ordinal)] = name
    # A new dict, so SQLAlchemy sees the JSONB change.
    project.speaker_names = names
    await session.commit()
    return _project_response(project)


@projects.delete("/{project_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_project(project: OwnedProject, session: SessionDep) -> dict[str, str]:
    project.deleted_at = dt.datetime.now(dt.UTC)
    await session.commit()
    return {"status": "deletion_scheduled", "project_id": project.id}


@projects.get("/{project_id}/job")
async def get_job(project: OwnedProject, session: SessionDep) -> JobResponse:
    job = await session.scalar(select(Job).where(Job.project_id == project.id))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no job yet")
    stages = (
        await session.scalars(
            select(JobStage).where(JobStage.job_id == job.id).order_by(JobStage.ordinal)
        )
    ).all()
    return JobResponse(
        id=job.id,
        project_id=job.project_id,
        status=job.status,
        progress=job.progress,
        pipeline_mode=job.pipeline_mode,
        error_code=job.error_code,
        stages=[
            JobStageResponse(
                stage=s.stage,
                status=s.status,
                ordinal=s.ordinal,
                duration_ms=s.duration_ms,
                warnings=list(s.warnings or []),
            )
            for s in stages
        ],
    )


app.include_router(health)
app.include_router(projects)
app.include_router(media_router)
app.include_router(limits_router)
app.include_router(exports_router)
app.include_router(share_router)
app.include_router(translations_router)
app.include_router(summaries_router)
app.include_router(dubs_router)
app.include_router(study_router)
app.include_router(hls_router)
app.include_router(import_router)
