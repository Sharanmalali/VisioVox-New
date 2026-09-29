"""Turning AV-MossFormer2's per-face-track output into per-person tracks.

The model itself runs in its own interpreter and needs a GPU; what is tested
here is everything the worker does with its output - which is also where a
mistake would put one person's words in another's mouth, or out of sync with
their lips.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from worker_gpu import av_tse
from worker_gpu.av_tse import FRAME_SAMPLES, SAMPLE_RATE, FaceTrack
from worker_gpu.real_pipeline import overlap_ratio

rng = np.random.default_rng(7)


def _speech(seconds: float) -> np.ndarray:
    return (rng.standard_normal(int(seconds * SAMPLE_RATE)) * 0.1).astype(np.float32)


def _track(
    index: int, start: int, end: int, n: int, x: float = 0.5, level: float = 0.1
) -> FaceTrack:
    audio = np.zeros(n, dtype=np.float32)
    audio[start:end] = rng.standard_normal(end - start).astype(np.float32) * level
    return FaceTrack(index=index, start=start, end=end, center_x=x, audio=audio)


# ---------------------------------------------------------------- placement --


def test_container_lag_is_measured_not_assumed() -> None:
    """The 69 ms AAC delay seen in practice, recovered from the audio alone."""
    reference = _speech(5)
    delayed = np.concatenate([np.zeros(1105, np.float32), reference])[: len(reference)]
    assert av_tse.container_lag(reference, delayed) == 1105


def test_container_lag_of_aligned_audio_is_zero() -> None:
    reference = _speech(3)
    assert av_tse.container_lag(reference, reference.copy()) == 0


def test_a_late_face_track_lands_where_the_face_appeared() -> None:
    """A face first seen at frame 88 (3.52 s) must not be heard from 0 s."""
    n = 6 * SAMPLE_RATE
    reference = _speech(6)
    lag = 1105
    runner_audio = np.concatenate([np.zeros(lag, np.float32), reference])
    start_frame, end_frame = 88, 150
    est = runner_audio[start_frame * FRAME_SAMPLES : end_frame * FRAME_SAMPLES].copy()

    files = {Path("/r/audio.wav"): runner_audio, Path("/r/est_0.wav"): est}
    description = {
        "audio_wav": "/r/audio.wav",
        "tracks": [
            {
                "index": 0,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "center_x": 0.3,
                "wav": "/r/est_0.wav",
            }
        ],
    }
    (track,), found_lag = av_tse.place_tracks(description, reference, files.__getitem__)

    assert found_lag == lag
    assert len(track.audio) == n
    assert track.start == start_frame * FRAME_SAMPLES - lag
    assert not track.audio[: track.start].any()
    # Placed audio is the reference itself, sample for sample.
    np.testing.assert_allclose(
        track.audio[track.start : track.end], reference[track.start : track.end]
    )


# ------------------------------------------------------------ silent faces --


def test_a_face_that_never_speaks_is_silent() -> None:
    n = 4 * SAMPLE_RATE
    reference = _speech(4)
    assert av_tse.is_silent(_track(0, 0, n, n, level=0.0001), reference)
    assert not av_tse.is_silent(_track(1, 0, n, n, level=0.05), reference)


def test_activity_follows_the_speech() -> None:
    n = 4 * SAMPLE_RATE
    active = av_tse.active_frames(_track(0, SAMPLE_RATE, 2 * SAMPLE_RATE, n).audio)
    assert active[25:50].all()
    assert not active[:25].any() and not active[50:].any()


# --------------------------------------------------------- people, not tracks --


def _embed_by_level(sample: np.ndarray) -> np.ndarray:
    """A stand-in voice embedder: louder tracks 'sound' like one person."""
    loud = float(np.sqrt(np.mean(sample**2))) > 0.05
    return np.array([1.0, 0.0]) if loud else np.array([0.0, 1.0])


def test_one_person_across_a_camera_cut_is_one_speaker() -> None:
    n = 10 * SAMPLE_RATE
    before = _track(0, 0, 4 * SAMPLE_RATE, n, x=0.3, level=0.1)
    after = _track(1, 5 * SAMPLE_RATE, n, n, x=0.35, level=0.1)
    people = av_tse.group_speakers([before, after], _embed_by_level)
    assert len(people) == 1
    assert {t.index for t in people[0].tracks} == {0, 1}


def test_different_voices_stay_different_people() -> None:
    n = 10 * SAMPLE_RATE
    a = _track(0, 0, 4 * SAMPLE_RATE, n, level=0.1)
    b = _track(1, 5 * SAMPLE_RATE, n, n, level=0.02)
    assert len(av_tse.group_speakers([a, b], _embed_by_level)) == 2


def test_two_faces_on_screen_together_are_never_merged() -> None:
    """Even with identical voices: one face cannot be in two places at once."""
    n = 6 * SAMPLE_RATE
    a = _track(0, 0, n, n, x=0.2)
    b = _track(1, 0, n, n, x=0.8)
    assert len(av_tse.group_speakers([a, b], lambda s: np.array([1.0, 0.0]))) == 2


def test_without_an_embedder_nothing_is_merged() -> None:
    n = 10 * SAMPLE_RATE
    a = _track(0, 0, 4 * SAMPLE_RATE, n)
    b = _track(1, 5 * SAMPLE_RATE, n, n)
    assert len(av_tse.group_speakers([a, b], None)) == 2


def test_speakers_are_numbered_left_to_right() -> None:
    n = 6 * SAMPLE_RATE
    right = _track(0, 0, n, n, x=0.8)
    left = _track(1, 0, n, n, x=0.2)
    people = av_tse.group_speakers([right, left], None)
    assert [p.tracks[0].index for p in people] == [1, 0]


def test_coverage_is_the_share_of_the_recording_the_face_was_seen() -> None:
    n = 10 * SAMPLE_RATE
    person = av_tse.Speaker(tracks=[_track(0, 0, 4 * SAMPLE_RATE, n)])
    assert person.coverage(n) == 0.4


# ------------------------------------------------------------------- overlap --


def test_overlap_is_measured_on_the_separated_tracks() -> None:
    n = 10 * SAMPLE_RATE
    a = _track(0, 0, 6 * SAMPLE_RATE, n).audio  # speaks 0-6 s
    b = _track(1, 4 * SAMPLE_RATE, n, n).audio  # speaks 4-10 s
    # Overlapped 4-6 s out of 10 s of speech.
    assert abs(overlap_ratio([a, b]) - 0.2) < 0.02
