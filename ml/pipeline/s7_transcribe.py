"""S7 — Transcription and word alignment (docs/05 §10).

Runs per speaker stream, on the **Faithful** track (invariant 6). That is not
an implementation detail: the Natural track may have been through generative
restoration, which can invent words, and captions must reflect what was
actually recovered rather than what sounded best. Transcribing the wrong track
undermines the whole hallucination-safety design at its source.

All timings are integer milliseconds at the API boundary (invariant 2).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .types import StageResult, StageStatus

STAGE = "S7_transcribe"
VERSION = "1.0.0"

DEFAULT_MODEL = "large-v3"


@dataclass(frozen=True)
class Word:
    text: str
    start_ms: int
    end_ms: int
    probability: float


@dataclass(frozen=True)
class Segment:
    text: str
    start_ms: int
    end_ms: int
    words: tuple[Word, ...]
    no_speech_prob: float

    @property
    def mean_word_confidence(self) -> float:
        if not self.words:
            return 0.0
        return float(np.mean([w.probability for w in self.words]))


@dataclass
class Transcript:
    segments: list[Segment]
    language: str
    language_probability: float

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    def to_vtt(self) -> str:
        """WebVTT for download and for the HLS subtitle renditions.

        Not what the player reads. VTT cannot carry word timings cleanly, and
        word timings are what click-to-seek and per-word confidence need — so
        the runtime format is `to_json` below and this is an export.
        """
        lines = ["WEBVTT", ""]
        for i, seg in enumerate(self.segments, start=1):
            lines.append(str(i))
            lines.append(f"{_vtt_time(seg.start_ms)} --> {_vtt_time(seg.end_ms)}")
            lines.append(seg.text.strip())
            lines.append("")
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        """The word-timed form the player indexes (docs/12 §5).

        Read by `apps/web/src/lib/playback/captions.ts`, whose parser skips any
        segment missing `text`, `start_ms` or `end_ms` — so those three keys are
        the contract and the rest is additive. `probability` is carried per word
        because the player dims low-confidence words rather than hiding the
        uncertainty, which is the same disclosure principle as the per-segment
        confidence in the manifest.
        """
        return {
            "language": self.language,
            "language_probability": self.language_probability,
            "segments": [
                {
                    "text": seg.text,
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                    "no_speech_prob": seg.no_speech_prob,
                    "words": [
                        {
                            "text": w.text,
                            "start_ms": w.start_ms,
                            "end_ms": w.end_ms,
                            "probability": w.probability,
                        }
                        for w in seg.words
                    ],
                }
                for seg in self.segments
            ],
        }


def _vtt_time(ms: int) -> str:
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


class Transcriber(Protocol):
    """The slice of faster-whisper we use, so strict typing has something real.

    faster-whisper ships no stubs, so the element types stay Any; the shape of
    the call is still checked.
    """

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> tuple[Iterable[Any], Any]: ...


def load_transcriber(
    model_size: str = DEFAULT_MODEL,
    device: str = "cuda",
    compute_type: str = "float16",
    cache: Path | None = None,
) -> Transcriber:
    from faster_whisper import WhisperModel

    model: Transcriber = WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        download_root=str(cache) if cache else None,
    )
    return model


def transcribe(
    audio: np.ndarray,
    model: Transcriber,
    language: str | None = None,
    beam_size: int = 5,
    *,
    vad_filter: bool = False,
    condition_on_previous_text: bool = True,
) -> tuple[Transcript, StageResult]:
    """Transcribe one stream with word-level timings.

    `vad_filter` defaults off for callers that have already decided where
    speech is. The real pipeline transcribes the whole mixture, with the VAD on
    and without conditioning on previous text: the configuration measured on
    AMI-AV (scripts/bench_ami_seat.py).
    """
    t0 = time.perf_counter()
    result = StageResult(stage=STAGE, status=StageStatus.OK)

    raw_segments, info = model.transcribe(
        audio,
        language=language,
        beam_size=beam_size,
        word_timestamps=True,
        vad_filter=vad_filter,
        condition_on_previous_text=condition_on_previous_text,
    )

    segments: list[Segment] = []
    for seg in raw_segments:
        words = tuple(
            Word(
                text=w.word,
                start_ms=round(w.start * 1000),
                end_ms=round(w.end * 1000),
                probability=float(w.probability),
            )
            for w in (seg.words or [])
        )
        segments.append(
            Segment(
                text=seg.text,
                start_ms=round(seg.start * 1000),
                end_ms=round(seg.end * 1000),
                words=words,
                no_speech_prob=float(seg.no_speech_prob),
            )
        )

    transcript = Transcript(
        segments=segments,
        language=str(info.language),
        language_probability=float(info.language_probability),
    )

    if not segments:
        result.status = StageStatus.DEGRADED
        result.warn("empty_transcript")
    elif transcript.language_probability < 0.5:
        # Worth surfacing: a low-confidence language guess usually means the
        # stream is mostly noise or the extraction failed for this speaker.
        result.warn("low_language_confidence")

    result.seconds = time.perf_counter() - t0
    result.detail = f"{len(segments)} segments, lang={transcript.language}"
    return transcript, result


# --------------------------------------------------------------------------
# One copy of each word, in the right person's captions
# --------------------------------------------------------------------------


def deduplicate_across(
    transcripts: list[Transcript], streams: np.ndarray, mixture: np.ndarray
) -> list[Transcript]:
    """Drop words that are another person's speech leaking into this track.

    Per-track transcription alone does not work on real meetings: every track
    keeps the room a few dB down and Whisper writes that cross-talk down, so
    each person's captions contain most of the meeting. On 10 held-out AMI-AV
    meetings this put words under the right face with 110 % error; with the
    duplicates removed, 78 % - while under heavy overlap, where transcribing
    the mixture once loses one speaker entirely, it keeps per-track accuracy
    (paper Sec. VII; scripts/bench_ami_dedup.py, check_overlap_captions.py).

    `streams` are the people's tracks on the mixture's timeline, in the same
    order as `transcripts`.
    """
    from .word_dedup import deduplicate

    words = [
        [(w.start_ms / 1000, w.end_ms / 1000, w.text) for seg in t.segments for w in seg.words]
        for t in transcripts
    ]
    keep = deduplicate(words, streams, mixture, normalise=_match_key())
    return [_keep_words(t, flags) for t, flags in zip(transcripts, keep, strict=True)]


def _match_key() -> Callable[[str], str] | None:
    """Whisper's English normaliser when installed (what was measured), else None."""
    try:
        from whisper_normalizer.english import EnglishTextNormalizer
    except ImportError:
        return None
    normaliser = EnglishTextNormalizer()
    return lambda text: str(normaliser(text)).strip()


def _keep_words(transcript: Transcript, flags: list[bool]) -> Transcript:
    out = Transcript(
        segments=[],
        language=transcript.language,
        language_probability=transcript.language_probability,
    )
    i = 0
    for seg in transcript.segments:
        if not seg.words:
            out.segments.append(seg)
            continue
        kept = [w for w, ok in zip(seg.words, flags[i : i + len(seg.words)], strict=True) if ok]
        i += len(seg.words)
        if len(kept) == len(seg.words):
            out.segments.append(seg)
        elif kept:
            out.segments.append(
                Segment(
                    text="".join(w.text for w in kept).strip(),
                    start_ms=kept[0].start_ms,
                    end_ms=kept[-1].end_ms,
                    words=tuple(kept),
                    no_speech_prob=seg.no_speech_prob,
                )
            )
    return out
