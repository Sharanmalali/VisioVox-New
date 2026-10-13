"""GPU worker: the real pipeline behind a Celery task (docs/28 §W8, ADR-0007).

Runs on its own queue with concurrency 1. That is not tuning — the machine has
one A5000, and a second concurrent job halves throughput while pushing VRAM to
the 24 GB ceiling, which is how a job dies with a CUDA OOM two thirds of the way
through somebody's hour-long recording.

The task owns the parts of a job that are not the pipeline's business: fetching
the source from object storage, publishing the artifacts back, metering the GPU
time against the owner's quota, and leaving the job row in a state that tells
the truth about what happened.
"""

from __future__ import annotations

import datetime as dt
import logging
import shutil
from pathlib import Path
from typing import Any

from celery import Celery
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from visiovox_api.config import Settings, get_settings
from visiovox_api.models import (
    Dub,
    Export,
    Job,
    MediaImport,
    Project,
    Summary,
    Translation,
    UsageCounter,
    new_id,
)
from visiovox_api.storage import StorageError

from .real_pipeline import INFORMATIONAL_WARNINGS, PipelineError, run_pipeline
from .stagerunner import StageRunner, publish

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery(
    "visiovox-gpu",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # A worker dying mid-stage must not lose the job; stage rows make the retry
    # resume rather than restart.
    task_acks_late=True,
    # ...and a worker killed outright (a deploy restart) returns the job to the
    # queue instead of acknowledging it: acks_late alone lost a 17-minute job.
    task_reject_on_worker_lost=True,
    # One long job at a time, and no hoarding the queue while it runs.
    worker_prefetch_multiplier=1,
    worker_concurrency=1,
    task_default_queue="gpu",
)

# Celery workers are synchronous, so this uses the sync driver rather than the
# app's async engine. Sharing an async pool across processes is not safe.
_engine = create_engine(settings.database_url.replace("+asyncpg", "+psycopg2"), pool_pre_ping=True)
_Session = sessionmaker(bind=_engine, expire_on_commit=False)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _meter_gpu_seconds(session: Any, user_id: str, seconds: float) -> None:
    """Charge GPU time to the owner.

    This, not rate limiting, is what bounds how much of the card one account can
    consume — a user inside every request limit can still queue fifty hour-long
    videos. Charged after the fact because the true cost is only known then;
    admission uses the running total from previous jobs.
    """
    from sqlalchemy.dialects.postgresql import insert

    if seconds <= 0:
        return
    key = _now().strftime("%Y-%m")
    amount = round(seconds)
    session.execute(
        insert(UsageCounter)
        .values(
            id=new_id("usc"),
            user_id=user_id,
            metric="gpu_seconds",
            period_key=key,
            value=amount,
        )
        .on_conflict_do_update(
            index_elements=[UsageCounter.user_id, UsageCounter.metric, UsageCounter.period_key],
            set_={"value": UsageCounter.value + amount},
        )
    )


def _s3_client(settings: Settings) -> Any:
    """A plain sync S3 client. The async ObjectStore is the API's; this is Celery."""
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        # .get_secret_value(): SecretStr stringifies to '**********', which
        # boto3 will happily sign with and the store then rejects as 403.
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
        ),
    )


def _publish_artifacts(artifact_dir: Path, project: Project, manifest: dict[str, Any]) -> None:
    """Put the packaged files where the player can actually fetch them.

    Into the object store under the project's prefix, because that is what
    PUBLIC_MEDIA_BASE_URL addresses and what the API presigns. A local copy is
    kept alongside it: that is the retention and cleanup path, and it is what a
    re-render reads rather than downloading its own output again.
    """
    destination = Path(settings.media_root) / "projects" / project.id
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(artifact_dir, destination)

    _upload_artifacts(destination, project.id)

    base = f"{settings.public_media_base_url.rstrip('/')}/projects/{project.id}/"
    _rewrite_urls(manifest, base)


