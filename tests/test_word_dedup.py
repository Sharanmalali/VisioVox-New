"""Cross-stream word deduplication tests.

Two regimes, both measured, must both survive:

* cross-talk - A speaks, B's track leaks A's words; the copy in B must go
  and the copy in A must stay (`test_cross_talk_copy_goes_to_the_talker`);
* genuine overlap - A and B say different words at once; every word stays
  (`test_different_words_at_once_are_all_kept`).
"""

from __future__ import annotations

import numpy as np

from pipeline.word_dedup import deduplicate

RATE = 16_000


def _voice(seconds: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (0.1 * rng.standard_normal(int(seconds * RATE))).astype(np.float32)


def _turns() -> tuple[np.ndarray, np.ndarray]:
    """A speaks 0-2 s, B 2-4 s; each track leaks the other at -3 dB."""
    a = np.concatenate([_voice(2, 1), np.zeros(2 * RATE, np.float32)])
    b = np.concatenate([np.zeros(2 * RATE, np.float32), _voice(2, 2)])
    leak = 10 ** (-3 / 20)
    return np.stack([a + leak * b, b + leak * a]), a + b


def test_cross_talk_copy_goes_to_the_talker() -> None:
    streams, mix = _turns()
    words = [
        [(0.2, 0.6, " hello"), (2.3, 2.7, " yes")],  # A's track: A's word, B leaked
        [(0.21, 0.62, " hello"), (2.31, 2.69, " yes")],  # B's track: A leaked, B's word
    ]
    keep = deduplicate(words, streams, mix)
    assert keep == [[True, False], [False, True]]


def test_different_words_at_once_are_all_kept() -> None:
    a, b = _voice(2, 3), _voice(2, 4)
    words = [[(0.2, 0.6, " left")], [(0.25, 0.65, " right")]]
    assert deduplicate(words, np.stack([a, b]), a + b) == [[True], [True]]


def test_same_word_far_apart_is_not_a_duplicate() -> None:
    streams, mix = _turns()
    words = [[(0.2, 0.6, " okay")], [(3.0, 3.4, " okay")]]
    assert deduplicate(words, streams, mix) == [[True], [True]]


def test_matching_ignores_case_and_punctuation() -> None:
    streams, mix = _turns()
    words = [[(0.2, 0.6, " Hello,")], [(0.22, 0.6, " hello")]]
    assert deduplicate(words, streams, mix) == [[True], [False]]


def test_one_copy_per_stream_in_a_group() -> None:
    # A word said twice in one track is two words, not a duplicate of itself.
    streams, mix = _turns()
    words = [[(0.2, 0.4, " no"), (0.5, 0.7, " no")], []]
    assert deduplicate(words, streams, mix) == [[True, True], []]
