"""S3 grouping with face identity.

The load-bearing case is `test_matching_faces_join_without_a_voice_match`: the
extracted voices do not match (as on AMI, where every track carries the room),
but the face crops do, so the two tracks must be one person.
"""

from __future__ import annotations

import numpy as np

from worker_gpu.av_tse import FaceTrack, group_speakers

N = 16_000 * 10


def _track(index: int, start_s: float, end_s: float, x: float = 0.5) -> FaceTrack:
    audio = np.zeros(N, dtype=np.float32)
    a, b = int(start_s * 16_000), int(end_s * 16_000)
    audio[a:b] = 0.1 * np.random.default_rng(index).standard_normal(b - a).astype(np.float32)
    return FaceTrack(index=index, start=a, end=b, center_x=x, audio=audio)


def _unit(*v: float) -> np.ndarray:
    a = np.asarray(v, dtype=np.float64)
    return a / np.linalg.norm(a)


ALICE, BOB = _unit(1, 0, 0), _unit(0, 1, 0)
ALICE_LATER = _unit(0.9, 0.3, 0)  # cosine ~0.95 with ALICE


def _no_voice_match(sample: np.ndarray) -> np.ndarray:
    # Every track gets an unrelated random voice print.
    return np.random.default_rng(len(sample)).standard_normal(192)


def test_matching_faces_join_without_a_voice_match() -> None:
    tracks = [_track(0, 0, 4), _track(1, 5, 9)]
    people = group_speakers(tracks, _no_voice_match, faces={0: ALICE, 1: ALICE_LATER})
    assert len(people) == 1


def test_overlapping_tracks_are_never_merged_even_with_matching_faces() -> None:
    tracks = [_track(0, 0, 6), _track(1, 5, 9)]
    people = group_speakers(tracks, None, faces={0: ALICE, 1: ALICE})
    assert len(people) == 2


def test_different_faces_stay_apart() -> None:
    tracks = [_track(0, 0, 4), _track(1, 5, 9)]
    people = group_speakers(tracks, None, faces={0: ALICE, 1: BOB})
    assert len(people) == 2


def test_missing_face_falls_back_to_voice() -> None:
    tracks = [_track(0, 0, 4), _track(1, 5, 9)]

    def same_voice(sample: np.ndarray) -> np.ndarray:
        return np.ones(192)

    people = group_speakers(tracks, same_voice, faces={0: ALICE, 1: None})
    assert len(people) == 1


def test_face_veto_overrules_a_voice_match() -> None:
    tracks = [_track(0, 0, 4), _track(1, 5, 9)]

    def same_voice(sample: np.ndarray) -> np.ndarray:
        return np.ones(192)

    people = group_speakers(tracks, same_voice, faces={0: ALICE, 1: BOB}, face_veto=0.2)
    assert len(people) == 2
    # Without the veto the voice match merges them: the face did not match, so
    # the voice rule decides, exactly as before faces existed.
    assert len(group_speakers(tracks, same_voice, faces={0: ALICE, 1: BOB}, face_veto=None)) == 1


def test_without_faces_behaviour_is_unchanged() -> None:
    tracks = [_track(0, 0, 4), _track(1, 5, 9)]
    assert len(group_speakers(tracks, None)) == 2
