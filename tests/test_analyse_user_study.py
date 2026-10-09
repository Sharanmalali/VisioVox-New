"""The user-study analysis (scripts/analyse_user_study.py).

The rows here are made up to exercise the arithmetic; they are not study data.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "analyse_user_study", ROOT / "scripts" / "analyse_user_study.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["analyse_user_study"] = module
    spec.loader.exec_module(module)
    return module


aus = _load()


def _row(participant: str, task: str, condition: str, seconds: float, correct: int) -> Any:
    return aus.Row(
        participant=participant,
        order="visiovox-first",
        task=task,
        condition=condition,
        recording="A" if condition == "visiovox" else "B",
        seconds=seconds,
        answer="",
        key="",
        correct=correct,
        f1=None,
        sus=75.0,
        preference="visiovox",
    )


def test_token_f1() -> None:
    assert aus.token_f1("we need the budget", "We need the budget.") == 1.0
    assert aus.token_f1("nothing", "the budget") == 0.0
    assert aus.token_f1("the budget now", "the budget") == pytest.approx(0.8)


def test_times_parse() -> None:
    assert aus.parse_time("at 2:37 I think") == 157
    assert aus.parse_time("1:02:03") == 3723
    assert aus.parse_time("157") == 157
    assert aus.parse_time("no idea") is None


def test_scoring_rules() -> None:
    assert aus.score("T1", "we need the budget", "we need the budget", "")[0] == 1
    assert aus.score("T2", "2:40", "2:37", "") == (1, None)
    assert aus.score("T2", "2:50", "2:37", "") == (0, None)
    assert aus.score("T3", "Ben sends it", "Ben sends the sheet", "") == (None, None)
    # A hand score wins over the automatic one.
    assert aus.score("T2", "2:50", "2:37", "1") == (1, None)


def test_pilots_and_incomplete_participants_are_excluded() -> None:
    rows = [
        _row(p, t, c, 30, 1)
        for p in ("P01", "pilot1")
        for t in ("T1", "T2", "T3")
        for c in ("visiovox", "baseline")
    ]
    rows += [_row("P02", t, "visiovox", 30, 1) for t in ("T1", "T2", "T3")]
    kept, excluded = aus.included(rows)
    assert {r.participant for r in kept} == {"P01"}
    assert excluded == ["P02", "pilot1"]


def test_analysis_reports_direction_and_withholds_accuracy_until_scored() -> None:
    rows = []
    for i in range(1, 9):
        p = f"P{i:02d}"
        for t in ("T1", "T2", "T3"):
            rows.append(_row(p, t, "visiovox", 20 + i, 1))
            rows.append(_row(p, t, "baseline", 60 + i, 0 if t == "T1" else 1))
    result = aus.analyse(rows)
    assert result["participants"] == 8
    assert result["time_seconds"]["rank_biserial"] == -1.0  # every participant faster
    assert result["time_seconds"]["p"] < 0.05
    assert result["accuracy"]["visiovox_mean"] == 1.0

    rows[0].correct = None
    assert aus.analyse(rows)["accuracy"] is None


def test_holm() -> None:
    assert aus.holm([0.01, 0.04]) == pytest.approx([0.02, 0.04])
    assert aus.holm([0.04, 0.01]) == pytest.approx([0.04, 0.02])


def test_nothing_to_analyse_is_refused() -> None:
    with pytest.raises(SystemExit):
        aus.analyse([])
