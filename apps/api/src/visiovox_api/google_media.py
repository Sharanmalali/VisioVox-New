"""Videos from a person's Google Drive and Google Photos (docs/28).

The browser obtains a short-lived Google access token from the person - for
Drive only the file they pick (`drive.file`), for Photos only the items they
pick (`photospicker.mediaitems.readonly`) - and the worker downloads that one
video into the project's source slot, exactly as a link import does. The
token is never stored: it travels in the job message and expires within the
hour.

Google Photos since 2025 offers no library listing to apps; the Photos Picker
API is the way. Its session calls are made here, server-side, so the browser
needs no cross-origin access to Google's APIs.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

PHOTOS_API = "https://photospicker.googleapis.com/v1"
DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
# Where a picked Photos item's bytes live; anything else is refused, so the
# worker cannot be pointed at another host.
PHOTOS_MEDIA = re.compile(r"^https://[a-z0-9-]+\.googleusercontent\.com/[A-Za-z0-9_\-/=?.&%]+$")
DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{6,200}$")
TIMEOUT = httpx.Timeout(20.0)


class GoogleError(Exception):
    """A Google API refused or failed; `message` is meant for the person."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def explain(status: int, body: dict[str, Any], service: str) -> GoogleError:
    """A Google error as something a person can act on."""
    raw = body.get("error")
    error: dict[str, Any] = raw if isinstance(raw, dict) else {}
    text = str(error.get("message", ""))
    reasons = {
        str(d.get("reason", "")) for d in error.get("details", []) or [] if isinstance(d, dict)
    }
    if "SERVICE_DISABLED" in reasons or "has not been used in project" in text:
        return GoogleError(
            f"{service} is not switched on for this site yet. The site owner needs to "
            "enable it in Google Cloud Console.",
            503,
        )
    if status == 401:
        return GoogleError("Google sign-in expired. Choose the video again.", 401)
    if status == 403:
        return GoogleError(
            f"Google did not allow access to {service}. Choose the video again and allow access.",
            403,
        )
    if status == 404:
        return GoogleError("That video is no longer available in your Google account.", 404)
    return GoogleError(f"{service} did not respond as expected ({status}). Try again.", 502)


async def _call(
    method: str, url: str, token: str, service: str, json: dict[str, Any] | None = None
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.request(
            method, url, json=json, headers={"Authorization": f"Bearer {token}"}
        )
    try:
        body: dict[str, Any] = response.json() if response.content else {}
    except ValueError:
        body = {}
    if response.status_code >= 400:
        raise explain(response.status_code, body, service)
    return body


async def create_photos_session(token: str) -> dict[str, Any]:
    """A Photos Picker session for one video; returns its id and picker link."""
    body = await _call(
        "POST",
        f"{PHOTOS_API}/sessions",
        token,
        "Google Photos",
        json={"pickingConfig": {"maxItemCount": "1"}},
    )
    return {
        "session_id": body.get("id"),
        "picker_uri": body.get("pickerUri"),
        "poll_seconds": _seconds(body.get("pollingConfig", {}).get("pollInterval"), 3.0),
    }


async def poll_photos_session(token: str, session_id: str) -> dict[str, Any]:
    """Whether the person has picked yet, and what - videos only."""
    session = await _call("GET", f"{PHOTOS_API}/sessions/{session_id}", token, "Google Photos")
    if not session.get("mediaItemsSet"):
        return {"done": False, "items": []}
    listing = await _call(
        "GET",
        f"{PHOTOS_API}/mediaItems?sessionId={session_id}&pageSize=25",
        token,
        "Google Photos",
    )
    items = []
    for item in listing.get("mediaItems", []) or []:
        media = item.get("mediaFile") or {}
        items.append(
            {
                "id": item.get("id"),
                "is_video": item.get("type") == "VIDEO"
                or str(media.get("mimeType", "")).startswith("video/"),
                "base_url": media.get("baseUrl"),
                "filename": media.get("filename"),
                "mime_type": media.get("mimeType"),
            }
        )
    return {"done": True, "items": items}


def _seconds(value: Any, default: float) -> float:
    """'3.5s' -> 3.5 (Google's Duration JSON)."""
    try:
        return max(1.0, float(str(value).rstrip("s")))
    except (TypeError, ValueError):
        return default
