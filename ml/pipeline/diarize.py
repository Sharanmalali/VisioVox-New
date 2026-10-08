"""Face-anchored diarization: *when* from pyannote, *who* from the faces.

Speaker insights need to know when each person is speaking. The separated
tracks answer "who" well - each is tied to a face - but their word timings
answer "when" poorly: every track carries the room a few dB down, and the
cross-track dedup that makes the captions readable removes words exactly where
people overlap. An audio-only diarizer (pyannote 3.1) has the opposite
profile: good boundaries and overlap detection, but anonymous labels that
nothing in a product can map to faces.

So the two are joined. pyannote segments the mixture; each anonymous label is
paired with one person, by how much it overlaps their separated, captioned
speech. A label that cannot be paired is left out rather than guessed. Measured on AMI-AV in docs/29 §9 (scripts/bench_ami_insights.py, the
`face_anchored` system), which calls `anchor` below.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"
FRAME_MS = 10
SAMPLE_RATE = 16_000

Interval = tuple[int, int]
Segment = tuple[int, int, str]


def _mask(intervals: Sequence[Interval], n: int) -> np.ndarray:
    mask = np.zeros(n, dtype=bool)
    for a, b in intervals:
        mask[max(a, 0) // FRAME_MS : -(-b // FRAME_MS)] = True
    return mask


def anchor(
    segments: Sequence[Segment],
    speech: Sequence[Sequence[Interval]],
    duration_ms: int,
) -> list[list[Interval]]:
    """Diarized segments regrouped per person.

    `speech[k]` is person k's speech (their caption word timings). Labels and
    people are paired one to one, maximising the total time each label
    overlaps its person's speech (Hungarian assignment) - chosen over "each
    label to its best person" on the AMI fitting meetings
    (scripts/select_insights_design.py), because two labels landing on one
    face moved whole stretches of talk to the wrong person. A label left
    unpaired, or paired with nobody's speech, is left out rather than guessed.
    Returns one interval list per person, in `speech` order.
    """
    from scipy.optimize import linear_sum_assignment

    out: list[list[Interval]] = [[] for _ in speech]
    if not speech or not segments:
        return out
    n = max(1, -(-duration_ms // FRAME_MS))
    people = np.stack([_mask(s, n) for s in speech])
    by_label: dict[str, list[Interval]] = {}
    for a, b, label in segments:
        by_label.setdefault(label, []).append((a, b))
    labels = list(by_label)
    overlap = np.stack([(people & _mask(by_label[k], n)).sum(1) for k in labels])
    rows, cols = linear_sum_assignment(-overlap)
    for i, j in zip(rows, cols, strict=True):
        if overlap[i, j] > 0:
            out[int(j)].extend(sorted(by_label[labels[int(i)]]))
    return out


def load_diarizer(token: str, device: str) -> Any:
    """pyannote's pipeline on `device`. Raises if the model is not available."""
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(DIARIZATION_MODEL, token=token)
    if pipeline is None:
        raise PermissionError(f"pyannote returned no pipeline for {DIARIZATION_MODEL}")
    pipeline.to(torch.device(device))
    return pipeline


def diarize(audio: np.ndarray, diarizer: Any) -> list[Segment]:
    """Anonymous (start_ms, end_ms, label) segments of a 16 kHz mono mixture."""
    import torch

    waveform = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))[None]
    result = diarizer({"waveform": waveform, "sample_rate": SAMPLE_RATE})
    # pyannote 4.x wraps the annotation; 3.x returned it directly.
    annotation: Any = getattr(result, "speaker_diarization", result)
    return [
        (round(seg.start * 1000), round(seg.end * 1000), str(label))
        for seg, _, label in annotation.itertracks(yield_label=True)
    ]