def _upload_artifacts(directory: Path, project_id: str, prefix: str = "") -> None:
    """Upload every packaged file to `projects/<id>/<prefix>` in the bucket."""
    client = _s3_client(settings)
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        key = f"projects/{project_id}/{prefix}{path.relative_to(directory).as_posix()}"
        # Content type matters: a browser will not play an .m4a served as
        # application/octet-stream, and the store has no reason to guess.
        content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        client.upload_file(
            str(path),
            settings.s3_bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
    logger.info("published artifacts for %s to the object store", project_id)


_CONTENT_TYPES = {
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".vtt": "text/vtt",
    ".json": "application/json",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
    ".jpg": "image/jpeg",
    ".png": "image/png",
}


def _rewrite_urls(node: Any, base: str) -> None:
    """Point every relative artifact name at the served base URL.

    The packager writes bare filenames so it does not have to know how the files
    will be served. This is the one place that decides.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {
                "url",
                "vtt",
                "json",
                "peaks_url",
                "thumbnail_url",
                "audio_url",
                "master_playlist",
                "hls",
            } and isinstance(value, str):
                if not value.startswith(("http://", "https://")):
                    node[key] = base + value.lstrip("/")
            else:
                _rewrite_urls(value, base)
    elif isinstance(node, list):
        for item in node:
            _rewrite_urls(item, base)


def _fetch_source(settings: Settings, key: str, job_id: str) -> Path:
    """Download the uploaded object into this job's scratch directory.

    Through the S3 API, deliberately. The object store's on-disk layout is its
    own business - MinIO keeps an object as a directory of parts, and a hosted
    store keeps it somewhere this process cannot see at all - so reading it as a
    file path only ever worked by accident.
    """
    if not key:
        raise StorageError("the project has no source key")

    scratch = Path(settings.media_work_dir) / job_id
    scratch.mkdir(parents=True, exist_ok=True)
    # Keep the original suffix: ffprobe and the sandbox both take a hint from it.
    target = scratch / (Path(key).name or "source")

    _s3_client(settings).download_file(settings.s3_bucket, key, str(target))

    if not target.is_file() or target.stat().st_size == 0:
        raise StorageError(f"downloaded source is empty: {key}")
    return target


@celery_app.task(name="visiovox.run_real_pipeline", bind=True, max_retries=2)
def run_real_pipeline(self: Any, job_id: str) -> dict[str, Any]:
    """Execute the real pipeline for one job."""
    with _Session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return {"status": "missing", "job_id": job_id}

        project = session.get(Project, job.project_id)
        if project is None:
            return {"status": "missing_project", "job_id": job_id}

        job.status = "running"
        job.started_at = job.started_at or _now()
        job.pipeline_mode = "real"
        session.commit()
        publish(settings.redis_url, job_id, {"status": "running", "progress": 0, "stage": None})

        runner = StageRunner(session, job, settings.redis_url)
        try:
            source = _fetch_source(settings, project.source_key or "", job_id)
            if project.trim_start_ms is not None or project.trim_end_ms is not None:
                source = _trim(source, project.trim_start_ms or 0, project.trim_end_ms)
        except Exception as exc:
            logger.warning("could not fetch the source object: %s", exc)
            job.status = "failed"
            job.error_code = "SOURCE_MISSING"
            job.error_detail = "the uploaded file could not be found"
            job.finished_at = _now()
            session.commit()
            publish(settings.redis_url, job_id, {"status": "failed", "progress": 0, "stage": None})
            return {"status": "failed", "job_id": job_id, "reason": "source_missing"}

        try:
            output = run_pipeline(
                runner=runner,
                source=source,
                settings=settings,
                project_id=project.id,
                language=project.language,
            )
        except PipelineError as exc:
            # A clean refusal: the recording cannot yield a result, and the
            # message is written to be shown to a person.
            job.status = "failed"
            job.error_code = "PIPELINE_REFUSED"
            job.error_detail = str(exc)[:500]
            job.finished_at = _now()
            project.status = "failed"
            project.warnings = list(runner.report.warnings)
            session.commit()
            publish(
                settings.redis_url,
                job_id,
                {"status": "failed", "progress": job.progress, "stage": None},
            )
            return {"status": "failed", "job_id": job_id, "reason": str(exc)}
        except Exception as exc:
            logger.exception("pipeline crashed for job %s", job_id)
            job.status = "failed"
            job.error_code = "PIPELINE_ERROR"
            # Never surfaced to a user; the correlation id is the bridge.
            job.error_detail = str(exc)[:500]
            job.finished_at = _now()
            project.status = "failed"
            session.commit()
            publish(
                settings.redis_url,
                job_id,
                {"status": "failed", "progress": job.progress, "stage": None},
            )
            raise

        try:
            return _complete_job(session, runner, job, project, output, settings, job_id)
        except Exception as exc:
            # The result was computed but could not be stored. Without this the
            # job stays "running" for ever and the page waits for ever (it did,
            # for a 14-speaker debate that broke a database constraint).
            logger.exception("could not store the result of job %s", job_id)
            session.rollback()
            job = session.get(Job, job_id) or job
            project = session.get(Project, job.project_id) or project
            job.status = "failed"
            job.error_code = "STORE_FAILED"
            job.error_detail = str(exc)[:500]
            job.finished_at = _now()
            project.status = "failed"
            session.commit()
            publish(
                settings.redis_url,
                job_id,
                {"status": "failed", "progress": job.progress, "stage": None},
            )
            return {"status": "failed", "job_id": job_id, "reason": "store_failed"}


def _complete_job(
    session: Any,
    runner: Any,
    job: Job,
    project: Project,
    output: Any,
    settings: Settings,
    job_id: str,
) -> dict[str, Any]:
    """Publish a finished pipeline's artifacts and record the project as ready."""
    _publish_artifacts(output.artifact_dir, project, output.manifest)
    shutil.rmtree(output.artifact_dir.parent, ignore_errors=True)

    project.manifest = output.manifest
    project.speaker_count = output.speaker_count
    project.overlap_ratio = output.overlap_ratio
    project.difficulty = output.difficulty
    project.warnings = output.warnings
    if output.duration_ms:
        project.duration_ms = output.duration_ms
    # The export ladder is derived from the source's height; without it
    # every project offers audio only, video or not.
    video = output.manifest.get("video") if output.manifest else None
    if isinstance(video, dict):
        project.width = video.get("width") or project.width
        project.height = video.get("height") or project.height
    project.status = "ready"

    _meter_gpu_seconds(session, project.user_id, output.gpu_seconds)

    # Invariant 8: some speakers recovered is a partial success, not a
    # failure, and the distinction is visible to the user.
    runner.finish(
        status="partial" if _outcome_degraded(output, settings) else "succeeded",
        stage="S9_package",
    )

    return {
        "status": job.status,
        "job_id": job_id,
        "speakers": output.speaker_count,
        "gpu_seconds": round(output.gpu_seconds, 1),
    }


def _outcome_degraded(output: Any, settings: Settings) -> bool:
    """Did the result actually suffer, or did the pipeline merely note something?

    A face that never spoke being dropped, or one person's face tracks being
    merged across a camera cut, describe the recording rather than a problem
    with the result. Counting them would mark ordinary jobs partial - which
    both cries wolf and camouflages the jobs where something did go wrong.
    """
    speakers = output.manifest.get("speakers", []) if output.manifest else []
    # A speaker the extractor could not produce a track for is the real thing.
    if any(sp.get("extraction_ok") is False for sp in speakers):
        return True
    return any(w not in INFORMATIONAL_WARNINGS for w in output.warnings)


def enqueue(job_id: str) -> None:
    """Hand a job to the GPU queue."""
    run_real_pipeline.apply_async(args=[job_id], queue="gpu")


def queue_depth() -> int:
    """How many jobs are waiting, for the 'you are Nth in line' display.

    Read from the broker rather than the database because the queue is the
    authoritative answer to "when will mine start"; a job row says queued from
    the moment it is created, including while a worker is already running it.
    """
    try:
        import redis

        client = redis.Redis.from_url(settings.celery_broker_url)
        # redis-py's stubs type this as `Awaitable[int] | int` because the same
        # class backs the async client. On the sync client it is always an int.
        depth = client.llen("gpu")
        return depth if isinstance(depth, int) else 0
    except Exception:
        return 0


# --------------------------------------------------------------------------
# exports (docs/28 §W6)
# --------------------------------------------------------------------------


def _speaker_audio_path(project: Project, ordinal: int | None) -> Path:
    """Where the packager left this speaker's isolated track.

    The FAITHFUL track, deliberately (invariant 6): an export is what someone
    keeps and quotes, so it must be what was recovered rather than what a
    restoration model found plausible.
    """
    base = Path(settings.media_root) / "projects" / project.id
    if ordinal is None:
        return base / "mixed.m4a"
    return base / f"spk_{ordinal}_f.m4a"


def _mix_levels(mix: str) -> dict[int, float]:
    """ "0:0,1:100,2:30" -> {0: 0.0, 1: 1.0, 2: 0.3}."""
    out: dict[int, float] = {}
    for part in mix.split(","):
        key, _, value = part.partition(":")
        out[int(key)] = int(value) / 100
    return out


def _render_mix(project: Project, row: Export, work: Path) -> Path:
    """The listener's mix as one audio file: each separated voice (and the
    original mix, ordinal 0) at its level, summed, then limited so a mix with
    several voices turned up cannot clip."""
    import subprocess

    from pipeline import s10_render

    assert row.mix is not None
    inputs: list[tuple[Path, float]] = []
    for ordinal, level in _mix_levels(row.mix).items():
        if level <= 0:
            continue
        path = _speaker_audio_path(project, None if ordinal == 0 else ordinal)
        if not path.is_file():
            raise ValueError(f"track {ordinal} is no longer available")
        inputs.append((path, level))
    if not inputs:
        raise ValueError("every voice in this mix is off")
    out = work / f"{row.id}-mix.m4a"
    argv = ["ffmpeg", "-v", "error", "-y", "-protocol_whitelist", "file"]
    for path, _ in inputs:
        argv += ["-i", str(path)]
    chains = [f"[{i}:a]volume={level:.2f}[a{i}]" for i, (_, level) in enumerate(inputs)]
    joined = "".join(f"[a{i}]" for i in range(len(inputs)))
    graph = ";".join(
        [
            *chains,
            f"{joined}amix=inputs={len(inputs)}:normalize=0:dropout_transition=0,"
            "alimiter=limit=0.95[out]",
        ]
    )
    argv += ["-filter_complex", graph, "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(out)]
    proc = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=1800)  # noqa: S603
    if proc.returncode != 0 or not out.is_file():
        raise s10_render.RenderError(f"mix failed: {proc.stderr.strip()[:300]}")
    return out


def _burned_captions(project: Project, row: Export, work: Path) -> Path | None:
    """An .ass file of this export's captions, or None when it has none.

    The exported speaker's own captions (their audio is the only voice in the
    file), or everyone's - with names - for the original mix. Translated when
    `row.captions` names a language; shifted so a clip starts at zero.
    """
    import json

    from pipeline import s10_captions

    if row.captions is None or project.manifest is None:
        return None
    base = Path(settings.media_root) / "projects" / project.id
    folder = base if row.captions == "original" else base / "translations" / row.captions
    ordinals = [
        sp["ordinal"]
        for sp in project.manifest.get("speakers", [])
        if isinstance(sp, dict) and isinstance(sp.get("ordinal"), int)
    ]
    if row.mix is not None:
        # The voices that are audible in the mix.
        wanted = [o for o, level in _mix_levels(row.mix).items() if o != 0 and level > 0]
    else:
        wanted = ordinals if row.speaker_ordinal is None else [row.speaker_ordinal]
    captions = {
        o: json.loads((folder / f"spk_{o}.json").read_text(encoding="utf-8"))
        for o in wanted
        if (folder / f"spk_{o}.json").is_file()
    }
    names = None
    if len(wanted) > 1:
        names = {o: (project.speaker_names or {}).get(str(o)) or f"Speaker {o}" for o in wanted}
    document = s10_captions.build_ass(
        s10_captions.cues_from_captions(captions),
        width=project.width or 1280,
        height=project.height or 720,
        names=names,
        clip_start_ms=row.start_ms or 0,
        clip_end_ms=row.end_ms,
    )
    path = work / f"{row.id}.ass"
    path.write_text(document, encoding="utf-8")
    return path


@celery_app.task(name="visiovox.render_export", bind=True, max_retries=1)
def render_export(self: Any, export_id: str) -> dict[str, Any]:
    """Render one export - optionally a clip, optionally captioned - and
    publish it to the object store for download."""
    from pipeline import s10_render

    with _Session() as session:
        row = session.get(Export, export_id)
        if row is None:
            return {"status": "missing", "export_id": export_id}

        project = session.get(Project, row.project_id)
        if project is None:
            return {"status": "missing_project", "export_id": export_id}

        row.status = "running"
        session.commit()

        def fail(reason: str) -> dict[str, Any]:
            row.status = "failed"
            row.error_detail = reason[:500]
            row.finished_at = _now()
            session.commit()
            return {"status": "failed", "export_id": export_id, "reason": reason}

        work = Path(settings.media_work_dir)
        work.mkdir(parents=True, exist_ok=True)
        out_dir = Path(settings.media_root) / "exports" / project.id
        out_dir.mkdir(parents=True, exist_ok=True)

        mixed_audio: Path | None = None
        if row.mix is not None:
            try:
                mixed_audio = _render_mix(project, row, work)
            except (s10_render.RenderError, OSError, ValueError) as exc:
                return fail(f"the mix could not be made: {exc}")
            audio = mixed_audio
        else:
            audio = _speaker_audio_path(project, row.speaker_ordinal)
        if not audio.is_file():
            return fail("the isolated track for this speaker is no longer available")

        suffix = "m4a" if row.kind == "audio" else "mp4"
        mixed = f"-mix-{row.mix.replace(':', '_').replace(',', '-')}" if row.mix else ""
        clip = f"-{row.start_ms}-{row.end_ms}" if row.start_ms is not None else ""
        captions = f"-cc-{row.captions}" if row.captions else ""
        name = f"{row.speaker_ordinal or 'all'}-{row.quality or row.kind}{clip}{captions}{mixed}.{suffix}"
        destination = out_dir / name
        subtitles: Path | None = None

        try:
            if row.kind == "audio":
                s10_render.render_audio(
                    audio=audio, out=destination, start_ms=row.start_ms, end_ms=row.end_ms
                )
            else:
                # S9's picture: the normalised video the player shows. (This
                # read "source.mp4", which packaging never writes, so every
                # video export silently came out as audio.)
                source_video = Path(settings.media_root) / "projects" / project.id / "video.mp4"
                if not source_video.is_file():
                    # No video to mux onto: give them the audio rather than
                    # failing, and say so. Invariant 8 in miniature.
                    destination = destination.with_suffix(".m4a")
                    s10_render.render_audio(
                        audio=audio, out=destination, start_ms=row.start_ms, end_ms=row.end_ms
                    )
                    row.kind = "audio"
                else:
                    # Resolved against what this source actually offers rather
                    # than the fixed ladder: a sub-480p source is offered a rung
                    # named for its own height, which is not in LADDER, and
                    # looking it up there would silently yield None.
                    rendition = next(
                        (
                            r
                            for r in s10_render.available_renditions(project.height)
                            if r.name == row.quality
                        ),
                        None,
                    )
                    subtitles = _burned_captions(project, row, work)
                    s10_render.render_mp4(
                        video=source_video,
                        audio=audio,
                        out=destination,
                        rendition=rendition,
                        source_height=project.height,
                        start_ms=row.start_ms,
                        end_ms=row.end_ms,
                        subtitles=subtitles,
                    )
        except (s10_render.RenderError, OSError, ValueError) as exc:
            return fail(str(exc))
        finally:
            if subtitles is not None:
                subtitles.unlink(missing_ok=True)
            if mixed_audio is not None:
                mixed_audio.unlink(missing_ok=True)

        # Into the object store, which is what the download link signs. (The
        # file used to stay on local disk, under a key the store never had.)
        key = f"exports/{project.id}/{destination.name}"
        try:
            _s3_client(settings).upload_file(
                str(destination),
                settings.s3_bucket,
                key,
                ExtraArgs={
                    "ContentType": _CONTENT_TYPES.get(
                        destination.suffix, "application/octet-stream"
                    )
                },
            )
        except Exception as exc:
            logger.exception("export %s could not be published", export_id)
            return fail(f"the file could not be published: {exc}")

        row.storage_key = key
        row.size_bytes = destination.stat().st_size
        row.status = "ready"
        row.finished_at = _now()
        session.commit()

        return {
            "status": "ready",
            "export_id": export_id,
            "bytes": row.size_bytes,
        }


def enqueue_export(export_id: str) -> None:
    """Renders share the GPU queue.

    Not because a render needs the GPU - it does not - but because it competes
    for the same disk and the same ffmpeg, and a render running beside an
    extraction is exactly the contention the single-concurrency queue exists to
    prevent.
    """
    render_export.apply_async(args=[export_id], queue="gpu")


# --------------------------------------------------------------------------
# translated captions (ADR-0020)
# --------------------------------------------------------------------------

_TRANSLATOR: Any = None


def _translator() -> Any:
    """NLLB, loaded once per worker process and kept: it is reused by every
    translation, and loading it costs more than translating a meeting."""
    global _TRANSLATOR
    if _TRANSLATOR is None:
        from pipeline import s8_translate

        _TRANSLATOR = s8_translate.load_translator(
            settings.translation_model_dir, settings.torch_device
        )
    return _TRANSLATOR


def _run_translation(project: Project, language: str) -> str | None:
    """Translate every speaker's captions into `language` and publish them.

    Returns None on success, or why it could not be done.
    """
    import json

    from pipeline import s8_translate
    from visiovox_api.translation_languages import BY_CODE, SOURCE_FLORES

    target = BY_CODE.get(language)
    if target is None:
        return "this language is not offered"
    assert project.manifest is not None
    base = Path(settings.media_root) / "projects" / project.id
    out_dir = base / "translations" / language
    out_dir.mkdir(parents=True, exist_ok=True)
    speakers = [s for s in project.manifest.get("speakers", []) if isinstance(s, dict)]
    client = _s3_client(settings)
    written = 0
    for speaker in speakers:
        ordinal = speaker.get("ordinal")
        source_file = base / f"spk_{ordinal}.json"
        if not source_file.is_file():
            # A speaker without captions has nothing to translate.
            continue
        body = json.loads(source_file.read_text(encoding="utf-8"))
        source = SOURCE_FLORES.get(str(body.get("language")))
        if source is None:
            return f"captions in '{body.get('language')}' cannot be translated by this model"
        try:
            translated = s8_translate.translate_transcript(
                body, source, target.nllb, target.code, _translator()
            )
        except Exception as exc:
            logger.exception("translation of %s into %s failed", project.id, language)
            return f"translation failed: {exc}"
        path = out_dir / f"spk_{ordinal}.json"
        path.write_text(json.dumps(translated, ensure_ascii=False), encoding="utf-8")
        client.upload_file(
            str(path),
            settings.s3_bucket,
            f"projects/{project.id}/translations/{language}/spk_{ordinal}.json",
            ExtraArgs={"ContentType": "application/json; charset=utf-8"},
        )
        written += 1
    if written == 0:
        return "this recording has no captions to translate"
    return None


@celery_app.task(name="visiovox.translate_captions", bind=True, max_retries=0)
def translate_captions(self: Any, translation_id: str) -> dict[str, Any]:
    """Translate every speaker's captions of one project into one language."""
    with _Session() as session:
        row = session.get(Translation, translation_id)
        if row is None:
            return {"status": "missing", "translation_id": translation_id}
        project = session.get(Project, row.project_id)
        if project is None or project.manifest is None:
            row.status = "failed"
            row.error_detail = "the recording is no longer available"
            row.finished_at = _now()
            session.commit()
            return {"status": "failed", "translation_id": translation_id}

        row.status = "running"
        session.commit()

        def fail(reason: str) -> dict[str, Any]:
            row.status = "failed"
            row.error_detail = reason[:500]
            row.finished_at = _now()
            session.commit()
            return {"status": "failed", "translation_id": translation_id, "reason": reason}

        problem = _run_translation(project, row.language)
        if problem is not None:
            return fail(problem)
        row.status = "ready"
        row.finished_at = _now()
        session.commit()
        return {"status": "ready", "translation_id": translation_id}


# --------------------------------------------------------------------------
# summary and action items (ADR-0021)
# --------------------------------------------------------------------------

LLM_HOST = "http://127.0.0.1:11434"
_LLM: Any = None


def _ensure_llm() -> None:
    """Start the local model server if it is not answering.

    Started by the worker rather than installed as its own service: it lives
    and dies with the worker's process group, and the model is only loaded
    while summaries are being written (keep_alive in s11 lets it go after).
    """
    import os
    import subprocess
    import time
    import urllib.request

    global _LLM

    def up() -> bool:
        try:
            with urllib.request.urlopen(f"{LLM_HOST}/api/tags", timeout=2):  # noqa: S310 - fixed loopback URL
                return True
        except OSError:
            return False

    if up():
        return
    home = Path.home()
    env = {
        **os.environ,
        "OLLAMA_HOST": "127.0.0.1:11434",
        "OLLAMA_MODELS": str(home / ".cache" / "ollama" / "models"),
        # Under the worker's ProtectHome=read-only, only ~/.cache is writable.
        "HOME": str(home / ".cache" / "ollama-home"),
    }
    log = (Path(settings.media_work_dir) / "ollama.log").open("ab")
    _LLM = subprocess.Popen(  # noqa: S603 - fixed binary, no shell
        [str(home / ".local" / "ollama" / "bin" / "ollama"), "serve"],
        env=env,
        stdout=log,
        stderr=log,
    )
    for _ in range(60):
        if up():
            return
        time.sleep(0.5)
    raise RuntimeError("the summary model did not start")


@celery_app.task(name="visiovox.summarize", bind=True, max_retries=0)
def summarize(self: Any, summary_id: str) -> dict[str, Any]:
    """Write one project's summary and action items."""
    import json

    from pipeline import s11_summarize

    with _Session() as session:
        row = session.get(Summary, summary_id)
        if row is None:
            return {"status": "missing", "summary_id": summary_id}
        project = session.get(Project, row.project_id)

        def fail(reason: str) -> dict[str, Any]:
            row.status = "failed"
            row.error_detail = reason[:500]
            row.finished_at = _now()
            session.commit()
            return {"status": "failed", "summary_id": summary_id, "reason": reason}

        if project is None or project.manifest is None:
            return fail("the recording is no longer available")
        row.status = "running"
        session.commit()

        base = Path(settings.media_root) / "projects" / project.id
        captions: dict[int, dict[str, Any]] = {}
        for speaker in project.manifest.get("speakers", []):
            ordinal = speaker.get("ordinal") if isinstance(speaker, dict) else None
            path = base / f"spk_{ordinal}.json"
            if isinstance(ordinal, int) and path.is_file():
                captions[ordinal] = json.loads(path.read_text(encoding="utf-8"))
        lines = s11_summarize.lines_from_captions(captions)
        if not lines:
            return fail("this recording has no captions to summarise")
        names = {o: (project.speaker_names or {}).get(str(o)) or f"Speaker {o}" for o in captions}
        try:
            _ensure_llm()
            content = s11_summarize.summarise(lines, names, LLM_HOST)
        except Exception as exc:
            logger.exception("summary %s failed", summary_id)
            return fail(f"the summary could not be written: {exc}")

        row.content = content
        row.status = "ready"
        row.finished_at = _now()
        session.commit()
        return {"status": "ready", "summary_id": summary_id, "lines": len(lines)}


# --------------------------------------------------------------------------
# per-speaker dubbing (ADR-0024)
# --------------------------------------------------------------------------

TTS_RATE = 24_000


def _decode(path: Path, rate: int) -> Any:
    """Any audio file as mono float32 at `rate`."""
    import subprocess

    import numpy as np

    proc = subprocess.run(  # noqa: S603
        [  # noqa: S607 - ffmpeg from PATH, as every renderer here
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=1800,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def _stretch(path: Path, tempo: float, rate: int) -> Any:
    """A synthesised line resampled to `rate`, sped up by `tempo` (pitch kept)."""
    import subprocess

    import numpy as np

    chain = f"atempo={tempo:.3f},aresample={rate}" if tempo > 1.0 else f"aresample={rate}"
    proc = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-af",
            chain,
            "-ac",
            "1",
            "-f",
            "f32le",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def _speech_level(track: Any, rate: int) -> float:
    """A speaker's loudness while speaking: the RMS of their louder frames."""
    import numpy as np

    frame = rate // 50
    n = len(track) // frame
    if n == 0:
        return 0.05
    rms = np.sqrt(
        np.mean(np.square(track[: n * frame].reshape(n, frame), dtype=np.float64), axis=1)
    )
    active = rms[rms > 0.1 * float(rms.max())] if float(rms.max()) > 0 else rms
    return float(np.percentile(active, 75)) if active.size else 0.05


def _synthesise(lines: list[dict[str, Any]], work: Path) -> dict[str, Any]:
    """Run the TTS runner in its own environment; its result file, parsed."""
    import json
    import os
    import subprocess

    python = Path(settings.tts_python).expanduser()
    if not python.is_file():
        raise RuntimeError(f"the dubbing voice model is not installed ({python})")
    request = work / "tts-request.json"
    result = work / "tts-result.json"
    request.write_text(
        json.dumps({"lines": lines, "out_dir": str(work / "tts")}, ensure_ascii=False),
        encoding="utf-8",
    )
    env = {
        k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    }
    # Weights are local; a job must never wait on the hub.
    env["HF_HUB_OFFLINE"] = "1"
    runner = Path(__file__).resolve().parents[2] / "tts" / "run_tts.py"
    log_path = work / "tts.log"
    with log_path.open("wb") as log:
        proc = subprocess.run(  # noqa: S603 - our interpreter and script
            [str(python), str(runner), "--request", str(request), "--result", str(result)],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=settings.tts_timeout_seconds,
            check=False,
        )
    if proc.returncode != 0 or not result.is_file():
        tail = log_path.read_bytes()[-1500:].decode("utf-8", "replace")
        raise RuntimeError(f"voice synthesis failed: {tail.strip()[-400:]}")
    parsed: dict[str, Any] = json.loads(result.read_text(encoding="utf-8"))
    return parsed


def _publish_dub_tracks(
    project_id: str,
    language: str,
    base: Path,
    background: Any,
    soundtrack: Any,
    placements: Any,
    audio: Any,
    levels: Any,
) -> dict[str, Any]:
    """Each dubbed speaker's new voice alone, beside the dub, for the player.

    Written to `dubs/<language>/tracks/`: `spk_<ordinal>.m4a` per speaker and
    `mix.m4a`, the dub's own soundtrack, so the player can offer every person
    and everyone together exactly as it does for the original. A long
    recording is also packaged for streaming, as the original is (s9_hls),
    reusing the original's video playlist when there is one.
    """
    import shutil

    from pipeline import s12_dub
    from pipeline.s9_hls import choose_engine, package_hls
    from pipeline.s9_package import _encode_m4a

    folder = base / "dubs" / language / "tracks"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    voices = s12_dub.speaker_voices(background, placements, audio, levels, TTS_RATE)
    sizes = {str(o): _encode_m4a(v, folder / f"spk_{o}.m4a", TTS_RATE) for o, v in voices.items()}
    sizes["mix"] = _encode_m4a(soundtrack, folder / "mix.m4a", TTS_RATE)

    duration_ms = round(len(soundtrack) * 1000 / TTS_RATE)
    hint = choose_engine(duration_ms, len(voices) + 1)
    if hint == "hls":
        shared = base / "video" / "v.m3u8"
        picture = base / "video.mp4"
        try:
            package_hls(
                folder,
                tracks=[(f"Speaker {o}", folder / f"spk_{o}.m4a") for o in voices]
                + [("Dubbed mix", folder / "mix.m4a")],
                duration_ms=duration_ms,
                # From dubs/<language>/tracks/ up to the project's own picture.
                video_playlist="../../../video/v.m3u8" if shared.is_file() else None,
                video=None if shared.is_file() or not picture.is_file() else picture,
            )
        except Exception:
            # As for the original: Web Audio still plays it, at a memory cost.
            logger.warning("dub tracks for %s: HLS packaging failed", project_id, exc_info=True)
            hint = "webaudio"
    _upload_artifacts(folder, project_id, prefix=f"dubs/{language}/tracks/")
    return {"speakers": sorted(voices), "bytes": sizes, "playback_hint": hint}


@celery_app.task(name="visiovox.dub_project", bind=True, max_retries=0)
def dub_project(self: Any, dub_id: str) -> dict[str, Any]:
    """Dub every speaker of one project into one language, keeping the room."""
    import json
    import shutil
    import subprocess
    import tempfile

    import numpy as np
    import soundfile as sf

    from pipeline import s12_dub

    with _Session() as session:
        row = session.get(Dub, dub_id)
        if row is None:
            return {"status": "missing", "dub_id": dub_id}
        project = session.get(Project, row.project_id)

        def fail(reason: str) -> dict[str, Any]:
            row.status = "failed"
            row.error_detail = reason[:500]
            row.finished_at = _now()
            session.commit()
            return {"status": "failed", "dub_id": dub_id, "reason": reason}

        if project is None or project.manifest is None:
            return fail("the recording is no longer available")
        row.status = "running"
        session.commit()
        language = row.language
        if language not in s12_dub.VOICES:
            return fail("there are no voices for this language")

        # 1. The translation, made now if it does not exist yet.
        translation = session.scalar(
            select(Translation).where(
                Translation.project_id == project.id, Translation.language == language
            )
        )
        if translation is None or translation.status != "ready":
            problem = _run_translation(project, language)
            if problem is not None:
                return fail(problem)
            if translation is None:
                translation = Translation(
                    project_id=project.id, user_id=row.user_id, language=language
                )
                session.add(translation)
            translation.status = "ready"
            translation.error_detail = None
            translation.finished_at = _now()
            session.commit()

        base = Path(settings.media_root) / "projects" / project.id
        folder = base / "translations" / language
        ordinals = [
            sp["ordinal"]
            for sp in project.manifest.get("speakers", [])
            if isinstance(sp, dict) and isinstance(sp.get("ordinal"), int)
        ]
        lines: list[s12_dub.Line] = []
        for o in ordinals:
            path = folder / f"spk_{o}.json"
            if not path.is_file():
                continue
            for seg in json.loads(path.read_text(encoding="utf-8")).get("segments", []):
                text = str(seg.get("text", "")).strip()
                if text:
                    lines.append(s12_dub.Line(o, int(seg["start_ms"]), int(seg["end_ms"]), text))
        if not lines:
            return fail("there is nothing to dub: no translated captions")
        voices = s12_dub.assign_voices(language, sorted({ln.speaker for ln in lines}))

        work_root = Path(settings.media_work_dir)
        work_root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"dub-{row.id}-", dir=work_root))
        try:
            # 2. Every line spoken in its speaker's voice.
            synth = _synthesise(
                [
                    {"id": i, "text": ln.text, "description": voices[ln.speaker].description}
                    for i, ln in enumerate(lines)
                ],
                work,
            )
            by_id = {r["id"]: r for r in synth["lines"]}
            durations = [
                int(by_id[i]["duration_ms"]) if i in by_id else 0 for i in range(len(lines))
            ]
            placements = s12_dub.plan(lines, durations)

            # 3. The room: the mixture without the dubbed voices.
            mixture = _decode(base / "mixed.m4a", TTS_RATE)
            tracks = [_decode(base / f"spk_{o}_f.m4a", TTS_RATE) for o in ordinals]
            gains = s12_dub.track_gains(mixture, tracks)
            dubbed = {ln.speaker for ln in lines}
            keep = [i for i, o in enumerate(ordinals) if o in dubbed]
            background = s12_dub.room(
                mixture, [tracks[i] for i in keep], np.asarray([gains[i] for i in keep])
            )
            levels = {
                o: _speech_level(gains[i] * tracks[i], TTS_RATE)
                for i, o in enumerate(ordinals)
                if o in dubbed
            }

            # 4. The new voices, placed and stretched to fit.
            audio = {
                i: _stretch(Path(by_id[i]["wav"]), p.tempo, TTS_RATE)
                for i, p in enumerate(placements)
                if i in by_id
            }
            soundtrack = s12_dub.assemble(background, placements, audio, levels, TTS_RATE)
            # Each person's dubbed voice alone, from the same run. Never at the
            # cost of the dub itself: without it the dubbed video still stands.
            stems: dict[str, Any] | None = None
            try:
                stems = _publish_dub_tracks(
                    project.id, language, base, background, soundtrack, placements, audio, levels
                )
            except Exception:
                logger.exception("dub %s: per-speaker tracks failed", dub_id)
            wav = work / "dub.wav"
            sf.write(wav, soundtrack, TTS_RATE)

            # 5. Onto the picture.
            out_dir = base / "dubs" / language
            out_dir.mkdir(parents=True, exist_ok=True)
            mp4 = out_dir / "dub.mp4"
            proc = subprocess.run(  # noqa: S603
                [  # noqa: S607
                 "ffmpeg", "-v", "error", "-y", "-i", str(base / "video.mp4"), "-i", str(wav),
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                 "-shortest", "-movflags", "+faststart", str(mp4)],
                check=False,
                capture_output=True,
                text=True,
                timeout=1800,
            )  # fmt: skip
            if proc.returncode != 0 or not mp4.is_file():
                return fail(f"the dubbed video could not be made: {proc.stderr.strip()[:300]}")
            _s3_client(settings).upload_file(
                str(mp4),
                settings.s3_bucket,
                f"projects/{project.id}/dubs/{language}/dub.mp4",
                ExtraArgs={"ContentType": "video/mp4"},
            )
        except Exception as exc:
            logger.exception("dub %s failed", dub_id)
            return fail(str(exc))
        finally:
            shutil.rmtree(work, ignore_errors=True)

        stats: dict[str, Any] = {
            **s12_dub.fit_stats(placements),
            "voices": {str(o): v.label or v.name for o, v in voices.items()},
            "synthesis_s": round(float(synth.get("seconds", 0.0)), 1),
            "speech_s": round(float(synth.get("speech_s", 0.0)), 1),
        }
        if stems is not None:
            stats["tracks"] = stems
        row.stats = stats
        row.status = "ready"
        row.finished_at = _now()
        session.commit()
        return {"status": "ready", "dub_id": dub_id, "lines": len(lines)}


# ------------------------------------------------------------------ imports --


def _trim(source: Path, start_ms: int, end_ms: int | None) -> Path:
    """The chosen part of `source`, cut frame-accurately (re-encoded: a stream
    copy can only cut at keyframes, seconds away from where the person chose)."""
    import subprocess

    out = source.with_name("trimmed.mp4")
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start_ms / 1000:.3f}", "-i", str(source)]
    if end_ms is not None:
        cmd += ["-t", f"{(end_ms - start_ms) / 1000:.3f}"]
    cmd += [
        "-map", "0:v:0?", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=3600)  # noqa: S603
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"trimming failed: {proc.stderr.strip()[:200]}")
    return out


