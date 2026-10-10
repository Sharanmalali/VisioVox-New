"""Speaker card photos for manifests stored before they existed.

The backfill (scripts/backfill_card_photos.py) puts `spk_<n>_face.jpg` beside
an older project's media without rewriting its stored manifest. When a speaker
has no `thumbnail_url`, the conventional object is attached here if it exists
in storage, so the card shows a photo rather than a broken image. Existence is
cached in-process: photos are written once and never change.
"""

from __future__ import annotations

from typing import Any

from .config import Settings
from .storage import ObjectStore

_EXISTS: dict[str, bool] = {}


async def attach_card_photos(
    manifest: dict[str, Any], settings: Settings, store: ObjectStore
) -> dict[str, Any]:
    project_id = manifest.get("project_id")
    speakers = manifest.get("speakers")
    if not isinstance(project_id, str) or not isinstance(speakers, list):
        return manifest
    base = settings.public_media_base_url.rstrip("/")
    out = []
    for speaker in speakers:
        if (
            isinstance(speaker, dict)
            and "thumbnail_url" not in speaker
            and isinstance(speaker.get("ordinal"), int)
        ):
            key = f"projects/{project_id}/spk_{speaker['ordinal']}_face.jpg"
            if key not in _EXISTS or not _EXISTS[key]:
                _EXISTS[key] = await store.exists(key)
            if _EXISTS[key]:
                speaker = {**speaker, "thumbnail_url": f"{base}/{key}"}
        out.append(speaker)
    return {**manifest, "speakers": out}
