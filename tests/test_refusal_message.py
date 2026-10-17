"""A refused recording says why, to the person who uploaded it.

Found on the live site: a video with no sound was refused at S0 with "This
file has no audio track", but the stage runner wraps everything a stage
raises, so the worker filed it as a crash and the page could only say that
faces were needed. These pin the three links: the pipeline refuses with the
person-facing message, the worker sees through the stage wrapper, and the API
hands a refusal's message - and only a refusal's - to the page.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
import pytest_asyncio
from authstub import sign_in
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from visiovox_api.config import Settings, get_settings
from visiovox_api.db import dispose_engine
from visiovox_api.main import app

if TYPE_CHECKING:
    from worker_gpu.stagerunner import StageRunner


class _WrappingRunner:
    """StageRunner's contract that matters here: a stage's error comes out wrapped."""

    def record_duration(self, ms: int) -> None:
        pass

    @contextmanager
    def stage(self, plan: Any, *, gpu: bool = False) -> Iterator[Any]:
        from worker_gpu.stagerunner import RunReport, StageFailedError

        try:
            yield RunReport()
        except Exception as exc:
            raise StageFailedError(plan.stage) from exc


def _silent_video_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import sandbox

    monkeypatch.setattr(sandbox, "probe", lambda path, image=None: {})
    monkeypatch.setattr(
        sandbox,
        "facts_from",
        lambda parsed: sandbox.MediaFacts(
            duration_seconds=120.0,
            has_audio=False,
            has_video=True,
            width=704,
            height=576,
            stream_count=1,
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
        ),
    )


def test_a_silent_video_is_refused_with_its_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from worker_gpu.real_pipeline import run_pipeline
    from worker_gpu.stagerunner import StageFailedError
    from worker_gpu.tasks import _refusal

    _silent_video_facts(monkeypatch)
    source = tmp_path / "mosaic.mp4"
    source.write_bytes(b"\0" * 64)
    settings = Settings(media_work_dir=str(tmp_path / "work"))

    with pytest.raises(StageFailedError) as raised:
        run_pipeline(
            runner=cast("StageRunner", _WrappingRunner()),
            source=source,
            settings=settings,
            project_id="p",
        )

    refused = _refusal(raised.value)
    assert refused is not None, "a refusal inside a stage was read as a crash"
    assert str(refused) == "This file has no audio track, so there is nothing to isolate."


def test_a_crash_is_not_a_refusal() -> None:
    from worker_gpu.real_pipeline import PipelineError
    from worker_gpu.stagerunner import StageFailedError
    from worker_gpu.tasks import _refusal

    try:
        try:
            raise RuntimeError("CUDA error: /home/someone/models/x.pt")
        except RuntimeError as exc:
            raise StageFailedError("S5_extract") from exc
    except StageFailedError as wrapped:
        assert _refusal(wrapped) is None

    direct = PipelineError("No faces were found in this video.")
    assert _refusal(direct) is direct


# --------------------------------------------------------------------- API --
settings = get_settings()


async def _stack_up() -> bool:
    try:
        engine = create_async_engine(settings.database_url)
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        await engine.dispose()
    except Exception:
        return False
    return True


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    if not await _stack_up():
        pytest.skip("Compose stack not running; run `make dev`")
    await dispose_engine()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        await dispose_engine()


async def _failed_job(client: AsyncClient, headers: dict[str, str], code: str, detail: str) -> str:
    """A project whose job ended with `code`/`detail`, as the worker records it."""
    created = await client.post(
        "/v1/projects", json={"title": "Refused", "rights_attested": True}, headers=headers
    )
    assert created.status_code == 201, created.text
    project_id = str(created.json()["id"])
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into jobs (id, project_id, status, error_code, error_detail) "
                    "values (:id, :p, 'failed', :c, :d)"
                ),
                {"id": f"job_test_{project_id[-12:]}", "p": project_id, "c": code, "d": detail},
            )
    finally:
        await engine.dispose()
    return project_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_carries_a_refusals_message(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = sign_in(monkeypatch)
    reason = "This file has no audio track, so there is nothing to isolate."
    pid = await _failed_job(client, headers, "PIPELINE_REFUSED", reason)
    job = await client.get(f"/v1/projects/{pid}/job", headers=headers)
    assert job.status_code == 200, job.text
    assert job.json()["error_message"] == reason


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_never_carries_a_crashs_detail(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = sign_in(monkeypatch)
    pid = await _failed_job(client, headers, "PIPELINE_ERROR", "Traceback: /srv/media/work/x")
    job = await client.get(f"/v1/projects/{pid}/job", headers=headers)
    assert job.status_code == 200, job.text
    assert job.json()["error_message"] is None
