"""Face positions from S5 to the caption file, for click-a-face listening."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from pipeline.s9_package import SpeakerTrack, package
from pipeline.types import Modality
from worker_gpu.av_tse import FaceTrack, Speaker

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _track(index: int, start: int, boxes: list[list[float]]) -> FaceTrack:
    return FaceTrack(
        index=index,
        start=start,
        end=start + 100,
        center_x=0.5,
        audio=np.zeros(10, dtype=np.float32),
        boxes=boxes,
    )


def test_a_person_s_boxes_are_merged_in_time_order_on_the_video_clock() -> None:
    person = Speaker(
        tracks=[
            _track(1, 500, [[50, 0.6, 0.4, 0.1, 0.18]]),
            _track(0, 0, [[0, 0.5, 0.4, 0.1, 0.18], [6, 0.52, 0.41, 0.1, 0.18]]),
        ]
    )
    # Frame f of the 25 fps video is at f * 40 ms.
    assert person.face_boxes() == [
        [0, 0.5, 0.4, 0.1, 0.18],
        [240, 0.52, 0.41, 0.1, 0.18],
        [2000, 0.6, 0.4, 0.1, 0.18],
    ]


def test_malformed_boxes_are_skipped() -> None:
    person = Speaker(tracks=[_track(0, 0, [[3, 0.5], [6, 0.5, 0.5, 0.1, 0.1]])])
    assert person.face_boxes() == [[240, 0.5, 0.5, 0.1, 0.1]]


@needs_ffmpeg
def test_face_boxes_ride_in_the_caption_json(tmp_path: Path) -> None:
    from pipeline.s7_transcribe import Segment, Transcript, Word

    transcript = Transcript(
        segments=[Segment("hi", 0, 500, (Word("hi", 0, 500, 0.9),), 0.01)],
        language="en",
        language_probability=1.0,
    )
    track = SpeakerTrack(
        speaker_id="face_1",
        ordinal=1,
        label="Speaker 1",
        faithful=np.zeros(16_000, dtype=np.float32),
        modality=Modality.AUDIOVISUAL,
        speaking_ratio=0.5,
        mean_confidence=0.9,
        extraction_ok=True,
        transcript=transcript,
        faces=[[0, 0.5, 0.4, 0.1, 0.18]],
    )
    package("prj_test", [track], tmp_path, has_video=False, signed_until="")
    body = json.loads((tmp_path / "spk_1.json").read_text())
    assert body["faces"] == {"boxes": [[0, 0.5, 0.4, 0.1, 0.18]]}
