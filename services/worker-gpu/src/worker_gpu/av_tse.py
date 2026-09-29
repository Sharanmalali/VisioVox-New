"""Audio-visual target speaker extraction with AV-MossFormer2 (ClearerVoice-Studio).

The serving extractor. It replaces the trained SEAVE checkpoint (C3), which was
conditioned on a voice enrolment mined from diarization; AV-MossFormer2 is
conditioned on the speaker's lips instead, so a speaker is a *face track*
rather than a diarized voice.

The model runs in its own interpreter (`av_tse/run_av_tse.py`, AV_TSE_PYTHON).
What comes back is one separated voice per face track, each covering only the
frames that face was on screen. Turning that into what S9 expects - one
full-length track per person, in sync with the recording - happens here:

- **Placement.** The runner counts on its own 25 fps re-encode, whose audio is
  offset from the reference (S0's analysis wav) by the container's encoder
  delay - measured at 69 ms on an AAC upload. The offset is found by
  cross-correlating the two mixtures, never assumed.
- **Silent faces.** A face that never speaks (a listener, a photo on the wall)
  still gets a track, and the model correctly returns near-silence for it.
  Those are dropped rather than shown as an empty speaker.
- **One person, several tracks.** A camera cut or a lost face starts a new
  track. Tracks that never overlap in time and whose voices match (ECAPA
  cosine) are merged into one speaker.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from visiovox_api.config import Settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FPS = 25
FRAME_SAMPLES = SAMPLE_RATE // FPS  # 640, the grid the rest of the pipeline uses
RUNNER = Path(__file__).resolve().parents[2] / "av_tse" / "run_av_tse.py"

# A track quieter than this, relative to the mixture over the same span, is a
# face that never spoke. -30 dB is far below any real extraction (the worst
# measured speaker was still within 10 dB of the mixture) and far above the
# model's residual on a silent target.
SILENT_TRACK_DB = -30.0
# Frame activity, relative to the track's own loud frames.
ACTIVE_FRAME_DB = -30.0
# ECAPA cosine above which two time-disjoint tracks are one person. Same-speaker
# pairs typically score 0.6-0.8, different speakers under 0.3.
DEFAULT_MERGE_THRESHOLD = 0.6
# Search window for the container offset. Encoder delays are tens of ms; a
# second of slack covers any muxer oddity without letting a repeated phrase
# produce a false match.
MAX_LAG_SAMPLES = SAMPLE_RATE
LAG_WINDOW_SECONDS = 60


class AvTseError(RuntimeError):
    """The extractor could not run over this recording."""


@dataclass
class FaceTrack:
    """One face track's separated voice, placed on the recording's timeline."""

    index: int
    start: int  # sample, inclusive
    end: int  # sample, exclusive
    center_x: float  # 0 = left edge of the frame, 1 = right
    audio: np.ndarray  # full recording length, zero outside [start, end)


@dataclass
class Speaker:
    """One person: every face track that belongs to them, merged."""

    tracks: list[FaceTrack] = field(default_factory=list)

    @property
    def audio(self) -> np.ndarray:
        return np.sum([t.audio for t in self.tracks], axis=0).astype(np.float32)

    @property
    def first_seen(self) -> int:
        return min(t.start for t in self.tracks)

    @property
    def center_x(self) -> float:
        spans = np.array([t.end - t.start for t in self.tracks], dtype=np.float64)
        xs = np.array([t.center_x for t in self.tracks], dtype=np.float64)
        return float(np.average(xs, weights=spans))

    def coverage(self, n_samples: int) -> float:
        """Fraction of the recording where this person's face was tracked.

        Speech outside it is not recovered at all - the model has no lips to
        follow - so this is the honest ceiling on how complete the track is.
        """
        covered = sum(t.end - t.start for t in self.tracks)
        return min(1.0, covered / max(1, n_samples))

    def overlaps(self, track: FaceTrack) -> bool:
        return any(t.start < track.end and track.start < t.end for t in self.tracks)


# --------------------------------------------------------------------------
# the runner
# --------------------------------------------------------------------------


def run_extractor(video: Path, audio: Path, work: Path, settings: Settings) -> dict[str, Any]:
    """Run AV-MossFormer2 over `video` with `audio` as its soundtrack.

    `audio` is S0's analysis wav: the timeline every later stage works on, and
    the one the extracted tracks are placed back onto.
    """
    python = Path(settings.av_tse_python).expanduser()
    clearvoice = Path(settings.av_tse_clearvoice_dir).expanduser()
    if not python.is_file():
        raise AvTseError(f"AV-TSE interpreter not found at {python}; set AV_TSE_PYTHON")
    if not (clearvoice / "checkpoints" / "AV_MossFormer2_TSE_16K").is_dir():
        raise AvTseError(f"AV-MossFormer2 checkpoint not found under {clearvoice}")

    work.mkdir(parents=True, exist_ok=True)
    out = work / "tracks.json"
    env = {
        k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    }
    # Weights are local; a job must never wait on, or fail because of, the hub.
    env["HF_HUB_OFFLINE"] = "1"
    # ClearVoice picks its GPU by running nvidia-smi, and quietly falls back to
    # the CPU when it cannot - while its face detector stays on CUDA, so the
    # job then dies on a device mismatch. Under WSL the binary lives in
    # /usr/lib/wsl/lib, which a systemd unit's PATH does not include.
    wsl_lib = "/usr/lib/wsl/lib"
    if Path(wsl_lib, "nvidia-smi").exists() and wsl_lib not in env.get("PATH", "").split(":"):
        env["PATH"] = f"{env.get('PATH', '')}:{wsl_lib}"
    cmd = [
        str(python),
        str(RUNNER),
        "--clearvoice-dir",
        str(clearvoice),
        "--video",
        str(video),
        "--audio",
        str(audio),
        "--work",
        str(work / "run"),
        "--out",
        str(out),
    ]

    log_path = work / "av_tse.log"
    with log_path.open("wb") as log:
        try:
            # Our own interpreter and script, over S0's normalised re-encode.
            proc = subprocess.run(  # noqa: S603
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=settings.av_tse_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AvTseError(f"AV-MossFormer2 timed out after {exc.timeout:.0f}s") from exc
    if proc.returncode != 0 or not out.is_file():
        tail = log_path.read_bytes()[-2000:].decode("utf-8", "replace")
        raise AvTseError(f"AV-MossFormer2 exited {proc.returncode}: {tail.strip()[-600:]}")

    description: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if " av_tse: " in line:
            logger.info("%s", line.split(" av_tse: ", 1)[1][:200])
    return description


# --------------------------------------------------------------------------
# placement
# --------------------------------------------------------------------------


def container_lag(reference: np.ndarray, runner_audio: np.ndarray) -> int:
    """Samples by which the runner's audio lags the reference.

    Positive means `runner_audio[t + lag] == reference[t]`. Found by FFT
    cross-correlation over the first minute, which is plenty for a constant
    offset and keeps an hour-long upload from costing an hour-long FFT.
    """
    from scipy.signal import correlate

    n = min(len(reference), len(runner_audio), LAG_WINDOW_SECONDS * SAMPLE_RATE)
    if n < SAMPLE_RATE // 10:
        return 0
    ref = reference[:n].astype(np.float64)
    run = runner_audio[:n].astype(np.float64)
    c = correlate(run, ref, mode="full", method="fft")
    zero = n - 1
    max_lag = min(MAX_LAG_SAMPLES, n - 1)
    window = c[zero - max_lag : zero + max_lag + 1]
    return int(np.argmax(np.abs(window))) - max_lag


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if x.size else 0.0


def place_tracks(
    description: dict[str, Any], reference: np.ndarray, read: Callable[[Path], np.ndarray]
) -> tuple[list[FaceTrack], int]:
    """Put every face track's voice at its position in the reference timeline."""
    runner_audio = read(Path(description["audio_wav"]))
    lag = container_lag(reference, runner_audio)
    n = len(reference)

    placed: list[FaceTrack] = []
    for t in description["tracks"]:
        est = read(Path(t["wav"])).astype(np.float32)
        # The track's audio is the runner's span [start_frame, end_frame);
        # moved back by the lag, that is where it sits in the reference.
        start = int(t["start_frame"]) * FRAME_SAMPLES - lag
        lo, hi = max(0, start), min(n, start + len(est))
        if hi <= lo:
            continue
        audio = np.zeros(n, dtype=np.float32)
        audio[lo:hi] = est[lo - start : hi - start]
        placed.append(
            FaceTrack(
                index=int(t["index"]), start=lo, end=hi, center_x=float(t["center_x"]), audio=audio
            )
        )
    return placed, lag


