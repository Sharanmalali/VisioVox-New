"""Names people give the separated speakers.

Stored on the project, keyed by ordinal, and applied to the manifest each
time it is served. The worker's manifest stays exactly what the pipeline
produced; a rename never rewrites an artifact, and clearing a name brings the
original "Speaker N" back.
"""

from __future__ import annotations

from typing import Any

MAX_NAME_LENGTH = 60


def clean_name(raw: str | None) -> str | None:
    """Collapse whitespace; an empty name means "use the default"."""
    if raw is None:
        return None
    name = " ".join(raw.split())
    return name or None


def with_names(manifest: dict[str, Any], names: dict[str, str] | None) -> dict[str, Any]:
    """A copy of the manifest whose speaker labels carry the given names."""
    if not names:
        return manifest
    speakers = manifest.get("speakers")
    if not isinstance(speakers, list):
        return manifest
    named = []
    for speaker in speakers:
        if isinstance(speaker, dict):
            name = names.get(str(speaker.get("ordinal")))
            if name:
                speaker = {**speaker, "label": name}
        named.append(speaker)
    return {**manifest, "speakers": named}


def speaker_ordinals(manifest: dict[str, Any] | None) -> set[int]:
    """The speaker ordinals a manifest actually has."""
    speakers = (manifest or {}).get("speakers")
    if not isinstance(speakers, list):
        return set()
    return {
        s["ordinal"] for s in speakers if isinstance(s, dict) and isinstance(s.get("ordinal"), int)
    }
