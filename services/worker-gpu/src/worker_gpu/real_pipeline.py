"""The real pipeline over user media (docs/05 §2, docs/28 §W8).

Serving extractor: AV-MossFormer2 (worker_gpu.av_tse), which follows each
speaker's lips. It replaced the trained SEAVE checkpoint on 2026-09-29, and
with it the stages that only existed to feed that model a voice reference -
diarization-driven enrolment (S4) and the face-to-voice fusion that preceded
it. A speaker is now a face on screen:

    S0 ingest -> S5 extract (faces, lips, one voice per face track)
      -> S3 fuse (place on the timeline, drop silent faces, merge tracks of
         one person) -> S7 captions -> S9 package

Four properties it has to hold:

- **Confinement.** Every ffmpeg/ffprobe touch of user media goes through
  `pipeline.sandbox` (invariant 5, ADR-0009), and the probe runs before anything
  is decoded. The extractor only ever sees S0's normalised re-encode.
- **Partial results.** Two speakers out of three beats nothing (invariant 8).
- **Resume.** Stages are keyed `(job_id, stage, version)` and a succeeded stage
  is skipped (invariant 7), so a killed worker restarts where it died.
- **Ephemeral biometrics.** Face crops and voice embeddings are deleted when the
  job ends, whatever the outcome (invariant 3, ADR-0008). A legal requirement,
  not an optimisation, so it lives in a `finally`.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from visiovox_api.config import Settings

from . import av_tse
from .stagerunner import StagePlan, StageRunner

# The most speakers a project holds (manifest schema, projects_speaker_count_check).
MAX_SPEAKERS = 8


def most_heard[T](people: list[T], spoken: list[float], n: int) -> list[T]:
    """The `n` people with the most speech, in their original order."""
    loudest = sorted(range(len(people)), key=lambda i: -spoken[i])[:n]
    return [people[i] for i in sorted(loudest)]


logger = logging.getLogger(__name__)

# Progress weights. Extraction dominates: face detection and one model pass per
# face track, scaling with both duration and the number of people on screen.
PLAN: list[StagePlan] = [
    StagePlan("S0_ingest", 0, 6),
    StagePlan("S5_extract", 1, 72),
    StagePlan("S3_fuse", 2, 78),
    StagePlan("S7_transcribe", 3, 93),
    StagePlan("S9_package", 4, 100),
]
BY_NAME = {p.stage: p for p in PLAN}

# Warnings that describe what was found rather than something that went wrong.
# They are worth recording, and they must not turn a good job `partial`.
INFORMATIONAL_WARNINGS = frozenset(
    {
        "silent_face_track_dropped",
        "face_tracks_merged",
        # S0 re-timing a variable or non-25 fps upload onto the grid: routine.
        "video_resampled_to_25fps",
    }
)


@dataclass
class PipelineOutput:
    manifest: dict[str, Any]
    artifact_dir: Path
    speaker_count: int
    overlap_ratio: float | None
    difficulty: str | None
    warnings: list[str]
    gpu_seconds: float
    # The probed length of the source. Upload completion writes a placeholder
    # (the mock pipeline's timeline length); this replaces it with the truth.
    duration_ms: int | None = None


class PipelineError(RuntimeError):
    """The job cannot produce anything useful."""


def _purge(path: Path) -> None:
    """Delete the scratch tree; loud on failure, never raising.

    It holds face crops and voice embeddings — biometric data with a legal
    deletion requirement — so a failure here is a compliance event worth a log
    line rather than something to swallow.
    """
    try:
        shutil.rmtree(path)
    except Exception:
        logger.exception("failed to purge scratch directory %s", path)


def _read_audio(path: Path) -> np.ndarray:
    import soundfile as sf

    data, _ = sf.read(path, dtype="float32", always_2d=False)
    audio = np.asarray(data, dtype=np.float32)
    return audio if audio.ndim == 1 else audio.mean(axis=1)


_DIARIZER: Any = None


def _speech_activity(
    audio: np.ndarray, tracks: list[Any], settings: Settings
) -> list[list[tuple[int, int]]]:
    """When each person spoke: pyannote's timing, named by the faces.

    Caption words alone undercount overlap (docs/29 §9), and the insights
    panel reads this instead when it is present. Loaded once per worker.
    """
    from pipeline import diarize

    global _DIARIZER
    if _DIARIZER is None:
        _DIARIZER = diarize.load_diarizer(
            settings.hf_token.get_secret_value(), settings.torch_device
        )
    segments = diarize.diarize(audio, _DIARIZER)
    speech = [
        [
            (w.start_ms, w.end_ms)
            for s in (t.transcript.segments if t.transcript is not None else [])
            for w in s.words
        ]
        for t in tracks
    ]
    return diarize.anchor(segments, speech, round(len(audio) / av_tse.SAMPLE_RATE * 1000))


def _load_embedder(settings: Settings) -> Any:
    """ECAPA, for deciding whether two face tracks are the same person.

    Loaded from the local model directory rather than fetched, so a job does not
    depend on the network.
    """
    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    device = settings.torch_device if torch.cuda.is_available() else "cpu"
    encoder = EncoderClassifier.from_hparams(
        source=settings.speaker_embedder_source,
        savedir=str(Path(settings.speaker_embedder_dir).expanduser()),
        run_opts={"device": device},
    )

    def embed(segment: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            wav = torch.from_numpy(np.asarray(segment, dtype=np.float32)).unsqueeze(0)
            vector = encoder.encode_batch(wav.to(device))
        return np.asarray(vector.squeeze().detach().cpu().numpy(), dtype=np.float32)

    return embed


def _face_vectors(
    description: dict[str, Any], tracks: list[av_tse.FaceTrack]
) -> dict[int, np.ndarray | None] | None:
    """ArcFace vector per face track, from the crops the extractor wrote.

    Held in memory only; the crops are purged with the job (invariant 3).
    Without them grouping falls back to voice, which splits people more often
    but never merges two.
    """
    try:
        from pipeline.face_identity import crop_video_for, load_embedder, track_vector

        embed = load_embedder()
        wav_of = {int(t["index"]): str(t["wav"]) for t in description["tracks"]}
        return {t.index: track_vector(crop_video_for(wav_of[t.index]), embed) for t in tracks}
    except Exception as exc:
        logger.warning("face identity unavailable: %s", exc)
        return None


def overlap_ratio(speakers: list[np.ndarray]) -> float:
    """Share of speech time where two or more people talk at once.

    Measured on the separated tracks themselves, so it needs no diarizer: a
    frame is overlapped when at least two tracks are active in it.
    """
    if not speakers:
        return 0.0
    active = np.stack([av_tse.active_frames(a) for a in speakers])
    talking = active.sum(axis=0)
    speech = int(np.count_nonzero(talking >= 1))
    return float(np.count_nonzero(talking >= 2) / speech) if speech else 0.0


def run_pipeline(
    *,
    runner: StageRunner,
    source: Path,
    settings: Settings,
    project_id: str,
    language: str | None = None,
) -> PipelineOutput:
    """Execute the pipeline for one project.

    `language` is the spoken language the person chose for captions (a code
    from visiovox_api.languages); None lets Whisper detect it per track.
    """
    from pipeline import s0_ingest, s9_package, sandbox
    from pipeline.s9_package import SpeakerTrack
    from pipeline.types import Modality

    scratch = Path(settings.media_work_dir)
    scratch.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="visiovox-job-", dir=scratch))
    keep = work / "out"

    try:
        # ---- S0: probe under confinement, then normalise -------------------
        with runner.stage(BY_NAME["S0_ingest"]) as report:
            staged = work / source.name
            shutil.copy2(source, staged)

            facts = sandbox.facts_from(sandbox.probe(staged, image=settings.media_sandbox_image))
            sandbox.assert_admissible(facts, max_duration_seconds=settings.max_duration_seconds)

            media, s0 = s0_ingest.ingest(staged, work / "media")
            for w in s0.warnings:
                report.warn(w)

        if media.video_mp4 is None:
            raise PipelineError(
                "This recording has no video. VisioVox separates speakers by following "
                "their faces, so it needs a video with the speakers on screen."
            )
        audio = _read_audio(media.analysis_wav)

        # ---- S5: faces, lips, one voice per face track ----------------------
        with runner.stage(BY_NAME["S5_extract"], gpu=True) as report:
            try:
                description = av_tse.run_extractor(
                    media.video_mp4, media.analysis_wav, work / "av_tse", settings
                )
            except av_tse.AvTseError as exc:
                logger.warning("extraction failed: %s", exc)
                raise PipelineError("The speakers could not be separated from this video.") from exc

            if not description.get("tracks"):
                raise PipelineError(
                    "No faces were found in this video. VisioVox needs the speakers' "
                    "faces on screen to tell their voices apart."
                )
            placed, lag = av_tse.place_tracks(description, audio, _read_audio)
            logger.info(
                "%d face track(s); container offset %d samples (%.1f ms)",
                len(placed),
                lag,
                lag / av_tse.SAMPLE_RATE * 1000,
            )

        # ---- S3: faces into people ------------------------------------------
        with runner.stage(BY_NAME["S3_fuse"], gpu=True) as report:
            speaking = [t for t in placed if not av_tse.is_silent(t, audio)]
            if len(speaking) < len(placed):
                report.warn("silent_face_track_dropped")
            if not speaking:
                raise PipelineError("Faces were found, but none of them was speaking.")

            try:
                embed = _load_embedder(settings) if len(speaking) > 1 else None
            except Exception as exc:
                # Without it every face track stays its own speaker: a person
                # split in two is a nuisance, not a wrong result.
                logger.warning("voice embedder unavailable: %s", exc)
                embed = None
            # Faces are the stronger identity cue: a face crop holds nobody
            # else, while an extracted voice carries the room. On held-out AMI
            # meetings, voice alone split 11 of 38 people; with faces, none.
            faces = _face_vectors(description, speaking) if len(speaking) > 1 else None
            people = av_tse.group_speakers(
                speaking, embed, settings.av_tse_merge_threshold, faces=faces
            )
            if len(people) < len(speaking):
                report.warn("face_tracks_merged")
            if len(people) > MAX_SPEAKERS:
                # A TV panel or a crowd can show more faces than a project
                # holds (the manifest and the database allow eight). Keep the
                # eight who speak most, in their order; everyone is still in
                # the original mix. Found when a 14-person debate crashed at
                # the very end of processing.
                spoken = [float(np.sum(av_tse.active_frames(p.audio))) for p in people]
                people = most_heard(people, spoken, MAX_SPEAKERS)
                report.warn("speakers_capped")

        tracks: list[SpeakerTrack] = []
        for ordinal, person in enumerate(people, start=1):
            voice = person.audio
            tracks.append(
                SpeakerTrack(
                    speaker_id=f"face_{ordinal}",
                    ordinal=ordinal,
                    label=f"Speaker {ordinal}",
                    faithful=voice,
                    modality=Modality.AUDIOVISUAL,
                    speaking_ratio=float(np.mean(av_tse.active_frames(voice))),
                    # The model reports no confidence of its own. What bounds
                    # how complete a track can be is how much of the recording
                    # this person's face was visible for: speech while they
                    # were off screen is not recovered at all.
                    mean_confidence=person.coverage(len(audio)),
                    extraction_ok=True,
                    faces=person.face_boxes(),
                )
            )
        overlap = overlap_ratio([t.faithful for t in tracks])

        # ---- S7: captions ---------------------------------------------------------
        with runner.stage(BY_NAME["S7_transcribe"], gpu=True) as report:
            try:
                from pipeline import s7_transcribe

                transcriber = s7_transcribe.load_transcriber(device=settings.torch_device)
                # Invariant 6: the FAITHFUL track is what gets transcribed.
                # Captions must reflect what was recovered, not what a
                # generative model found plausible. VAD on and no conditioning
                # on previous text: the configuration measured on AMI-AV.
                transcripts = [
                    s7_transcribe.transcribe(
                        track.faithful,
                        transcriber,
                        # A chosen language is honoured as given: automatic
                        # detection misreads accented speech (docs/29 §7).
                        language=language,
                        vad_filter=True,
                        condition_on_previous_text=False,
                    )[0]
                    for track in tracks
                ]
                # Each track also carries the others' speech a few dB down, and
                # Whisper writes it down: the same words then appear under
                # several faces. Keep each such word only where it belongs.
                if len(tracks) > 1:
                    streams = np.stack([t.faithful[: len(audio)] for t in tracks])
                    transcripts = s7_transcribe.deduplicate_across(transcripts, streams, audio)
                for track, transcript in zip(tracks, transcripts, strict=True):
                    track.transcript = transcript
            except Exception as exc:
                logger.warning("transcription unavailable: %s", exc)
                report.warn("captions_unavailable")
            else:
                # Optional: without it the insights fall back to caption words.
                try:
                    for track, activity in zip(
                        tracks, _speech_activity(audio, tracks, settings), strict=True
                    ):
                        track.activity = activity
                except Exception as exc:
                    logger.warning("diarization unavailable: %s", exc)

        # ---- S9: package -----------------------------------------------------------
        with runner.stage(BY_NAME["S9_package"]) as report:
            # The picture for the player: S0's normalised re-encode, which has
            # no soundtrack of its own - the audio is the speaker tracks, mixed
            # in the browser. Same frames the face tracks were counted on.
            keep.mkdir(parents=True, exist_ok=True)
            shutil.copy2(media.video_mp4, keep / "video.mp4")
            width, height = description["frame_size"]
            manifest, s9 = s9_package.package(
                project_id,
                tracks,
                keep,
                has_video=True,
                video_url="video.mp4",
                video_size=(int(width), int(height)),
                overlap_ratio=overlap,
                mixture=audio,
                signed_until="",
                base_url="",
            )
            for w in s9.warnings:
                report.warn(w)

        # Copied out of the scratch tree before the purge below removes it.
        staging = Path(tempfile.mkdtemp(prefix="visiovox-out-", dir=scratch))
        shutil.copytree(keep, staging / "out")

        speakers = manifest.get("speakers", [])
        return PipelineOutput(
            manifest=manifest,
            artifact_dir=staging / "out",
            speaker_count=len(speakers) if isinstance(speakers, list) else 0,
            overlap_ratio=overlap,
            difficulty=manifest.get("difficulty"),
            warnings=list(runner.report.warnings),
            gpu_seconds=runner.report.gpu_seconds,
            duration_ms=round(facts.duration_seconds * 1000)
            if facts.duration_seconds > 0
            else None,
        )
    finally:
        # Invariant 3 / ADR-0008: face crops and embeddings go, whatever
        # happened.
        _purge(work)
