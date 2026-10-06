"""AMI manual word alignments: who is speaking when, and what they said.

The headsets alone cannot answer "who is speaking": every headset hears the
whole room, and once their gains are equalised the bleed is loud enough to
pass an energy test. AMI ships hand-corrected, time-aligned words for every
participant, keyed by NXT agent letter. This maps them onto headset channels
via corpusResources/meetings.xml.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from defusedxml.ElementTree import parse as parse_xml

UNPACKED = Path.home() / "data" / "ami" / "annotations" / "unpacked"
NITE = "{http://nite.sourceforge.net/}"
FRAME_RATE = 100  # 10 ms, matches pipeline.vad
PAD_S = 0.05  # word boundaries are forced-aligned; allow for onset/offset


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


def agents_by_channel(meeting: str) -> dict[int, str]:
    root = parse_xml(UNPACKED / "corpusResources" / "meetings.xml").getroot()
    for m in root.iter("meeting"):
        if m.get("observation") == meeting:
            return {int(s.get("channel")): s.get("nxt_agent") for s in m.iter("speaker")}
    raise KeyError(meeting)


def words(
    meeting: str, channel: int, start_s: float = 0.0, end_s: float = float("inf")
) -> list[Word]:
    """Spoken words of one channel inside [start_s, end_s), times relative to start_s."""
    agent = agents_by_channel(meeting)[channel]
    root = parse_xml(UNPACKED / "words" / f"{meeting}.{agent}.words.xml").getroot()
    out: list[Word] = []
    for w in root.iter("w"):
        if w.get("punc") == "true" or w.get("starttime") is None or w.get("endtime") is None:
            continue
        a, b = float(w.get("starttime")), float(w.get("endtime"))
        if b <= start_s or a >= end_s:
            continue
        out.append(Word(a - start_s, b - start_s, (w.text or "").strip()))
    return out


def activity(meeting: str, start_s: float, duration_s: float, n_channels: int = 4) -> np.ndarray:
    """(n_channels, frames) bool at 10 ms: inside an aligned word (padded)."""
    n = round(duration_s * FRAME_RATE)
    masks = np.zeros((n_channels, n), dtype=bool)
    for ch in range(n_channels):
        for w in words(meeting, ch, start_s, start_s + duration_s):
            a = max(int((w.start - PAD_S) * FRAME_RATE), 0)
            b = min(int(np.ceil((w.end + PAD_S) * FRAME_RATE)), n)
            masks[ch, a:b] = True
    return masks
