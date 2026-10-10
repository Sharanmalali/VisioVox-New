"""How long processing takes on this machine, learned from its own jobs.

Each pipeline stage's time is modelled as `fixed + per_second x media
seconds`, fitted by least squares to the recent jobs that finished, stage by
stage: separation grows with length, loading a model does not. During a job
the remaining time is the stages still to run plus what is left of the one
running, so it is measured from where the job actually is - a page reload or
a slow stage does not throw it off - and it is computed by the server, which
knows when each stage really started.
"""

from __future__ import annotations

import datetime as dt
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Job, JobStage, Project

RECENT_JOBS = 200
CACHE_SECONDS = 600
MIN_POINTS = 5
# Before any history exists: the median measured in the paper (2.6x realtime).
FALLBACK_PER_SECOND = 2.6


@dataclass(frozen=True)
class StageModel:
    fixed: float
    per_second: float

    def predict(self, media_s: float) -> float:
        return self.fixed + self.per_second * media_s


@dataclass
class Model:
    stages: dict[str, StageModel] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    jobs: int = 0

    @property
    def fixed(self) -> float:
        return sum(m.fixed for m in self.stages.values())

    @property
    def per_second(self) -> float:
        if not self.stages:
            return FALLBACK_PER_SECOND
        return sum(m.per_second for m in self.stages.values())

    def total(self, media_s: float) -> float:
        return self.fixed + self.per_second * media_s


def fit(points: Sequence[tuple[float, float]]) -> StageModel:
    """`seconds = fixed + per_second x media` with both terms kept non-negative.

    Plain least squares in closed form: the API image carries no numpy (a
    deploy that imported it here crash-looped the API).
    """
    x = [p[0] for p in points]
    y = [p[1] for p in points]
    if len(points) < MIN_POINTS or max(x) == min(x):
        return StageModel(0.0, statistics.median(b / max(a, 1.0) for a, b in points))
    n = len(points)
    sx, sy = sum(x), sum(y)
    sxx = sum(v * v for v in x)
    sxy = sum(a * b for a, b in points)
    per_second = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    fixed = (sy - per_second * sx) / n
    if per_second < 0:
        return StageModel(statistics.median(y), 0.0)
    if fixed < 0:
        return StageModel(0.0, sxy / sxx)
    return StageModel(fixed, per_second)


def build(rows: Sequence[tuple[str, int, str, float, float]]) -> Model:
    """From (job id, stage ordinal, stage name, media seconds, stage seconds) rows."""
    by_stage: dict[str, list[tuple[float, float]]] = {}
    ordinals: dict[str, int] = {}
    jobs: set[str] = set()
    for job_id, ordinal, stage, media_s, seconds in rows:
        by_stage.setdefault(stage, []).append((media_s, seconds))
        ordinals[stage] = min(ordinal, ordinals.get(stage, ordinal))
        jobs.add(job_id)
    model = Model(jobs=len(jobs))
    for stage, points in by_stage.items():
        model.stages[stage] = fit(points)
    model.order = sorted(model.stages, key=lambda s: ordinals[s])
    return model


_CACHE: dict[str, tuple[float, Model]] = {}


async def load(session: AsyncSession) -> Model:
    """The current model, refitted at most every CACHE_SECONDS."""
    cached = _CACHE.get("model")
    if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    recent = (
        select(Job.id)
        .where(Job.status.in_(("succeeded", "partial")), Job.pipeline_mode == "real")
        .order_by(Job.finished_at.desc())
        .limit(RECENT_JOBS)
        .subquery()
    )
    result = await session.execute(
        select(
            JobStage.job_id,
            JobStage.ordinal,
            JobStage.stage,
            Project.duration_ms,
            JobStage.started_at,
            JobStage.finished_at,
        )
        .join(Job, Job.id == JobStage.job_id)
        .join(Project, Project.id == Job.project_id)
        .where(
            JobStage.job_id.in_(select(recent.c.id)),
            JobStage.started_at.is_not(None),
            JobStage.finished_at.is_not(None),
            Project.duration_ms.is_not(None),
        )
    )
    rows = [
        (job_id, ordinal, stage, duration_ms / 1000, (end - start).total_seconds())
        for job_id, ordinal, stage, duration_ms, start, end in result.all()
        if duration_ms and duration_ms > 0
    ]
    model = build(rows)
    _CACHE["model"] = (time.monotonic(), model)
    return model


def remaining(
    model: Model,
    media_s: float,
    stages: Sequence[JobStage],
    now: dt.datetime | None = None,
) -> float:
    """Seconds left for a running job, from where it actually is."""
    now = now or dt.datetime.now(dt.UTC)
    by_name = {s.stage: s for s in stages}
    left = 0.0
    for name in model.order:
        predicted = model.stages[name].predict(media_s)
        stage = by_name.get(name)
        if stage is None or stage.started_at is None:
            left += predicted
        elif stage.finished_at is None:
            spent = (now - stage.started_at).total_seconds()
            # Running long: it is nearly done or much slower than usual; either
            # way a quarter of what it has taken is a fair remaining guess.
            left += predicted - spent if spent < predicted * 0.9 else max(5.0, spent * 0.25)
    return left
