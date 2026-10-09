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
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from visiovox_api.config import Settings, get_settings
from visiovox_api.models import (
    Export,
    Job,
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


def _upload_artifacts(directory: Path, project_id: str) -> None:
    """Upload every packaged file to `projects/<id>/` in the bucket."""
    client = _s3_client(settings)
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        key = f"projects/{project_id}/{path.relative_to(directory).as_posix()}"
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


@celery_app.task(name="visiovox.translate_captions", bind=True, max_retries=0)
def translate_captions(self: Any, translation_id: str) -> dict[str, Any]:
    """Translate every speaker's captions of one project into one language."""
    import json

    from pipeline import s8_translate
    from visiovox_api.translation_languages import BY_CODE, SOURCE_FLORES

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

        target = BY_CODE.get(row.language)
        if target is None:
            return fail("this language is not offered")

        base = Path(settings.media_root) / "projects" / project.id
        out_dir = base / "translations" / row.language
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
                return fail(
                    f"captions in '{body.get('language')}' cannot be translated by this model"
                )
            try:
                translated = s8_translate.translate_transcript(
                    body, source, target.nllb, target.code, _translator()
                )
            except Exception as exc:
                logger.exception("translation %s failed", translation_id)
                return fail(f"translation failed: {exc}")
            path = out_dir / f"spk_{ordinal}.json"
            path.write_text(json.dumps(translated, ensure_ascii=False), encoding="utf-8")
            client.upload_file(
                str(path),
                settings.s3_bucket,
                f"projects/{project.id}/translations/{row.language}/spk_{ordinal}.json",
                ExtraArgs={"ContentType": "application/json; charset=utf-8"},
            )
            written += 1

        if written == 0:
            return fail("this recording has no captions to translate")
        row.status = "ready"
        row.finished_at = _now()
        session.commit()
        return {"status": "ready", "translation_id": translation_id, "speakers": written}


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
