"""Clips and burned-in captions in exports (ADR-0022)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import s10_captions, s10_render
from pipeline.s10_captions import Cue, build_ass, cues_from_captions

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

CAPTIONS = {
    1: {"segments": [{"text": "नमस्ते सभी", "start_ms": 500, "end_ms": 1800}]},
    2: {
        "segments": [
            {"text": "Shall we {begin}?", "start_ms": 1500, "end_ms": 3200},
            {"text": "   ", "start_ms": 3300, "end_ms": 3400},
        ]
    },
}


def test_cues_are_merged_in_time_order_and_blank_ones_dropped() -> None:
    cues = cues_from_captions(CAPTIONS)
    assert [(c.speaker, c.start_ms) for c in cues] == [(1, 500), (2, 1500)]


def test_cues_can_be_limited_to_one_speaker() -> None:
    assert [c.speaker for c in cues_from_captions(CAPTIONS, speakers=[2])] == [2]


def test_a_clip_shifts_cues_to_zero_and_trims_them() -> None:
    doc = build_ass(
        [Cue(1, 500, 1800, "a"), Cue(2, 1500, 3200, "b"), Cue(1, 4000, 5000, "after")],
        width=1280,
        height=720,
        clip_start_ms=1000,
        clip_end_ms=3000,
    )
    events = [line for line in doc.splitlines() if line.startswith("Dialogue:")]
    assert events == [
        "Dialogue: 0,0:00:00.00,0:00:00.80,Default,,0,0,0,,a",
        "Dialogue: 0,0:00:00.50,0:00:02.00,Default,,0,0,0,,b",
    ]


def test_several_speakers_are_named_in_their_colour() -> None:
    doc = build_ass(
        cues_from_captions(CAPTIONS), width=1280, height=720, names={1: "Priya", 2: "Arjun"}
    )
    assert "{\\b1\\c&H00F0A64B&}Priya:{\\r} नमस्ते सभी" in doc


def test_transcript_text_cannot_inject_ass_tags() -> None:
    doc = build_ass(cues_from_captions(CAPTIONS, speakers=[2]), width=640, height=360)
    assert "{begin}" not in doc
    assert "Shall we (begin)?" in doc


def test_caption_size_follows_the_frame() -> None:
    small = build_ass([], width=640, height=360)
    large = build_ass([], width=1920, height=1080)
    assert "Noto Sans,19," in small
    assert "Noto Sans,56," in large


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


@needs_ffmpeg
def test_a_captioned_clip_renders_at_the_asked_length(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
         "-t", "4", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(video)],
        check=True,
    )  # fmt: skip
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440",
         "-t", "4", "-c:a", "aac", str(audio)],
        check=True,
    )  # fmt: skip
    ass = tmp_path / "captions.ass"
    ass.write_text(
        build_ass(
            cues_from_captions(CAPTIONS),
            width=640,
            height=360,
            names={1: "Priya", 2: "Arjun"},
            clip_start_ms=1000,
            clip_end_ms=3000,
        ),
        encoding="utf-8",
    )
    out = s10_render.render_mp4(
        video=video,
        audio=audio,
        out=tmp_path / "clip.mp4",
        rendition=None,
        source_height=360,
        start_ms=1000,
        end_ms=3000,
        subtitles=ass,
    )
    assert abs(_probe_duration(out) - 2.0) < 0.15


@needs_ffmpeg
def test_an_audio_clip_renders_at_the_asked_length(tmp_path: Path) -> None:
    audio = tmp_path / "audio.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440",
         "-t", "5", "-c:a", "aac", str(audio)],
        check=True,
    )  # fmt: skip
    out = s10_render.render_audio(
        audio=audio, out=tmp_path / "clip.m4a", start_ms=1500, end_ms=4000
    )
    assert abs(_probe_duration(out) - 2.5) < 0.15


def test_speaker_palette_is_rgb_hex() -> None:
    assert all(len(c) == 6 for c in s10_captions.SPEAKER_RGB)
