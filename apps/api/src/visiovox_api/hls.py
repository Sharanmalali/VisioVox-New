"""Streaming playlists through presigned storage (docs/12 §4).

An HLS playlist names its children by relative path, and the bucket has no
anonymous access (ADR-0012): a presigned master playlist would load, then
every rendition and segment it names would 403, because those URLs carry no
signature. So playlists are served here, rewritten on each request:

  * a child playlist becomes another link to this endpoint, signed with an
    HMAC and an expiry - no bearer token, so hls.js and Safari's native
    player can fetch it as they fetch anything else;
  * a segment or caption file becomes a presigned storage URL.

Segments - the bytes - never pass through the API; only the few-kilobyte
playlists do.
"""

from __future__ import annotations

import hashlib
import hmac
import posixpath
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .config import Settings

PLAYLIST_KEY = re.compile(r"^projects/[A-Za-z0-9_]+/[A-Za-z0-9_./-]+\.m3u8$")
_URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"')


def _secret(settings: Settings) -> bytes:
    # Derived from a secret the server already holds and no client ever sees,
    # so there is no new setting to forget in production.
    raw = settings.s3_secret_access_key.get_secret_value().encode()
    return hashlib.sha256(b"visiovox-hls-playlist:" + raw).digest()


def signature(settings: Settings, key: str, expires: int) -> str:
    message = f"{key}\n{expires}".encode()
    return hmac.new(_secret(settings), message, hashlib.sha256).hexdigest()[:32]


def playlist_url(settings: Settings, key: str, now: float | None = None) -> str:
    """A signed, expiring link to playlist `key` through this API."""
    expires = int(now if now is not None else time.time()) + settings.signed_url_ttl_seconds
    base = settings.public_api_url.rstrip("/")
    return f"{base}/v1/hls/{key}?exp={expires}&sig={signature(settings, key, expires)}"


def verify(settings: Settings, key: str, expires: int, sig: str, now: float | None = None) -> bool:
    if expires < int(now if now is not None else time.time()):
        return False
    return hmac.compare_digest(signature(settings, key, expires), sig)


def resolve(playlist_key: str, uri: str) -> str | None:
    """The storage key a playlist's relative `uri` points at, or None if it
    leaves the project (absolute URLs, `..` past the project root)."""
    if "://" in uri or uri.startswith("/"):
        return None
    target = posixpath.normpath(posixpath.join(posixpath.dirname(playlist_key), uri))
    project_root = "/".join(playlist_key.split("/")[:2]) + "/"
    return target if target.startswith(project_root) else None


async def rewrite(
    text: str,
    playlist_key: str,
    settings: Settings,
    presign: Callable[[str], Awaitable[str]],
    now: float | None = None,
) -> str:
    """`text` with every child playlist signed here and every file presigned."""

    async def link(uri: str) -> str:
        target = resolve(playlist_key, uri)
        if target is None:
            return uri
        if target.endswith(".m3u8"):
            return playlist_url(settings, target, now)
        return await presign(target)

    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append(line)
        elif stripped.startswith("#"):
            rebuilt, last = [], 0
            for m in _URI_ATTRIBUTE.finditer(line):
                rebuilt.append(line[last : m.start(1)])
                rebuilt.append(await link(m.group(1)))
                last = m.end(1)
            rebuilt.append(line[last:])
            out.append("".join(rebuilt))
        else:
            out.append(await link(stripped))
    return "\n".join(out) + "\n"


def normalise_playback(manifest: dict[str, Any]) -> dict[str, Any]:
    """Make a stored manifest playable as served.

    Projects packaged before 2026-10-10 wrote their master playlist under a
    key the player never read (`hls.master`) and without the picture, so they
    told the player to stream and gave it nothing to stream. They play through
    Web Audio instead - they were short, having been sent
    to HLS for their speaker count alone (the one in production was 1.1
    minutes). A long one would also play, at the memory cost Web Audio has.
    """
    out = dict(manifest)
    out.pop("hls", None)
    if out.get("playback_hint") == "hls" and not out.get("master_playlist"):
        out["playback_hint"] = "webaudio"
    return out