# A link that resolves to something other than these is refused even if the
# API's host check were bypassed (routes_import.py).
_IMPORT_EXTRACTORS = ["youtube", "vimeo"]
# Tried in order. YouTube hands out a different set of streams on each request
# and throttles or refuses (HTTP 403) individual stream links at random - the
# same 48-minute video gave one stream at 16 MB/s, its neighbour at 0.4 MB/s
# and a third a 403 (2026-10-10) - so a refused or crawling attempt moves on
# rather than failing the import or taking twenty minutes. H.264 first so the
# trim preview plays in every browser (Safari has no AV1).
_IMPORT_FORMATS = (
    "bv*[height<=720][vcodec^=avc1]+ba[ext=m4a]/b[height<=720][vcodec^=avc1]",
    "bv*[height<=720]+ba/b[height<=720]",
    "bv*[height<=480]+ba/b[height<=480]",
    "b/bv*+ba",
)
# Kept for callers that want one selector.
_IMPORT_FORMAT = _IMPORT_FORMATS[0]
# A download this slow after this long, with this much left, is a throttled
# link: abandon it and ask again.
_SLOW_BYTES_PER_S = 1_000_000
_SLOW_AFTER_S = 45
_SLOW_IF_LEFT_S = 240


class _ThrottledError(Exception):
    """The current stream link is crawling; try another."""


