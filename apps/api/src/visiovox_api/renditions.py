"""Which export qualities a project can offer - the API's copy of the ladder.

The renderer's own ladder is `pipeline.s10_render`, in the ML package, which
the API image does not install (and should not: it is the worker's). The API
only needs names and heights to list and validate options, so it keeps this
copy; `tests/test_api_packaging.py` fails if the two ever disagree.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rendition:
    name: str
    height: int


LADDER: tuple[Rendition, ...] = (
    Rendition("1080p", 1080),
    Rendition("720p", 720),
    Rendition("480p", 480),
)


def available_renditions(source_height: int | None) -> list[Rendition]:
    """The rungs this source can honestly offer; see pipeline.s10_render."""
    if source_height is None or source_height <= 0:
        return []
    usable = [r for r in LADDER if r.height <= source_height]
    if usable:
        return usable
    return [Rendition(name=f"{source_height}p", height=source_height)]
