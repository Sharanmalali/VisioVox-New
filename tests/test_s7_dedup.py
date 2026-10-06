"""S7: per-track captions with cross-track duplicates removed.

The load-bearing case is `test_leaked_words_leave_the_silent_persons_captions`:
A speaks, B's track carries A's words 3 dB down, and Whisper has written them
into B's captions. They must stay in A's captions and leave B's.
"""

from __future__ import annotations

import numpy as np

from pipeline.s7_transcribe import Segment, Transcript, Word, deduplicate_across

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


def _transcript(*words: Word) -> Transcript:
    seg = Segment(
        text="".join(w.text for w in words).strip(),
        start_ms=words[0].start_ms,
        end_ms=words[-1].end_ms,
        words=words,
        no_speech_prob=0.0,
    )
    return Transcript(segments=[seg], language="en", language_probability=0.99)


def test_leaked_words_leave_the_silent_persons_captions() -> None:
    streams, mix = _turns()
    a = _transcript(Word(" good", 200, 500, 0.9), Word(" morning", 550, 1200, 0.9))
    b = _transcript(
        Word(" good", 210, 520, 0.8),
        Word(" morning", 560, 1190, 0.8),
        Word(" hi", 2300, 2600, 0.9),
    )
    out_a, out_b = deduplicate_across([a, b], streams, mix)
    assert out_a.text == "good morning"
    assert out_b.text == "hi"
    # The surviving segment is re-timed to the words it still holds.
    assert out_b.segments[0].start_ms == 2300


def test_untouched_transcripts_are_returned_as_they_were() -> None:
    streams, mix = _turns()
    a = _transcript(Word(" alpha", 200, 600, 0.9))
    b = _transcript(Word(" beta", 2300, 2700, 0.9))
    out = deduplicate_across([a, b], streams, mix)
    assert out[0].segments == a.segments and out[1].segments == b.segments


def test_segment_emptied_by_dedup_is_removed() -> None:
    streams, mix = _turns()
    a = _transcript(Word(" right", 300, 700, 0.9))
    b = _transcript(Word(" right", 310, 690, 0.9))
    _, out_b = deduplicate_across([a, b], streams, mix)
    assert out_b.segments == []
    assert out_b.to_json()["segments"] == []
