"""Captions for burning into an exported video (ADR-0022).

Written as ASS (Advanced SubStation Alpha) and rendered by ffmpeg's libass,
which shapes Indian scripts correctly (HarfBuzz) and finds Noto fonts for
each script through fontconfig. A burned-in caption is part of the picture:
it plays on every phone and in every feed that autoplays muted, which a
separate .srt never does.

Styling follows what is legible over any footage: white text on a
translucent box, sized to the frame. When an export carries more than one
speaker, each line leads with the speaker's name in their colour, and
overlapping lines stack rather than replace each other (libass's collision
handling).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# Speaker hues on a dark box: the product's dark-theme --spk-1..4, as RGB.
SPEAKER_RGB = ("4BA6F0", "5CC97A", "E3A63B", "E070C2")


@dataclass(frozen=True)
class Cue:
    speaker: int  # ordinal
    start_ms: int
    end_ms: int
    text: str


def _time(ms: int) -> str:
    """ASS time: h:mm:ss.cc."""
    cs = max(0, round(ms / 10))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _bgr(rgb: str) -> str:
    """ASS colours are &HAABBGGRR."""
    return f"&H00{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"


def _escape(text: str) -> str:
    # Braces open override blocks and backslashes start tags; neither may
    # come from a transcript.
    return " ".join(text.split()).replace("\\", "/").replace("{", "(").replace("}", ")")


def cues_from_captions(
    captions: Mapping[int, Mapping[str, object]], speakers: Sequence[int] | None = None
) -> list[Cue]:
    """Cues from S9/S8 caption files (`{"segments": [...]}`) per speaker."""
    out: list[Cue] = []
    for ordinal, body in captions.items():
        if speakers is not None and ordinal not in speakers:
            continue
        segments = body.get("segments", [])
        for seg in segments if isinstance(segments, list) else []:
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text", "")).strip()
            start, end = seg.get("start_ms"), seg.get("end_ms")
            if text and isinstance(start, int) and isinstance(end, int) and end > start:
                out.append(Cue(ordinal, start, end, text))
    return sorted(out, key=lambda c: (c.start_ms, c.speaker))


def build_ass(
    cues: Sequence[Cue],
    *,
    width: int,
    height: int,
    names: Mapping[int, str] | None = None,
    clip_start_ms: int = 0,
    clip_end_ms: int | None = None,
) -> str:
    """An .ass document for `cues`, shifted so `clip_start_ms` is time zero.

    `names` given (more than one speaker in the export): each line leads with
    the speaker's name in their colour.
    """
    size = max(16, round(height * 0.052))
    margin = max(10, round(height * 0.055))
    side = max(10, round(width * 0.06))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # BorderStyle 3: an opaque box behind the text, drawn translucent.
        f"Style: Default,Noto Sans,{size},&H00FFFFFF,&H00FFFFFF,&H70000000,&H70000000,"
        f"0,0,0,0,100,100,0,0,3,{max(2, size // 6)},0,2,{side},{side},{margin},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for cue in cues:
        start = cue.start_ms - clip_start_ms
        end = cue.end_ms - clip_start_ms
        if clip_end_ms is not None:
            end = min(end, clip_end_ms - clip_start_ms)
        start = max(0, start)
        if end <= start:
            continue
        text = _escape(cue.text)
        if names is not None:
            rgb = SPEAKER_RGB[(cue.speaker - 1) % len(SPEAKER_RGB)]
            name = _escape(names.get(cue.speaker, f"Speaker {cue.speaker}"))
            text = f"{{\\b1\\c{_bgr(rgb)}&}}{name}:{{\\r}} {text}"
        events.append(f"Dialogue: 0,{_time(start)},{_time(end)},Default,,0,0,0,,{text}")
    return header + "\n".join(events) + ("\n" if events else "")
