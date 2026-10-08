"""Shared FastAPI dependencies: current user, and ownership-checked project lookup.

Identity comes from a Firebase ID token (ADR-0016). This module is where that
token becomes a local `users` row — created on first sight, refreshed from the
token's claims on every request, and never trusted beyond what the signature
covers.

`owned_project` exists because invariant 4 says every artifact access is
ownership-checked server-side, and IDOR is the highest-impact vulnerability in
this system. Making it a dependency rather than a line of code in each handler
means a new endpoint gets the check by declaring its parameter, and forgetting
it is visible in the signature rather than buried in the body.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import get_session
from .firebase_auth import FirebaseIdentity, verify
from .models import Project, User

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


async def _create_user(session: AsyncSession, identity: FirebaseIdentity) -> User:
    """Insert the row for a new identity, tolerating a concurrent first request.

    A browser's first page after sign-up fires several API calls at once, and
    each one reaches here having found no row. Without a guard all but one
    fail on the unique email / uid constraint and answer 500 - seen in
    production on a new account's first upload page. The insert runs in a
    savepoint, and on a conflict the row the other request created is read
    back instead.
    """
    user = User(
        firebase_uid=identity.uid,
        email=identity.email or f"{identity.uid}@firebase.local",
        display_name=identity.display_name,
    )
    try:
        async with session.begin_nested():
            session.add(user)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(select(User).where(User.firebase_uid == identity.uid))
        if existing is None and identity.email:
            existing = await session.scalar(select(User).where(User.email == identity.email))
        if existing is None:
            raise
        existing.firebase_uid = identity.uid
        return existing
    return user


async def sync_user(session: AsyncSession, identity: FirebaseIdentity) -> User:
    """Find or create the local row for a verified Firebase identity.

    Created on first request rather than by a signup webhook: a webhook is a
    second thing that can be down when someone signs up, and the first
    authenticated request already carries everything the row needs.

    The email is refreshed from the token every time, because Firebase is the
    source of truth for it and a stale local copy is how support tickets start.
    """
    user = await session.scalar(select(User).where(User.firebase_uid == identity.uid))

    if user is None:
        # An account may predate Firebase, or have been created by a provider
        # that returns the same address. Adopt it by uid rather than creating a
        # duplicate, which would orphan every project the person already has.
        if identity.email:
            user = await session.scalar(select(User).where(User.email == identity.email))
        if user is not None:
            user.firebase_uid = identity.uid
        else:
            user = await _create_user(session, identity)

    if identity.email and user.email != identity.email:
        user.email = identity.email
    if identity.display_name and user.display_name != identity.display_name:
        user.display_name = identity.display_name
    user.email_verified_at = (
        user.email_verified_at or dt.datetime.now(dt.UTC) if identity.email_verified else None
    )
    user.auth_provider = identity.provider

    await session.flush()
    return user


async def current_user(request: Request, session: SessionDep, settings: SettingsDep) -> User:
    identity = verify(_bearer(request), settings)

    user = await sync_user(session, identity)
    if user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account unavailable")

    await session.commit()
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def owned_project(
    project_id: Annotated[str, Path(pattern=r"^prj_[0-9A-HJKMNP-TV-Z]{26}$")],
    user: CurrentUser,
    session: SessionDep,
) -> Project:
    """Fetch a project, or 404 if it is not this user's.

    404 rather than 403 on purpose: 403 confirms the id exists, which turns the
    endpoint into an existence oracle for other people's projects.
    """
    project = await session.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == user.id,
            Project.deleted_at.is_(None),
        )
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


OwnedProject = Annotated[Project, Depends(owned_project)]


# --------------------------------------------------------------------------
# SSE authentication
# --------------------------------------------------------------------------
#
# `EventSource` cannot set an Authorization header — the API simply does not
# exist in the browser — so the progress stream accepts the ID token as a query
# parameter instead.
#
# The trade is real and worth naming: URLs reach access logs, proxy logs and
# `Referer` headers in a way headers do not. It is acceptable only because of
# what a Firebase ID token is: one hour maximum, revocable, and carrying no
# refresh capability of its own. The access log must not record query strings
# for this path.


async def current_user_sse(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    access_token: Annotated[str | None, Query()] = None,
) -> User:
    """Authenticate a stream, from the header if present, else the query."""
    header = request.headers.get("authorization", "")
    scheme, _, header_token = header.partition(" ")
    token = header_token if scheme.lower() == "bearer" and header_token else access_token

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing access token")

    user = await sync_user(session, verify(token, settings))
    if user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account unavailable")
    await session.commit()
    return user


CurrentUserSSE = Annotated[User, Depends(current_user_sse)]


async def owned_project_sse(
    project_id: Annotated[str, Path(pattern=r"^prj_[0-9A-HJKMNP-TV-Z]{26}$")],
    user: CurrentUserSSE,
    session: SessionDep,
) -> Project:
    """Ownership check for the stream (invariant 4), 404 for the same reason."""
    project = await session.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == user.id,
            Project.deleted_at.is_(None),
        )
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


OwnedProjectSSE = Annotated[Project, Depends(owned_project_sse)]
