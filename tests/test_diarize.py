"""Face-anchored diarization (ml/pipeline/diarize.py): labels to people."""

from __future__ import annotations

from pipeline.diarize import anchor


def test_each_label_goes_to_the_person_whose_speech_it_overlaps() -> None:
    segments = [(0, 2000, "A"), (2500, 4000, "B"), (4200, 5000, "A")]
    speech = [
        [(2600, 3000), (3100, 3900)],  # person 0 spoke when B did
        [(100, 900), (1000, 1800)],  # person 1 spoke when A did
    ]
    got = anchor(segments, speech, 5000)
    assert got == [[(2500, 4000)], [(0, 2000), (4200, 5000)]]


def test_a_label_nobody_was_heard_in_is_left_out() -> None:
    got = anchor([(0, 1000, "A"), (5000, 6000, "ghost")], [[(100, 900)]], 7000)
    assert got == [[(0, 1000)]]


def test_a_label_goes_whole_to_one_person() -> None:
    """Splitting a label between people would invent turn-taking that the
    diarizer never claimed."""
    segments = [(0, 1000, "A"), (2000, 3000, "A")]
    speech = [[(0, 900)], [(2000, 2100)]]
    got = anchor(segments, speech, 3000)
    assert got == [[(0, 1000), (2000, 3000)], []]


def test_ties_go_to_the_lower_index() -> None:
    got = anchor([(0, 1000, "A")], [[(0, 500)], [(500, 1000)]], 1000)
    assert got == [[(0, 1000)], []]


def test_no_people_means_nothing_placed() -> None:
    assert anchor([(0, 1000, "A")], [], 1000) == []


def test_two_labels_cannot_both_go_to_one_person() -> None:
    """Leaked words under a silent face must not pull a second label onto the
    talkative person - pairing is one to one."""
    segments = [(0, 1000, "A"), (2000, 3000, "B")]
    speech = [[(0, 1000), (2000, 2600)], [(2500, 3000)]]
    got = anchor(segments, speech, 3000)
    assert got == [[(0, 1000)], [(2000, 3000)]]
