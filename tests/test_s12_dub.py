"""Per-speaker dubbing (ml/pipeline/s12_dub.py, ADR-0024)."""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.s12_dub import (
    GAP_MS,
    LATE_MS,
    MAX_TEMPO,
    VOICES,
    Line,
    assemble,
    assign_voices,
    fit_stats,
    plan,
    residual_db,
    room,
    track_gains,
)


def test_every_speaker_gets_a_voice_of_their_own() -> None:
    voices = assign_voices("hi", [2, 1, 3])
    assert [voices[o].name for o in (1, 2, 3)] == ["Rohit", "Divya", "Aman"]


def test_a_language_with_one_voice_varies_pitch_instead() -> None:
    voices = assign_voices("ne", [1, 2])
    assert voices[1].name == voices[2].name == "Amrita"
    assert voices[1].description != voices[2].description
    assert "higher pitch" in voices[2].description


def test_every_dub_language_has_a_voice() -> None:
    assert all(VOICES[lang] for lang in VOICES)


LINES = [
    Line(1, 0, 2000, "a"),
    Line(1, 2500, 4000, "b"),
    Line(2, 1000, 3000, "c"),
]


def test_a_line_that_fits_is_left_as_spoken() -> None:
    p = plan(LINES, [1500, 1000, 1800])
    assert [x.tempo for x in p] == [1.0, 1.0, 1.0]
    assert [x.start_ms for x in p] == [0, 2500, 1000]


def test_a_long_line_is_sped_up_to_fit_before_the_same_speaker_speaks_again() -> None:
    slot = 2500 - GAP_MS
    p = plan(LINES, [round(slot * 1.2), 1000, 1800])[0]
    assert p.tempo == pytest.approx(1.2, abs=0.01)
    assert p.overrun_ms == 0


def test_a_line_too_long_to_fit_overruns_and_says_so() -> None:
    slot = 2500 - GAP_MS
    p = plan(LINES, [slot * 2, 1000, 1800])[0]
    assert p.tempo == MAX_TEMPO
    assert p.overrun_ms > 0
    stats = fit_stats(plan(LINES, [slot * 2, 1000, 1800]))
    # The pushed-back second line has less time left, so it is sped up too.
    assert stats["overrun"] == pytest.approx(2 / 3)
    assert stats["as_is"] == pytest.approx(1 / 3)


def test_an_overrun_delays_the_speaker_s_next_line_instead_of_overlapping_it() -> None:
    slot = 2500 - GAP_MS
    first, second, _ = plan(LINES, [slot * 2, 1000, 1800])
    assert second.start_ms == first.end_ms + GAP_MS
    assert second.lag_ms == second.start_ms - 2500 > LATE_MS
    stats = fit_stats([first, second])
    assert stats["late"] == pytest.approx(1 / 2)
    assert stats["max_lag_ms"] == second.lag_ms


def test_lag_is_made_up_in_a_pause() -> None:
    lines = [Line(1, 0, 1000, "a"), Line(1, 1200, 2000, "b"), Line(1, 9000, 9500, "c")]
    _, b, c = plan(lines, [2000, 2000, 500])
    assert b.lag_ms > 0  # pushed by a
    assert c.lag_ms == 0  # the pause before c absorbs it
    assert c.start_ms == 9000


def test_speakers_may_overlap_each_other_as_they_did() -> None:
    """Speaker 2's line starts inside speaker 1's: that is not an overrun."""
    p = plan(LINES, [1500, 1000, 1800])
    assert all(x.overrun_ms == 0 for x in p)


def test_packaged_tracks_are_rescaled_before_they_are_removed() -> None:
    rng = np.random.default_rng(0)
    a, b, noise = (rng.standard_normal(8000) * s for s in (1.0, 1.0, 0.05))
    mixture = 0.4 * a + 0.7 * b + noise
    # As packaged: each track normalised to its own level.
    tracks = [a * 3.0, b * 0.5]
    gains = track_gains(mixture, tracks)
    assert gains == pytest.approx([0.4 / 3.0, 0.7 / 0.5], rel=0.02)
    left = room(mixture, tracks, gains)
    assert np.std(left) == pytest.approx(0.05, rel=0.1)


def test_residual_db_reads_how_much_of_a_voice_remains() -> None:
    # Whole periods of different frequencies: exactly orthogonal, so the only
    # voice left in a signal is the voice actually put there.
    t = np.arange(8000) / 8000
    voice, other = np.sin(2 * np.pi * 5 * t), np.sin(2 * np.pi * 13 * t)
    mixture = voice + other
    assert residual_db(mixture, voice, mixture) == pytest.approx(0.0, abs=1e-6)
    assert residual_db(other + 0.1 * voice, voice, mixture) == pytest.approx(-20.0, abs=1.0)


def test_lines_are_placed_at_their_speaker_s_level_without_clipping() -> None:
    rate = 1000
    background = np.zeros(5 * rate, dtype=np.float32)
    # Speaker 1's first line runs 0-2 s, speaker 2's 1-2 s: both at 1.5 s.
    placements = plan(LINES, [2000, 1000, 1000])
    audio = {i: np.ones(round(placements[i].end_ms - placements[i].start_ms)) for i in range(3)}
    out = assemble(background, placements, audio, {1: 0.2, 2: 0.4}, rate)
    assert out[500] == pytest.approx(0.2)  # speaker 1 alone
    assert out[1500] == pytest.approx(0.6)  # both
    loud = assemble(background, placements, audio, {1: 0.9, 2: 0.9}, rate)
    assert float(np.max(np.abs(loud))) <= 0.95 + 1e-6


def test_a_reused_voice_is_labelled_with_its_pitch() -> None:
    voices = assign_voices("hi", list(range(1, 9)))
    labels = [voices[o].label for o in range(1, 9)]
    assert labels[:4] == ["Rohit", "Divya", "Aman", "Rani"]
    assert labels[4] == "Rohit (a slightly higher pitch)"
    assert len(set(labels)) == 8, "every speaker reads differently in the panel"
