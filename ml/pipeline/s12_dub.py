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
beyond that pushes the speaker's next line later (counted, never hidden).

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
    # What the dub panel shows: the name, and the pitch when a language has
    # fewer voices than speakers and a name is used twice (Hindi and Kannada
    # have four each; an eight-person debate read "voiced by Suresh" twice).
    label: str = ""


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
            label=f"{name} ({pitch})" if pitch else name,
            description=(
                f"{name} speaks at a fast pace{tone}, in a close-sounding recording "
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
    start_ms: int  # may be later than the line's own start: see lag_ms
    end_ms: int  # after tempo
    overrun_ms: int  # beyond the slot it was given, pushed onto the next line
    lag_ms: int = 0  # how late it starts, waiting for the speaker's previous line


def plan(lines: Sequence[Line], durations_ms: Sequence[int]) -> list[Placement]:
    """Where each synthesised line goes, and how much to speed it up.

    A line's slot runs from its own start to just before the same speaker's
    next line (or, for their last line, its own end plus the gap): a speaker
    may talk over someone else, as they did, but never over themselves. A line
    longer than its slot is sped up, at most MAX_TEMPO; one still too long
    pushes the speaker's next line later, and the lag is made up in the next
    pause - starting late is better than one voice speaking twice at once.
    """
    order = sorted(range(len(lines)), key=lambda i: (lines[i].speaker, lines[i].start_ms))
    out: list[Placement | None] = [None] * len(lines)
    cursor: dict[int, int] = {}  # per speaker: when their previous line (plus gap) ends
    for pos, i in enumerate(order):
        line = lines[i]
        nxt = order[pos + 1] if pos + 1 < len(order) else None
        same_next = nxt is not None and lines[nxt].speaker == line.speaker
        slot_end = (
            lines[nxt].start_ms - GAP_MS if same_next and nxt is not None else line.end_ms + GAP_MS
        )
        start = max(line.start_ms, cursor.get(line.speaker, line.start_ms))
        slot = max(1, slot_end - start)
        dur = max(1, int(durations_ms[i]))
        tempo = min(MAX_TEMPO, dur / slot) if dur > slot else 1.0
        end = start + round(dur / tempo)
        cursor[line.speaker] = end + GAP_MS
        out[i] = Placement(
            line=line,
            tempo=round(tempo, 3),
            start_ms=start,
            end_ms=end,
            overrun_ms=max(0, end - slot_end),
            lag_ms=start - line.start_ms,
        )
    return [p for p in out if p is not None]


# Starting this much late or less is not noticeable as "late".
LATE_MS = 300


def fit_stats(placements: Sequence[Placement]) -> dict[str, float]:
    """How well the dub fits the original timing."""
    n = len(placements)
    if n == 0:
        return {
            "lines": 0, "as_is": 0.0, "sped_up": 0.0, "overrun": 0.0, "late": 0.0,
            "mean_tempo": 1.0, "median_lag_ms": 0.0, "max_lag_ms": 0.0,
        }  # fmt: skip
    sped = [p for p in placements if p.tempo > 1.0]
    over = [p for p in placements if p.overrun_ms > 0]
    late = [p for p in placements if p.lag_ms > LATE_MS]
    return {
        "lines": n,
        "as_is": (n - len(sped)) / n,
        "sped_up": len(sped) / n,
        "overrun": len(over) / n,
        "late": len(late) / n,
        "mean_tempo": float(np.mean([p.tempo for p in placements])),
        "median_lag_ms": float(np.median([p.lag_ms for p in late])) if late else 0.0,
        "max_lag_ms": float(max(p.lag_ms for p in placements)),
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


def _place(
    out: np.ndarray,
    placements: Sequence[Placement],
    audio: Mapping[int, np.ndarray],
    levels: Mapping[int, float],
    rate: int,
    speaker: int | None = None,
) -> None:
    """Add each placed line (of `speaker` only, if given) into `out`, in place."""
    for i, p in enumerate(placements):
        if speaker is not None and p.line.speaker != speaker:
            continue
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


def headroom(signal: np.ndarray) -> float:
    """The gain that keeps `signal` under full scale: 1, or less if it peaks past 0.95."""
    peak = float(np.max(np.abs(signal))) if signal.size else 0.0
    return 0.95 / peak if peak > 0.95 else 1.0


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
    _place(out, placements, audio, levels, rate)
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    # Several voices summed can exceed full scale; scale down rather than clip.
    return out / peak * 0.95 if peak > 0.95 else out


def speaker_voices(
    background: np.ndarray,
    placements: Sequence[Placement],
    audio: Mapping[int, np.ndarray],
    levels: Mapping[int, float],
    rate: int,
) -> dict[int, np.ndarray]:
    """Each dubbed speaker's new voice alone, as loud as it is inside `assemble`'s dub.

    Same placement and level as the dub, and the same overall scale, so the
    voices plus the scaled room add up to the dub: soloing one person in the
    player is hearing exactly their part of it.
    """
    length = len(background)
    voices: dict[int, np.ndarray] = {}
    for speaker in sorted({p.line.speaker for p in placements}):
        track = np.zeros(length, dtype=np.float32)
        _place(track, placements, audio, levels, rate, speaker=speaker)
        voices[speaker] = track
    total = np.asarray(background, dtype=np.float32).copy()
    for track in voices.values():
        total += track
    gain = headroom(total)
    return {s: (t * gain).astype(np.float32) for s, t in voices.items()}