def _import_error(exc: Exception) -> str:
    """What a person can act on, from yt-dlp's message."""
    text = str(exc)
    low = text.lower()
    if "longer than" in low:
        return text
    if "private" in low:
        return "This video is private. Only public or unlisted videos can be imported."
    if "sign in" in low or ("age" in low and "confirm" in low):
        return "This video needs a signed-in account (age-restricted or members-only)."
    if "unavailable" in low or "removed" in low or "not available" in low:
        return "This video is unavailable - it may have been removed or blocked in this region."
    if "live" in low:
        return "Live streams cannot be imported; wait until the stream has ended."
    if "file is larger" in low or "max-filesize" in low:
        return "This video is too large to import."
    return "The video could not be downloaded. Check the link and try again."


def _node_path() -> str | None:
    """A JavaScript runtime for YouTube's player code, if one is installed."""
    import shutil

    found = shutil.which("node")
    if found:
        return found
    candidates = sorted(Path.home().glob(".nvm/versions/node/*/bin/node"))
    return str(candidates[-1]) if candidates else None


@celery_app.task(name="visiovox.import_media", bind=True, max_retries=0)
def import_media(self: Any, import_id: str) -> dict[str, Any]:
    """Download an imported link into its project's source slot."""
    import json
    import shutil
    import subprocess
    import tempfile
    import time

    import yt_dlp

    with _Session() as session:
        row = session.get(MediaImport, import_id)
        if row is None:
            return {"status": "missing", "import_id": import_id}
        project = session.get(Project, row.project_id)
        if project is None or project.source_key is None:
            return {"status": "missing_project", "import_id": import_id}

        def fail(reason: str) -> dict[str, Any]:
            row.status = "failed"
            row.error_detail = reason[:500]
            row.finished_at = _now()
            project.status = "failed"
            session.commit()
            return {"status": "failed", "import_id": import_id, "reason": reason}

        row.status = "downloading"
        session.commit()
        limit_s = settings.max_duration_seconds
        work_root = Path(settings.media_work_dir)
        work_root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"import-{import_id}-", dir=work_root))
        last = {"at": 0.0}
        started: dict[str, float] = {}
        # Bytes of the files (video, then audio) already finished this attempt.
        finished: dict[str, int] = {}

        def overall_total(d: dict[str, Any]) -> int:
            """Every file of this download, not just the current one."""
            info = d.get("info_dict") or {}
            parts = info.get("requested_formats") or [info]
            sizes = [int(f.get("filesize") or f.get("filesize_approx") or 0) for f in parts]
            current = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            return max(sum(sizes), sum(finished.values()) + current)

        def hook(d: dict[str, Any]) -> None:
            key = str(d.get("filename"))
            if d.get("status") == "finished":
                finished[key] = int(d.get("total_bytes") or d.get("downloaded_bytes") or 0)
                return
            if d.get("status") != "downloading":
                return
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes", 0)
            now = time.monotonic()
            if now - last["at"] >= 2:
                last["at"] = now
                all_total = overall_total(d)
                all_got = sum(v for k, v in finished.items() if k != key) + int(got)
                speed = float(d.get("speed") or 0)
                row.downloaded_bytes = all_got
                row.total_bytes = all_total or None
                row.speed_bps = int(speed) if speed else None
                row.eta_seconds = (
                    int((all_total - all_got) / speed)
                    if speed > 0 and all_total > all_got
                    else None
                )
                row.progress = max(0, min(99, int(100 * all_got / all_total))) if all_total else 0
                session.commit()
            # Per file (video, then audio): a throttled link shows itself early.
            key = str(d.get("filename"))
            now = time.monotonic()
            started.setdefault(key, now)
            elapsed = now - started[key]
            if total and elapsed > _SLOW_AFTER_S:
                rate = got / elapsed
                if rate < _SLOW_BYTES_PER_S and (total - got) / max(rate, 1) > _SLOW_IF_LEFT_S:
                    raise _ThrottledError(f"throttled at {rate / 1e6:.2f} MB/s")

        def too_long(info: dict[str, Any], *, incomplete: bool) -> str | None:
            if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
                return "Live streams cannot be imported; wait until the stream has ended."
            duration = info.get("duration")
            if duration and duration > limit_s:
                return (
                    f"This video is longer than {limit_s // 60} minutes "
                    f"({int(duration) // 60} min). Import a shorter one."
                )
            return None

        options: dict[str, Any] = {
            "outtmpl": str(work / "source.%(ext)s"),
            "format": _IMPORT_FORMATS[0],
            # Segmented streams in parallel pieces, where YouTube offers them.
            "concurrent_fragment_downloads": 8,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "allowed_extractors": _IMPORT_EXTRACTORS,
            "match_filter": too_long,
            "max_filesize": settings.max_upload_bytes,
            "progress_hooks": [hook],
            "socket_timeout": 30,
            "retries": 3,
            "quiet": True,
            "no_warnings": True,
        }
        node = _node_path()
        if node:
            options["js_runtimes"] = {"node": {"path": node}}
        try:
            info = None
            error: Exception | None = None
            # Each format twice: a second request gets fresh stream links, and a
            # throttled or refused link is usually not refused again.
            for attempt, fmt in enumerate(f for f in _IMPORT_FORMATS for _ in range(2)):
                for leftover in work.glob("source.*"):
                    leftover.unlink(missing_ok=True)
                started.clear()
                finished.clear()
                try:
                    with yt_dlp.YoutubeDL({**options, "format": fmt}) as ydl:
                        info = ydl.extract_info(row.url, download=True)
                    error = None
                    break
                except Exception as exc:
                    error = exc
                    text = str(exc).lower()
                    retryable = (
                        isinstance(exc, _ThrottledError)
                        or "throttled" in text
                        or "403" in text
                        or "requested format is not available" in text
                        or "http error 5" in text
                        or "timed out" in text
                    )
                    logger.warning("import %s attempt %d (%s): %s", import_id, attempt, fmt, exc)
                    if not retryable:
                        break
            if error is not None:
                return fail(_import_error(error))
            if info is None:
                return fail("The video could not be downloaded. Check the link and try again.")
            files = sorted(work.glob("source.*"))
            if not files:
                reason = too_long(info, incomplete=False)
                return fail(reason or "The video could not be downloaded.")
            source = files[0]
            probe = subprocess.run(  # noqa: S603
                [  # noqa: S607
                    "ffprobe", "-v", "error", "-show_entries",
                    "format=duration:stream=codec_type,width,height", "-of", "json", str(source),
                ],
                check=False, capture_output=True, text=True,
            )  # fmt: skip
            meta = json.loads(probe.stdout or "{}")
            streams = meta.get("streams", [])
            video = next((s for s in streams if s.get("codec_type") == "video"), None)
            if video is None:
                return fail("This link has no video track; VisioVox needs to see the speakers.")
            duration_s = float(meta.get("format", {}).get("duration") or 0)
            if duration_s <= 0:
                return fail("The downloaded video could not be read.")

            _s3_client(settings).upload_file(
                str(source),
                settings.s3_bucket,
                project.source_key,
                ExtraArgs={"ContentType": "video/mp4"},
            )
            title = str(info.get("title") or "").strip()
            row.source_title = title[:300] or None
            if title and project.title == "Imported video":
                project.title = title[:200]
            project.source_bytes = source.stat().st_size
            project.duration_ms = round(duration_s * 1000)
            project.width = video.get("width")
            project.height = video.get("height")
            row.status = "ready"
            row.progress = 100
            row.finished_at = _now()
            session.commit()
            return {"status": "ready", "import_id": import_id, "seconds": duration_s}
        finally:
            shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------ Google imports --

