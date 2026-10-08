"""Conversation insights from who-spoke-when: talk time, turns, overlap, interruptions.

The product computes these in the browser (apps/web/src/lib/playback/insights.ts)
from each separated track's word timings; this is the same algorithm, line for
line, so the AMI benchmark (scripts/bench_ami_insights.py) measures exactly
what users are shown. `packages/contracts/examples/insights.json` pins both
implementations to the same expected output.

Input is, per speaker, a list of (start_ms, end_ms) intervals - words, or
diarization segments for the baseline. Definitions:

- spurt: a speaker's words joined across gaps of at most MERGE_GAP_MS.
- overlap: time when two or more speakers' spurts run at once (regions shorter
  than MIN_OVERLAP_MS are ignored, and regions closer than that are joined).
- an overlap onset is speaker B starting a spurt while speaker A is mid-spurt
  (A started at least ONSET_MARGIN_MS earlier). It is
    a backchannel   if B's spurt is shorter than BACKCHANNEL_MS and ends
                    before A's ("yeah", "right");
    an interruption if A stops within YIELD_MS of B's onset and B keeps going
                    past A's end - B took the floor;
    talk-over       otherwise - both kept talking.
- a turn: consecutive non-backchannel spurts by one speaker, until another
  speaker's spurt starts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

MERGE_GAP_MS = 500
MIN_OVERLAP_MS = 250
ONSET_MARGIN_MS = 200
BACKCHANNEL_MS = 1000
YIELD_MS = 2000

Interval = tuple[int, int]


@dataclass(frozen=True)
class Spurt:
    speaker: int
    start: int
    end: int
    words: int

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Event:
    kind: str  # "interruption" | "talk_over" | "backchannel"
    at: int
    by: int
    of: int


@dataclass
class SpeakerStats:
    speaker: int
    talk_ms: int = 0
    words: int = 0
    turns: int = 0
    longest_turn_ms: int = 0
    interruptions_made: int = 0
    interruptions_received: int = 0
    backchannels: int = 0
    share: float = 0.0


@dataclass
class Insights:
    speakers: dict[int, SpeakerStats]
    spurts: list[Spurt]
    overlaps: list[Interval]
    events: list[Event]
    overlap_ms: int
    speech_ms: int
    duration_ms: int
    silence_ms: int = field(init=False)

    def __post_init__(self) -> None:
        self.silence_ms = max(0, self.duration_ms - self.speech_ms)


def spurts_of(speaker: int, intervals: Sequence[Interval]) -> list[Spurt]:
    """Join one speaker's words into spurts."""
    out: list[Spurt] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        last = out[-1] if out else None
        if last is not None and start - last.end <= MERGE_GAP_MS:
            out[-1] = Spurt(speaker, last.start, max(last.end, end), last.words + 1)
        else:
            out.append(Spurt(speaker, start, end, 1))
    return out


def _union_length(intervals: Sequence[Interval]) -> int:
    total = 0
    cursor = -1
    for start, end in sorted(intervals):
        if end <= cursor:
            continue
        total += end - max(start, cursor)
        cursor = end
    return total


def _overlap_regions(spurts: Sequence[Spurt]) -> list[Interval]:
    """Where two or more speakers' spurts run at once."""
    edges: list[tuple[int, int]] = []
    for s in spurts:
        edges.append((s.start, 1))
        edges.append((s.end, -1))
    # Ends before starts at the same instant: touching spurts do not overlap.
    edges.sort(key=lambda e: (e[0], e[1]))
    regions: list[Interval] = []
    active = 0
    opened = 0
    for at, step in edges:
        before = active
        active += step
        if before < 2 <= active:
            opened = at
        elif before >= 2 > active and at > opened:
            regions.append((opened, at))
    joined: list[Interval] = []
    for start, end in regions:
        if joined and start - joined[-1][1] < MIN_OVERLAP_MS:
            joined[-1] = (joined[-1][0], end)
        else:
            joined.append((start, end))
    return [r for r in joined if r[1] - r[0] >= MIN_OVERLAP_MS]


def _events(spurts: Sequence[Spurt]) -> list[Event]:
    ordered = sorted(spurts, key=lambda s: (s.start, s.speaker))
    events: list[Event] = []
    for b in ordered:
        holders = [
            a
            for a in ordered
            if a.speaker != b.speaker and a.start <= b.start - ONSET_MARGIN_MS < b.start < a.end
        ]
        if not holders:
            continue
        # The floor holder is whoever started most recently.
        a = max(holders, key=lambda s: (s.start, -s.speaker))
        if b.duration < BACKCHANNEL_MS and b.end <= a.end:
            kind = "backchannel"
        elif a.end - b.start <= YIELD_MS and b.end > a.end:
            kind = "interruption"
        else:
            kind = "talk_over"
        events.append(Event(kind, b.start, b.speaker, a.speaker))
    return events


def compute(
    intervals: Mapping[int, Sequence[Interval]],
    duration_ms: int,
    word_counts: Mapping[int, int] | None = None,
) -> Insights:
    """Insights for one recording. `intervals[speaker]` are words or segments."""
    spurts = [s for speaker, ws in intervals.items() for s in spurts_of(speaker, ws)]
    stats = {speaker: SpeakerStats(speaker) for speaker in intervals}
    for s in spurts:
        stats[s.speaker].talk_ms += s.duration
        stats[s.speaker].words += s.words
    if word_counts is not None:
        for speaker, n in word_counts.items():
            if speaker in stats:
                stats[speaker].words = n

    events = _events(spurts)
    backchannel_starts = {(e.by, e.at) for e in events if e.kind == "backchannel"}
    for e in events:
        if e.kind == "interruption":
            stats[e.by].interruptions_made += 1
            stats[e.of].interruptions_received += 1
        elif e.kind == "backchannel":
            stats[e.by].backchannels += 1

    holder: int | None = None
    for s in sorted(spurts, key=lambda s: (s.start, s.speaker)):
        if (s.speaker, s.start) in backchannel_starts or s.speaker == holder:
            continue
        holder = s.speaker
        stats[s.speaker].turns += 1

    # The longest stretch of speech without a pause - not the longest turn,
    # which during sustained overlap spans the gaps while the other person
    # talks and so can exceed the speaker's whole talk time.
    for s in spurts:
        t = stats[s.speaker]
        t.longest_turn_ms = max(t.longest_turn_ms, s.duration)

    total_talk = sum(t.talk_ms for t in stats.values())
    for t in stats.values():
        t.share = t.talk_ms / total_talk if total_talk else 0.0

    overlaps = _overlap_regions(spurts)
    return Insights(
        speakers=stats,
        spurts=sorted(spurts, key=lambda s: (s.start, s.speaker)),
        overlaps=overlaps,
        events=events,
        overlap_ms=sum(b - a for a, b in overlaps),
        speech_ms=_union_length([(s.start, s.end) for s in spurts]),
        duration_ms=duration_ms,
    )
