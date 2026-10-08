"""Conversation insights (ml/pipeline/insights.py).

The fixture is shared with the browser implementation's tests
(apps/web/src/lib/playback/insights.test.ts): both must produce exactly the
hand-computed expected output, so the numbers the benchmark reports are the
numbers users see.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.insights import BACKCHANNEL_MS, MERGE_GAP_MS, Insights, compute, spurts_of

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "packages/contracts/examples/insights.json").read_text()
)


def _fixture_insights() -> Insights:
    given = FIXTURE["input"]
    intervals = {int(k): [tuple(w) for w in v] for k, v in given["speakers"].items()}
    return compute(intervals, given["duration_ms"])


def test_matches_the_shared_fixture_per_speaker() -> None:
    got = _fixture_insights()
    for key, want in FIXTURE["expected"]["speakers"].items():
        stats = got.speakers[int(key)]
        for name, value in want.items():
            assert getattr(stats, name) == pytest.approx(value), (key, name)


def test_matches_the_shared_fixture_overall() -> None:
    got = _fixture_insights()
    want = FIXTURE["expected"]
    assert [list(r) for r in got.overlaps] == want["overlaps"]
    assert [{"kind": e.kind, "at": e.at, "by": e.by, "of": e.of} for e in got.events] == want[
        "events"
    ]
    assert got.overlap_ms == want["overlap_ms"]
    assert got.speech_ms == want["speech_ms"]
    assert got.silence_ms == want["silence_ms"]


def test_words_join_into_spurts_across_short_gaps_only() -> None:
    spurts = spurts_of(1, [(0, 100), (100 + MERGE_GAP_MS, 900), (2000, 2100)])
    assert [(s.start, s.end, s.words) for s in spurts] == [(0, 900, 2), (2000, 2100, 1)]


def test_touching_speech_is_not_overlap() -> None:
    got = compute({1: [(0, 1000)], 2: [(1000, 2000)]}, 2000)
    assert got.overlaps == []
    assert got.events == []


def test_a_short_reply_after_the_floor_is_free_is_a_turn_not_a_backchannel() -> None:
    got = compute({1: [(0, 1000)], 2: [(1200, 1200 + BACKCHANNEL_MS // 2)]}, 3000)
    assert got.events == []
    assert got.speakers[2].turns == 1


def test_silence_alone_has_no_insights() -> None:
    got = compute({1: [], 2: []}, 5000)
    assert got.speech_ms == 0
    assert got.silence_ms == 5000
    assert all(s.share == 0 for s in got.speakers.values())