_GOOGLE_HOSTS = (".googleapis.com", ".googleusercontent.com")
_DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
_PHOTOS_API = "https://photospicker.googleapis.com/v1"


class GoogleDownloadError(Exception):
    """A Google download that cannot go on; the message is for the person."""


def _google_message(status: int) -> str:
    if status == 401:
        return "Google access expired before the download started. Choose the video again."
    if status == 403:
        return "Google did not allow the download. Choose the video again and allow access."
    if status == 404:
        return "That video is no longer in your Google account."
    return f"Google did not send the video ({status}). Try again in a moment."


def _google_get(url: str, token: str, *, stream: bool) -> Any:
    """GET with the token, following redirects only within Google's hosts.

    `requests` drops the Authorization header when a redirect changes host;
    Google's media hosts need it, and no other host may receive it.
    """
    from urllib.parse import urljoin, urlsplit

    import requests

    for _ in range(5):
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            stream=stream,
            timeout=(20, 120),
            allow_redirects=False,
        )
        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
            nxt = urljoin(url, response.headers["Location"])
            host = urlsplit(nxt).hostname or ""
            response.close()
            if urlsplit(nxt).scheme != "https" or not host.endswith(_GOOGLE_HOSTS):
                raise GoogleDownloadError("Google redirected the download somewhere unexpected.")
            url = nxt
            continue
        return response
    raise GoogleDownloadError("Google redirected the download too many times.")


