"""Signed streaming playlists (hls.py, docs/12 §4).

No bearer token: the HMAC in the query is the authorisation, issued only
inside a manifest the caller was already allowed to read, and it expires with
the manifest's other signed links.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from . import hls
from .config import get_settings
from .storage import ObjectStore, StorageError

router = APIRouter(prefix="/v1/hls", tags=["media"])


@router.get("/{key:path}")
async def playlist(key: str, exp: int, sig: str) -> Response:
    settings = get_settings()
    if not hls.PLAYLIST_KEY.match(key) or ".." in key.split("/"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such playlist")
    if not hls.verify(settings, key, exp, sig):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="this link has expired or is invalid"
        )
    store = ObjectStore(settings)
    try:
        text = await store.get_text(key)
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no such playlist"
        ) from exc
    body = await hls.rewrite(text, key, settings, store.presign_get)
    return Response(
        content=body,
        media_type="application/vnd.apple.mpegurl",
        # Short: the links inside expire with the manifest's.
        headers={"Cache-Control": "private, max-age=60"},
    )
