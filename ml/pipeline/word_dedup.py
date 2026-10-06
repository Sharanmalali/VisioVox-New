"""Cross-stream word deduplication: one copy of each word, in the right person's track.

Two ways to caption a multi-person video fail in opposite regimes, both measured:

  * Transcribe each person's extracted track. In real meetings every track
    keeps the room a few dB down, Whisper writes that cross-talk down, and
    every caption track contains most of the meeting (AMI-AV: 110 % seat-
    attributed WER, mostly insertions).
  * Transcribe the mixture once and attribute each word by which track keeps
    it. In heavy overlap Whisper transcribes only the dominant voice, and the
    other person's words vanish (VoxCeleb2 two-face videos).

Cross-talk and genuine overlap leave different traces in the per-track
transcripts. Cross-talk is the *same* word appearing in several tracks at the
same moment; genuine overlap is *different* words. So: transcribe each track,
find words that recur across tracks at the same time, keep each recurring word
only in the track that keeps the most of the mixture's energy over it, and
keep every word that appears once.

Duplicate rule, fixed before evaluation: identical normalised text and word
midpoints within `window_s`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from .arbitration import FRAME, RATE, retention

DEFAULT_WINDOW_S = 0.5
DEFAULT_SMOOTH_S = 0.05  # retention smoothing, fitted on AMI training meetings


@dataclass(frozen=True)
class StreamWord:
    stream: int
    index: int  # position within its stream
    start: float  # seconds
    end: float
    key: str  # normalised text used for matching

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2


def _alnum(text: str) -> str:
    """Default match key: lower-case letters and digits only."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _word_retention(ratios: np.ndarray, start: float, end: float) -> np.ndarray:
    last = ratios.shape[1] - 1
    a = min(max(int(start * RATE / FRAME), 0), last)
    b = max(min(int(np.ceil(end * RATE / FRAME)), last + 1), a + 1)
    return np.asarray(ratios[:, a:b].mean(axis=1))


def deduplicate(
    words: Sequence[Sequence[tuple[float, float, str]]],
    streams: np.ndarray,
    mixture: np.ndarray,
    normalise: Callable[[str], str] | None = None,
    window_s: float = DEFAULT_WINDOW_S,
    smooth_s: float = DEFAULT_SMOOTH_S,
) -> list[list[bool]]:
    """Which words to keep: `keep[k][i]` for word i of stream k.

    `words[k]` is stream k's transcript as (start_s, end_s, text). A word with
    no copy in another stream is always kept. A group of copies (same text,
    midpoints within `window_s`, at most one per stream) keeps only the copy
    whose stream retains most of the mixture over that word.
    """
    normalise = normalise or _alnum
    ratios = retention(streams, mixture, smooth_s)
    keep = [[True] * len(ws) for ws in words]
    flat = [
        StreamWord(k, i, s, e, normalise(t))
        for k, ws in enumerate(words)
        for i, (s, e, t) in enumerate(ws)
    ]
    flat = [w for w in flat if w.key]
    flat.sort(key=lambda w: w.mid)
    claimed: set[tuple[int, int]] = set()
    for i, w in enumerate(flat):
        if (w.stream, w.index) in claimed:
            continue
        group = [w]
        seen = {w.stream}
        for v in flat[i + 1 :]:
            if v.mid - w.mid > window_s:
                break
            if v.key == w.key and v.stream not in seen and (v.stream, v.index) not in claimed:
                group.append(v)
                seen.add(v.stream)
        if len(group) == 1:
            continue
        for v in group:
            claimed.add((v.stream, v.index))
        start = min(v.start for v in group)
        end = max(v.end for v in group)
        owner_scores = _word_retention(ratios, start, end)
        winner = max(group, key=lambda v: owner_scores[v.stream])
        for v in group:
            if v is not winner:
                keep[v.stream][v.index] = False
    return keep
