"""The summary's citation check (ml/pipeline/s11_summarize.py, ADR-0021)."""

from __future__ import annotations

from pipeline.s11_summarize import Line, citations, grounded, lines_from_captions

VALID = {"L1", "L2", "L3"}


def test_citations_accept_the_forms_models_write() -> None:
    assert citations(["L1", "2", "[L3 00:41 S2 Priya]"], VALID) == ["L1", "L2", "L3"]
    assert citations(["L1-L3"], VALID) == ["L1", "L3"]
    assert citations([3, "L2"], VALID) == ["L2", "L3"]


def test_citations_to_lines_that_do_not_exist_are_dropped() -> None:
    assert citations(["L9", "L42"], VALID) == []
    assert citations("L1", VALID) == []  # not a list


def test_uncited_items_are_dropped_and_counted() -> None:
    out = grounded(
        {
            "overview": [
                {"text": "A meeting.", "lines": ["L1"]},
                {"text": "Made up.", "lines": []},
            ],
            "key_points": [{"text": "Point", "lines": ["L7"]}],
            "decisions": [],
            "action_items": [
                {"owner": "S2", "task": "Send the report", "due": "Friday", "lines": ["L2"]},
                {"owner": "S1", "task": "Invented", "due": None, "lines": ["L99"]},
            ],
        },
        VALID,
        {1, 2},
    )
    assert out["overview"] == [{"text": "A meeting.", "lines": ["L1"]}]
    assert out["key_points"] == []
    assert out["action_items"] == [
        {"owner": 2, "task": "Send the report", "due": "Friday", "lines": ["L2"]}
    ]
    assert out["dropped_ungrounded"] == 3


def test_an_owner_who_is_not_a_speaker_becomes_no_owner() -> None:
    out = grounded(
        {
            "overview": [],
            "key_points": [],
            "decisions": [],
            "action_items": [
                {"owner": "S7", "task": "x", "due": "", "lines": ["L1"]},
                {"owner": "Priya", "task": "y", "due": None, "lines": ["L1"]},
            ],
        },
        VALID,
        {1, 2},
    )
    assert [a["owner"] for a in out["action_items"]] == [None, None]
    assert out["action_items"][0]["due"] is None


def test_lines_are_merged_like_the_browser_merges_them() -> None:
    lines = lines_from_captions(
        {
            2: {"segments": [{"text": "second", "start_ms": 500}, {"text": " ", "start_ms": 600}]},
            1: {"segments": [{"text": "first", "start_ms": 100}, {"text": "tie", "start_ms": 500}]},
        }
    )
    assert lines == [
        Line("L1", 1, 100, "first"),
        Line("L2", 1, 500, "tie"),
        Line("L3", 2, 500, "second"),
    ]