def _download_google(url: str, token: str, dest: Path, limit: int, report: Any) -> int:
    """Stream `url` into `dest`; `report(got, total, bytes_per_second)` every 2 s."""
    import time

    response = _google_get(url, token, stream=True)
    with response:
        if response.status_code >= 400:
            raise GoogleDownloadError(_google_message(response.status_code))
        total = int(response.headers.get("Content-Length") or 0)
        if total > limit:
            raise GoogleDownloadError("This video is too large to import.")
        got, started, last = 0, time.monotonic(), 0.0
        with dest.open("wb") as out:
            for chunk in response.iter_content(1 << 20):
                out.write(chunk)
                got += len(chunk)
                if got > limit:
                    raise GoogleDownloadError("This video is too large to import.")
                now = time.monotonic()
                if now - last >= 2:
                    last = now
                    report(got, total, got / max(now - started, 1e-3))
    if got == 0:
        raise GoogleDownloadError("Google sent an empty file.")
    return got


def _browser_playable(source: Path, work: Path) -> Path:
    """H.264 in MP4, so the trim preview plays everywhere (iPhones record HEVC)."""
    import json
    import subprocess

    probe = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,pix_fmt:format=format_name", "-of", "json",
            str(source),
        ],
        check=False, capture_output=True, text=True,
    )  # fmt: skip
    meta = json.loads(probe.stdout or "{}")
    streams = meta.get("streams") or [{}]
    codec = streams[0].get("codec_name")
    pix = streams[0].get("pix_fmt")
    container = meta.get("format", {}).get("format_name", "")
    out = work / "playable.mp4"
    if codec == "h264" and pix in (None, "yuv420p") and "mp4" in container:
        return source
    if codec == "h264" and pix in (None, "yuv420p"):
        cmd = ["-c", "copy"]
    else:
        cmd = [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-vf", "scale=-2:'min(1080,ih)'", "-c:a", "aac", "-b:a", "160k",
        ]  # fmt: skip
    done = subprocess.run(  # noqa: S603
        ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",  # noqa: S607
         *cmd, "-movflags", "+faststart", str(out)],
        check=False, capture_output=True, text=True, timeout=7200,
    )  # fmt: skip
    if done.returncode != 0 or not out.is_file():
        raise GoogleDownloadError("The video could not be read. Is it a normal video file?")
    return out


