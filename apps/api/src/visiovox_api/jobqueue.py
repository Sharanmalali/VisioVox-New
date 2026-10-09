"""The API's side of the job queue: send work by name, read progress from Redis.

The API used to import the worker packages to do this (`worker_gpu.tasks`,
`worker_cpu.tasks`). That works wherever the whole workspace is installed and
nowhere else - and the production API image installs only the API. There every
upload was accepted, its job row written, and nothing ever ran: the import
failed inside a swallowed `except`, and the progress stream crashed on the
same import every time a browser opened it.

Sending by task name needs nothing from the worker but the name, which is the
contract anyway: the names below are the ones the workers register.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .config import get_settings

REAL_PIPELINE_TASK = "visiovox.run_real_pipeline"
MOCK_PIPELINE_TASK = "visiovox.run_mock_pipeline"
RENDER_EXPORT_TASK = "visiovox.render_export"
TRANSLATE_TASK = "visiovox.translate_captions"
SUMMARY_TASK = "visiovox.summarize"
DUB_TASK = "visiovox.dub_project"

# The single-concurrency queue the GPU worker serves. Renders share it: not for
# the GPU, but because a render beside an extraction contends for the same disk
# and ffmpeg, which is what one-at-a-time exists to prevent.
GPU_QUEUE = "gpu"

# Written by the workers (worker_cpu.tasks.PROGRESS_KEY, used by both).
PROGRESS_KEY = "visiovox:job:{job_id}:progress"


@lru_cache(maxsize=1)
def _producer() -> Any:
    from celery import Celery

    settings = get_settings()
    return Celery(
        "visiovox",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
    )


def enqueue_real(job_id: str) -> None:
    _producer().send_task(REAL_PIPELINE_TASK, args=[job_id], queue=GPU_QUEUE)


def enqueue_mock(job_id: str) -> None:
    # The mock worker serves Celery's default queue.
    _producer().send_task(MOCK_PIPELINE_TASK, args=[job_id])


def enqueue_export(export_id: str) -> None:
    _producer().send_task(RENDER_EXPORT_TASK, args=[export_id], queue=GPU_QUEUE)


def enqueue_dub(dub_id: str) -> None:
    _producer().send_task(DUB_TASK, args=[dub_id], queue=GPU_QUEUE)


def enqueue_summary(summary_id: str) -> None:
    # The GPU queue: the language model shares the card with extraction.
    _producer().send_task(SUMMARY_TASK, args=[summary_id], queue=GPU_QUEUE)


def enqueue_translation(translation_id: str) -> None:
    # The GPU queue: the translation model shares the card with extraction.
    _producer().send_task(TRANSLATE_TASK, args=[translation_id], queue=GPU_QUEUE)


@lru_cache(maxsize=1)
def _redis() -> Any:
    import redis

    return redis.Redis.from_url(get_settings().redis_url)


def read_progress(job_id: str) -> dict[str, Any] | None:
    """The latest progress a worker published, or None if Redis has none."""
    try:
        raw = _redis().get(PROGRESS_KEY.format(job_id=job_id))
    except Exception:
        return None
    if raw is None:
        return None
    decoded: dict[str, Any] = json.loads(raw)
    return decoded
