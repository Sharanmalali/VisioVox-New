"""S12: dub each speaker into another language, keeping the room (ADR-0024).

Ordinary dubbing replaces the whole soundtrack, so the room - laughter,
the other people, the ambience - goes with it. With every person separated,
only the people being dubbed need to be removed:

    room   = mixture - sum_k g_k * s_k        (what nobody dubbed said, and the room)
    dubbed = room + sum_k voice_k             (each speaker's translated lines, re-voiced)

`s_k` are the separated tracks as packaged (S9 loudness-normalises each one),
so the scale each had inside the mixture, `g_k`, is recovered by least
squares rather than assumed. Each translated caption segment is spoken by a
text-to-speech voice of its own per speaker and placed at the segment's start;
a line that runs longer than its slot is sped up, at most MAX_TEMPO, and
beyond that allowed to overrun (counted, never hidden).

Pure numpy here; synthesis runs in its own environment
(services/worker-gpu/tts/run_tts.py) and time-stretching in ffmpeg.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

# Speeding a line up beyond this sounds hurried; past it, a line overruns.
MAX_TEMPO = 1.35
# Silence kept before the same speaker's next line.
GAP_MS = 120

# Named voices per language (Indic Parler-TTS model card), recommended first.
VOICES: dict[str, tuple[str, ...]] = {
    "as": ("Amit", "Sita", "Poonam", "Rakesh"),
    "bn": ("Arjun", "Aditi", "Tapan", "Rashmi", "Arnav", "Riya"),
    "en": ("Thoma", "Mary", "Dinesh", "Meera", "Jatin", "Sneha", "Kabir", "Priya"),
    "gu": ("Yash", "Neha"),
    "hi": ("Rohit", "Divya", "Aman", "Rani"),
    "kn": ("Suresh", "Anu", "Chetan", "Vidya"),
    "ml": ("Anjali", "Harish", "Anju"),
    "mni": ("Laishram", "Ranjit"),
    "mr": ("Sanjay", "Sunita", "Nikhil", "Radha", "Varun", "Isha"),
    "ne": ("Amrita",),
    "or": ("Manas", "Debjani"),
    "pa": ("Divjot", "Gurpreet"),
    "sa": ("Aryan",),
    "ta": ("Jaya", "Kavitha"),
    "te": ("Prakash", "Lalitha", "Kiran"),
}

# When a language has fewer voices than speakers, the same voice is told
# apart by pitch - which the model's description controls.
PITCHES = ("", "a slightly higher pitch", "a slightly lower pitch", "a higher pitch")


@dataclass(frozen=True)
class Voice:
    name: str
    description: str


def assign_voices(language: str, ordinals: Sequence[int]) -> dict[int, Voice]:
    """A distinct voice per speaker, in speaker order."""
    names = VOICES[language]
    out: dict[int, Voice] = {}
    for i, ordinal in enumerate(sorted(ordinals)):
        name = names[i % len(names)]
        pitch = PITCHES[(i // len(names)) % len(PITCHES)]
        tone = f" with {pitch}" if pitch else ""
        out[ordinal] = Voice(
            name=name,
            description=(
                f"{name} speaks at a moderate pace{tone}, in a close-sounding recording "
                "with very clear audio and very little background noise."
            ),
        )
    return out


@dataclass(frozen=True)
class Line:
    speaker: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class Placement:
    line: Line
    tempo: float  # 1.0 = as synthesised
    start_ms: int
    end_ms: int  # after tempo
    overrun_ms: int  # beyond the slot it was given


def plan(lines: Sequence[Line], durations_ms: Sequence[int]) -> list[Placement]:
    """Where each synthesised line goes, and how much to speed it up.

    A line's slot runs from its own start to just before the same speaker's
    next line (or, for their last line, its own end plus the gap): a speaker
    may talk over someone else, as they did, but never over themselves.
    """
    order = sorted(range(len(lines)), key=lambda i: (lines[i].speaker, lines[i].start_ms))
    out: list[Placement | None] = [None] * len(lines)
    for pos, i in enumerate(order):
        line = lines[i]
        nxt = order[pos + 1] if pos + 1 < len(order) else None
        same_next = nxt is not None and lines[nxt].speaker == line.speaker
        slot_end = (
            lines[nxt].start_ms - GAP_MS if same_next and nxt is not None else line.end_ms + GAP_MS
        )
        slot = max(1, slot_end - line.start_ms)
        dur = max(1, int(durations_ms[i]))
        tempo = min(MAX_TEMPO, dur / slot) if dur > slot else 1.0
        length = round(dur / tempo)
        out[i] = Placement(
            line=line,
            tempo=round(tempo, 3),
            start_ms=line.start_ms,
            end_ms=line.start_ms + length,
            overrun_ms=max(0, line.start_ms + length - slot_end),
        )
    return [p for p in out if p is not None]


def fit_stats(placements: Sequence[Placement]) -> dict[str, float]:
    """How well the dub fits the original timing."""
    n = len(placements)
    if n == 0:
        return {"lines": 0, "as_is": 0.0, "sped_up": 0.0, "overrun": 0.0, "mean_tempo": 1.0}
    sped = [p for p in placements if p.tempo > 1.0]
    over = [p for p in placements if p.overrun_ms > 0]
    return {
        "lines": n,
        "as_is": (n - len(sped)) / n,
        "sped_up": len(sped) / n,
        "overrun": len(over) / n,
        "mean_tempo": float(np.mean([p.tempo for p in placements])),
        "median_overrun_ms": float(np.median([p.overrun_ms for p in over])) if over else 0.0,
    }


def track_gains(mixture: np.ndarray, tracks: Sequence[np.ndarray]) -> np.ndarray:
    """g minimising ||mixture - sum_k g_k tracks_k||^2 (least squares)."""
    if not tracks:
        return np.zeros(0)
    n = min(len(mixture), *(len(t) for t in tracks))
    a = np.stack([np.asarray(t[:n], dtype=np.float64) for t in tracks], axis=1)
    g, *_ = np.linalg.lstsq(a, np.asarray(mixture[:n], dtype=np.float64), rcond=None)
    return np.asarray(g, dtype=np.float64)


def room(mixture: np.ndarray, tracks: Sequence[np.ndarray], gains: np.ndarray) -> np.ndarray:
    """The mixture with the given separated voices taken out."""
    out = np.asarray(mixture, dtype=np.float64).copy()
    for g, t in zip(gains, tracks, strict=True):
        n = min(len(out), len(t))
        out[:n] -= g * np.asarray(t[:n], dtype=np.float64)
    return out.astype(np.float32)


def residual_db(signal: np.ndarray, reference: np.ndarray, mixture: np.ndarray) -> float:
    """How much of one person's voice is left in `signal`, against the mixture.

    The energy of `signal` along the reference (the person's close-talk
    microphone) relative to the mixture's along it, in dB: 0 dB is as present
    as in the original, -20 dB a hundredth of the energy.
    """
    ref = np.asarray(reference, dtype=np.float64)
    denom = float(ref @ ref)
    if denom <= 0:
        return float("nan")

    def along(x: np.ndarray) -> float:
        x = np.asarray(x[: len(ref)], dtype=np.float64)
        return float(((x @ ref[: len(x)]) ** 2) / denom)

    m = along(mixture)
    return float(10 * np.log10(max(along(signal), 1e-20) / max(m, 1e-20)))


def assemble(
    background: np.ndarray,
    placements: Sequence[Placement],
    audio: Mapping[int, np.ndarray],
    levels: Mapping[int, float],
    rate: int,
) -> np.ndarray:
    """`background` plus each placed line (already stretched), at its speaker's level.

    `audio[i]` is line i's synthesised audio after its tempo change, at `rate`;
    `levels[speaker]` the RMS that speaker had in the recording.
    """
    out = np.asarray(background, dtype=np.float32).copy()
    for i, p in enumerate(placements):
        clip = np.asarray(audio.get(i, np.zeros(0)), dtype=np.float32)
        if clip.size == 0:
            continue
        rms = float(np.sqrt(np.mean(np.square(clip, dtype=np.float64))))
        target = levels.get(p.line.speaker, 0.05)
        if rms > 0:
            clip = clip * (target / rms)
        start = round(p.start_ms * rate / 1000)
        if start >= len(out):
            continue
        end = min(len(out), start + len(clip))
        out[start:end] += clip[: end - start]
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    # Several voices summed can exceed full scale; scale down rather than clip.
    return out / peak * 0.95 if peak > 0.95 else out
