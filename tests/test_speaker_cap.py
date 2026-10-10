"""At most eight speakers per project (real_pipeline.most_heard)."""

from __future__ import annotations

import pytest

pytest.importorskip("worker_gpu.real_pipeline")

from worker_gpu.real_pipeline import MAX_SPEAKERS, most_heard


def test_the_most_heard_people_are_kept_in_their_order() -> None:
    people = list("ABCDEFGHIJKLMN")  # fourteen, as in the debate that crashed
    spoken = [5, 90, 1, 70, 2, 60, 3, 50, 4, 40, 30, 20, 10, 80.0]
    kept = most_heard(people, spoken, MAX_SPEAKERS)
    assert len(kept) == MAX_SPEAKERS == 8
    assert kept == ["B", "D", "F", "H", "J", "K", "L", "N"]


def test_a_small_group_is_untouched() -> None:
    assert most_heard(["A", "B"], [1.0, 2.0], MAX_SPEAKERS) == ["A", "B"]
