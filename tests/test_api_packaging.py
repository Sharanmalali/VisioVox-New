"""The API must run on its own: the production image installs nothing else.

Every upload on the first production deploy was accepted and never processed,
because the API imported `worker_gpu.tasks` to enqueue it and the image has no
worker packages. Development never noticed - the workspace installs all of
them into one environment. These tests hold the line from inside that same
environment, by reading what the API imports rather than trying the import.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from visiovox_api import jobqueue, logredact

API_SRC = Path(__file__).resolve().parents[1] / "apps" / "api" / "src" / "visiovox_api"
NOT_IN_THE_IMAGE = ("worker_gpu", "worker_cpu", "pipeline", "ml", "seave")


def test_the_api_imports_no_worker_or_ml_package() -> None:
    offenders = []
    for path in sorted(API_SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            offenders += [
                f"{path.name}:{node.lineno} {n}"
                for n in names
                if n.split(".")[0] in NOT_IN_THE_IMAGE
            ]
    assert not offenders, offenders


def test_the_apis_export_ladder_matches_the_renderers() -> None:
    from pipeline import s10_render
    from visiovox_api import renditions

    for height in [None, 0, 144, 360, 479, 480, 720, 1079, 1080, 2160]:
        ours = [(r.name, r.height) for r in renditions.available_renditions(height)]
        theirs = [(r.name, r.height) for r in s10_render.available_renditions(height)]
        assert ours == theirs, height


def test_task_names_match_what_the_workers_register() -> None:
    from worker_cpu import tasks as cpu
    from worker_gpu import tasks as gpu

    assert gpu.run_real_pipeline.name == jobqueue.REAL_PIPELINE_TASK
    assert gpu.render_export.name == jobqueue.RENDER_EXPORT_TASK
    assert cpu.run_mock_pipeline.name == jobqueue.MOCK_PIPELINE_TASK
    assert jobqueue.PROGRESS_KEY == cpu.PROGRESS_KEY


def test_access_tokens_never_reach_the_access_log() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "172.20.0.3:1",
            "GET",
            "/v1/projects/p/events?access_token=eyJhbGci.secret.sig&x=1",
            "1.1",
            200,
        ),
        None,
    )
    logredact.RedactTokens().filter(record)
    line = record.getMessage()
    assert "eyJhbGci" not in line
    assert "access_token=[redacted]&x=1" in line
