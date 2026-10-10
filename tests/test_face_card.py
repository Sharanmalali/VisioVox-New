"""Speaker card photos (ml/pipeline/face_card.py, docs/16 D1a)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline.face_card import HEIGHT, WIDTH, best_box, crop_rect, write_card_photo


def test_the_largest_usable_face_is_chosen() -> None:
    boxes = [
        [1000, 0.5, 0.5, 0.05, 0.08],
        [2000, 0.4, 0.4, 0.12, 0.2],
        [3000, 0.5, 0.5, 0.01, 0.01],
    ]
    assert best_box(boxes) == [2000, 0.4, 0.4, 0.12, 0.2]
    assert best_box([[0, 0.5, 0.5, 0.01, 0.01]]) is None, "too small to recognise"
    assert best_box([]) is None


def test_the_crop_is_16_by_10_and_stays_inside_the_frame() -> None:
    for box in ([0, 0.5, 0.4, 0.1, 0.15], [0, 0.02, 0.03, 0.1, 0.15], [0, 0.98, 0.97, 0.2, 0.3]):
        w, h, x, y = crop_rect(box, 1280, 720)
        assert abs(w / h - WIDTH / HEIGHT) < 0.02
        assert x >= 0 and x + w <= 1280
        assert y >= 0 and y + h <= 720


def test_a_face_bigger_than_the_frame_allows_is_shrunk_to_fit() -> None:
    w, h, _, _ = crop_rect([0, 0.5, 0.5, 0.3, 0.45], 640, 360)
    assert w <= 640 and h <= 360


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg unavailable")
def test_a_photo_is_cut_from_the_video(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
        ],
        check=True,
    )  # fmt: skip
    dest = tmp_path / "spk_1_face.jpg"
    assert write_card_photo(video, [[1500, 0.5, 0.4, 0.08, 0.14]], (640, 360), dest)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(dest)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    assert probe.stdout.strip() == f"{WIDTH},{HEIGHT}"
    assert not write_card_photo(video, [], (640, 360), tmp_path / "none.jpg")
