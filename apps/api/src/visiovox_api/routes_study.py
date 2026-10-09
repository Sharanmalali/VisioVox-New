"""The user study's sessions (docs/user-study).

The facilitator runs each session from their own signed-in browser; sessions
are stored against that account and exported for analysis. Participants are
anonymous codes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .deps import CurrentUser, SessionDep
from .models import StudySession
from .schemas import StudySessionRequest, StudySessionResponse

router = APIRouter(prefix="/v1/study", tags=["study"])


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def save_session(
    body: StudySessionRequest, user: CurrentUser, session: SessionDep
) -> StudySessionResponse:
    row = StudySession(
        user_id=user.id, participant=body.participant, data=body.model_dump(mode="json")
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"participant {body.participant} already has a session",
        ) from exc
    return StudySessionResponse(
        id=row.id, participant=row.participant, created_at=row.created_at, data=row.data
    )


@router.get("/sessions")
async def list_sessions(user: CurrentUser, session: SessionDep) -> list[StudySessionResponse]:
    rows = (
        await session.scalars(
            select(StudySession)
            .where(StudySession.user_id == user.id)
            .order_by(StudySession.created_at)
        )
    ).all()
    return [
        StudySessionResponse(
            id=r.id, participant=r.participant, created_at=r.created_at, data=r.data
        )
        for r in rows
    ]
