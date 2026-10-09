"""Meeting summary and action items (ADR-0021).

Written on request by a local language model on the GPU worker. Every item
cites the transcript lines it rests on; the worker drops anything that does
not cite a real line.
"""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from . import jobqueue
from .deps import CurrentUser, OwnedProject, SessionDep
from .models import Summary
from .schemas import SummaryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/projects", tags=["summaries"])


def _response(row: Summary | None) -> SummaryResponse:
    if row is None:
        return SummaryResponse(status="none")
    return SummaryResponse(
        status=row.status,
        content=row.content if row.status == "ready" else None,
        error=row.error_detail,
        created_at=row.created_at,
    )


@router.get("/{project_id}/summary")
async def get_summary(project: OwnedProject, session: SessionDep) -> SummaryResponse:
    row = await session.scalar(select(Summary).where(Summary.project_id == project.id))
    return _response(row)


@router.post("/{project_id}/summary", status_code=status.HTTP_202_ACCEPTED)
async def request_summary(
    project: OwnedProject, user: CurrentUser, session: SessionDep
) -> SummaryResponse:
    """Write the summary, or write it again (names may have changed)."""
    if project.status != "ready" or project.manifest is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a summary can be written once processing has finished",
        )
    row = await session.scalar(select(Summary).where(Summary.project_id == project.id))
    if row is not None and row.status in ("queued", "running"):
        return _response(row)
    if row is None:
        row = Summary(project_id=project.id, user_id=user.id)
        session.add(row)
    else:
        row.status = "queued"
        row.error_detail = None
        row.finished_at = None
        row.created_at = dt.datetime.now(dt.UTC)
    await session.commit()
    try:
        jobqueue.enqueue_summary(row.id)
    except Exception:
        logger.warning("could not enqueue summary %s", row.id, exc_info=True)
    return _response(row)
