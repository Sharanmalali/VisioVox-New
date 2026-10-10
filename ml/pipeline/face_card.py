"""The photo on each speaker's card (docs/16 D1a).

One still per person, cut from the packaged video at the moment their face is
largest on screen - head and shoulders, 16:10 to fill the card. It is a
downscaled region of a frame the project already stores, kept with the
project and deleted with it; the aligned face crops and embeddings used to
separate the voices remain job-only (docs/16 D1).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

WIDTH, HEIGHT = 320, 200
# The crop is this many face-heights tall: the head with room above it and the
# shoulders below, so a face does not fill the card edge to edge...
FACE_HEIGHTS = 2.0
# ...but never wider than this many face-widths...
MAX_FACE_WIDTHS = 2.6
# ...and never reaching another person's face on screen at that moment (a
# split-screen interview gave each card both faces). Faces count as "at that
# moment" within this much of the chosen frame.
NEIGHBOUR_MS = 500
# Faces narrower than this share of the frame are too small to recognise.
MIN_FACE = 0.03


def best_box(boxes: Sequence[Sequence[float]]) -> list[float] | None:
    """The [ms, cx, cy, half_w, half_h] sample with the largest face, or None."""
    usable = [list(b) for b in boxes if len(b) >= 5 and 2 * b[3] >= MIN_FACE]
    if not usable:
        return None
    return max(usable, key=lambda b: b[3] * b[4])


def horizontal_room(
    box: Sequence[float], others: Sequence[Sequence[Sequence[float]]]
) -> tuple[float, float]:
    """(left, right) frame fractions the crop may span without reaching another
    person's face shown within NEIGHBOUR_MS of `box`."""
    ms, cx = box[0], box[1]
    left, right = 0.0, 1.0
    for person in others:
        near = [b for b in person if len(b) >= 5 and abs(b[0] - ms) <= NEIGHBOUR_MS]
        if not near:
            continue
        o = min(near, key=lambda b: abs(b[0] - ms))
        if o[1] > cx:
            right = min(right, o[1] - o[3])
        else:
            left = max(left, o[1] + o[3])
    return left, right


def crop_rect(
    box: Sequence[float], width: int, height: int, room: tuple[float, float] = (0.0, 1.0)
) -> tuple[int, int, int, int]:
    """(w, h, x, y) in pixels: a 16:10 head-and-shoulders crop around the face,
    kept inside the frame (shifted, then shrunk if the frame is too small) and
    inside `room`, the span clear of other faces, centred on this one."""
    _, cx, cy, hw, hh = box
    h = 2 * hh * height * FACE_HEIGHTS
    w = h * WIDTH / HEIGHT
    widest = min(
        2 * hw * width * MAX_FACE_WIDTHS,
        2 * max(0.0, min(cx - room[0], room[1] - cx)) * width if room != (0.0, 1.0) else width,
    )
    # Never narrower than the face itself: better a neighbour's edge than half a face.
    widest = max(widest, 2 * hw * width)
    if w > widest:
        w, h = widest, widest * HEIGHT / WIDTH
    scale = min(1.0, width / w, height / h)
    w, h = w * scale, h * scale
    # A little more room above the face than below it.
    x = cx * width - w / 2
    y = cy * height - h * 0.45
    x = min(max(0.0, x), width - w)
    y = min(max(0.0, y), height - h)
    # Even sizes: the JPEG encoder's chroma subsampling wants them.
    return (int(w) // 2 * 2, int(h) // 2 * 2, int(x), int(y))


def write_card_photo(
    video: Path,
    boxes: Sequence[Sequence[float]],
    size: tuple[int, int],
    dest: Path,
    others: Sequence[Sequence[Sequence[float]]] = (),
) -> bool:
    """Cut the card photo into `dest` (JPEG). False if there is no usable face.

    `others` are the other people's face boxes, kept out of the crop."""
    box = best_box(boxes)
    ffmpeg = shutil.which("ffmpeg")
    if box is None or ffmpeg is None or not video.is_file():
        return False
    w, h, x, y = crop_rect(box, *size, room=horizontal_room(box, others))
    proc = subprocess.run(  # noqa: S603 - fixed argv, shell=False
        [
            ffmpeg,
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{box[0] / 1000:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"crop={w}:{h}:{x}:{y},scale={WIDTH}:{HEIGHT}",
            "-q:v",
            "4",
            str(dest),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0