@celery_app.task(name="visiovox.import_google", bind=True, max_retries=0)
def import_google(self: Any, import_id: str, google: dict[str, Any]) -> dict[str, Any]:
    """Download a video picked in Google Drive or Google Photos into its project.

    `google` carries the person's short-lived access token; it is used here,
    never logged and never stored.
    """
    import json
    import shutil
    import subprocess
    import tempfile

    with _Session() as session:
        row = session.get(MediaImport, import_id)
        if row is None:
            return {"status": "missing", "import_id": import_id}
        project = session.get(Project, row.project_id)
        if project is None or project.source_key is None:
            return {"status": "missing_project", "import_id": import_id}

        def fail(reason: str) -> dict[str, Any]:
            row.status = "failed"
            row.error_detail = reason[:500]
            row.finished_at = _now()
            project.status = "failed"
            session.commit()
            return {"status": "failed", "import_id": import_id, "reason": reason}

        token = str(google.get("access_token") or "")
        kind = google.get("kind")
        row.status = "downloading"
        session.commit()
        work_root = Path(settings.media_work_dir)
        work_root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"import-{import_id}-", dir=work_root))

        def report(got: int, total: int, rate: float) -> None:
            row.downloaded_bytes = got
            row.total_bytes = total or None
            row.speed_bps = int(rate)
            row.eta_seconds = int((total - got) / rate) if total > got and rate > 0 else None
            row.progress = max(0, min(99, int(100 * got / total))) if total else 0
            session.commit()

        try:
            limit = min(settings.max_upload_bytes, int(shutil.disk_usage(work_root).free * 0.4))
            filename = str(google.get("filename") or "")
            if kind == "drive":
                file_id = str(google.get("file_id") or "")
                meta_response = _google_get(
                    f"{_DRIVE_FILES}/{file_id}?fields=name,mimeType,size&supportsAllDrives=true",
                    token,
                    stream=False,
                )
                if meta_response.status_code >= 400:
                    return fail(_google_message(meta_response.status_code))
                meta = meta_response.json()
                if not str(meta.get("mimeType", "")).startswith("video/"):
                    return fail("That file is not a video. Choose a video file in Google Drive.")
                if int(meta.get("size") or 0) > limit:
                    return fail("This video is too large to import.")
                filename = str(meta.get("name") or filename)
                url = f"{_DRIVE_FILES}/{file_id}?alt=media&supportsAllDrives=true"
            elif kind == "photos":
                # "=dv" asks for the video itself rather than a still of it.
                url = f"{google.get('base_url')}=dv"
            else:
                return fail("Unknown Google source.")

            raw = work / "download"
            _download_google(url, token, raw, limit, report)
            if kind == "photos" and google.get("session_id"):
                try:  # The picker session is no longer needed; tidy it away.
                    import requests

                    requests.delete(
                        f"{_PHOTOS_API}/sessions/{google['session_id']}",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=10,
                    )
                except Exception:  # noqa: S110 - best effort only
                    pass
            row.progress = 99
            row.eta_seconds = None
            session.commit()

            source = _browser_playable(raw, work)
            probe = subprocess.run(  # noqa: S603
                [  # noqa: S607
                    "ffprobe", "-v", "error", "-show_entries",
                    "format=duration:stream=codec_type,width,height", "-of", "json", str(source),
                ],
                check=False, capture_output=True, text=True,
            )  # fmt: skip
            meta = json.loads(probe.stdout or "{}")
            video = next(
                (x for x in meta.get("streams", []) if x.get("codec_type") == "video"), None
            )
            if video is None:
                return fail("This file has no video track; VisioVox needs to see the speakers.")
            duration_s = float(meta.get("format", {}).get("duration") or 0)
            if duration_s <= 0:
                return fail("The video could not be read.")

            _s3_client(settings).upload_file(
                str(source), settings.s3_bucket, project.source_key,
                ExtraArgs={"ContentType": "video/mp4"},
            )  # fmt: skip
            title = Path(filename).stem.strip() if filename else ""
            row.source_title = title[:300] or None
            if title and project.title == "Imported video":
                project.title = title[:200]
            project.source_bytes = source.stat().st_size
            project.duration_ms = round(duration_s * 1000)
            project.width = video.get("width")
            project.height = video.get("height")
            row.status = "ready"
            row.progress = 100
            row.finished_at = _now()
            session.commit()
            return {"status": "ready", "import_id": import_id, "seconds": duration_s}
        except GoogleDownloadError as exc:
            return fail(str(exc))
        except Exception as exc:
            # Never the token: only the type and a trimmed message.
            logger.warning("google import %s failed: %s", import_id, type(exc).__name__)
            return fail("The video could not be downloaded from Google. Try again.")
        finally:
            shutil.rmtree(work, ignore_errors=True)