def is_silent(track: FaceTrack, reference: np.ndarray) -> bool:
    """Did this face never speak? Judged against the mixture over its own span."""
    mix = _rms(reference[track.start : track.end])
    own = _rms(track.audio[track.start : track.end])
    if mix <= 0:
        return True
    return bool(20 * np.log10(max(own, 1e-12) / mix) < SILENT_TRACK_DB)


def active_frames(audio: np.ndarray) -> np.ndarray:
    """Per-frame speech activity on the 25 fps grid."""
    n_frames = max(1, len(audio) // FRAME_SAMPLES)
    frames = audio[: n_frames * FRAME_SAMPLES].reshape(n_frames, FRAME_SAMPLES)
    level = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
    loud = float(np.percentile(level, 95)) if level.size else 0.0
    if loud <= 1e-6:
        return np.zeros(n_frames, dtype=bool)
    return np.asarray(level > loud * 10 ** (ACTIVE_FRAME_DB / 20), dtype=bool)


# --------------------------------------------------------------------------
# one person, several tracks
# --------------------------------------------------------------------------


def _voice_sample(track: FaceTrack, max_seconds: float = 30.0) -> np.ndarray:
    """The track's speech only, capped - what the voice embedder should hear."""
    span = track.audio[track.start : track.end]
    mask = np.repeat(active_frames(span), FRAME_SAMPLES)
    speech = span[: len(mask)][mask]
    return np.asarray(speech[: int(max_seconds * SAMPLE_RATE)], dtype=np.float32)


def group_speakers(
    tracks: list[FaceTrack],
    embed: Callable[[np.ndarray], np.ndarray] | None,
    threshold: float = DEFAULT_MERGE_THRESHOLD,
) -> list[Speaker]:
    """Merge time-disjoint tracks whose voices match; order speakers left to right.

    Two tracks that overlap in time are two people by construction - one face
    cannot be on screen twice - so they are never compared. Without an
    embedder every track stays its own speaker: a split person is a nuisance,
    two people merged into one is a wrong result.
    """
    speakers: list[Speaker] = []
    vectors: dict[int, np.ndarray] = {}

    def vector(t: FaceTrack) -> np.ndarray | None:
        if embed is None:
            return None
        if t.index not in vectors:
            sample = _voice_sample(t)
            if len(sample) < SAMPLE_RATE:  # under a second of speech: not enough to judge
                return None
            v = np.asarray(embed(sample), dtype=np.float64).reshape(-1)
            vectors[t.index] = v / (np.linalg.norm(v) + 1e-12)
        return vectors.get(t.index)

    for track in sorted(tracks, key=lambda t: t.start):
        best, best_score = None, threshold
        v = vector(track)
        if v is not None:
            for sp in speakers:
                if sp.overlaps(track):
                    continue
                scores = [float(v @ u) for t in sp.tracks if (u := vector(t)) is not None]
                if scores and max(scores) > best_score:
                    best, best_score = sp, max(scores)
        if best is not None:
            logger.info("face track %d joins a speaker (voice match %.2f)", track.index, best_score)
            best.tracks.append(track)
        else:
            speakers.append(Speaker(tracks=[track]))

    # Left to right, as the viewer sees them; ties by who appeared first.
    speakers.sort(key=lambda s: (round(s.center_x, 1), s.first_seen))
    return speakers
