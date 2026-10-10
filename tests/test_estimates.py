"""Processing-time estimates learned from finished jobs (apps/api estimates.py)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any

import pytest

from visiovox_api.estimates import Model, StageModel, build, fit, remaining

T0 = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.UTC)


def test_fixed_cost_and_rate_are_recovered() -> None:
    points = [(m, 30 + 2 * m) for m in (10.0, 30.0, 60.0, 120.0, 180.0, 240.0)]
    model = fit(points)
    assert model.fixed == pytest.approx(30, abs=0.01)
    assert model.per_second == pytest.approx(2, abs=0.001)


def test_too_little_history_falls_back_to_a_rate() -> None:
    model = fit([(60.0, 120.0), (30.0, 60.0)])
    assert (model.fixed, model.per_second) == (0.0, 2.0)


def test_neither_term_goes_negative() -> None:
    # Longer clips that happened to run faster must not predict negative time.
    m = fit([(10.0, 50.0), (20.0, 48.0), (40.0, 46.0), (80.0, 44.0), (160.0, 42.0)])
    assert m.fixed >= 0 and m.per_second >= 0


def test_a_model_is_built_stage_by_stage_in_pipeline_order() -> None:
    rows = []
    for i, media in enumerate((10.0, 40.0, 90.0, 150.0, 200.0, 60.0)):
        rows.append((f"job{i}", 2, "S5_extract", media, 20 + 2 * media))
        rows.append((f"job{i}", 1, "S0_ingest", media, 2 + 0.1 * media))
    model = build(rows)
    assert model.order == ["S0_ingest", "S5_extract"]
    assert model.jobs == 6
    assert model.total(100) == pytest.approx(2 + 10 + 20 + 200, rel=0.01)


def _at(seconds: float | None) -> dt.datetime | None:
    return None if seconds is None else T0 + dt.timedelta(seconds=seconds)


def _stage(name: str, start: float | None, end: float | None) -> Any:
    return SimpleNamespace(stage=name, started_at=_at(start), finished_at=_at(end))


MODEL = Model(
    stages={"S0": StageModel(5, 0.0), "S5": StageModel(20, 2.0), "S7": StageModel(0, 0.5)},
    order=["S0", "S5", "S7"],
    jobs=10,
)


def test_remaining_counts_from_where_the_job_is() -> None:
    media = 60.0  # S0 5 s, S5 140 s, S7 30 s
    now = T0 + dt.timedelta(seconds=45)
    stages = [_stage("S0", 0, 5), _stage("S5", 5, None)]  # 40 s into S5
    assert remaining(MODEL, media, stages, now) == pytest.approx(100 + 30)
    assert remaining(MODEL, media, [], now) == pytest.approx(175), "queued: everything"


def test_a_stage_running_long_still_has_a_little_left() -> None:
    now = T0 + dt.timedelta(seconds=405)
    stages = [_stage("S0", 0, 5), _stage("S5", 5, None)]  # 400 s into a 140 s stage
    left = remaining(MODEL, 60.0, stages, now)
    assert left == pytest.approx(100 + 30)  # a quarter of 400, plus S7
