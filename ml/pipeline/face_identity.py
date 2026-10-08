"""Face identity for face tracks: is this the same person as that track?

S3 merges face tracks into people. It used to decide by voice alone (ECAPA on
each track's extracted audio), and measured on AMI-AV that splits people: 120
seats came out as 152 people, 21 seats in several pieces. An extracted track
carries other people's speech a few dB down, which blurs a voice print; a face
crop carries nobody else. The face is the stronger identity cue, and the
extractor has already cropped it.

Embeddings are ArcFace (insightface buffalo_sc, MobileFaceNet) on a handful of
frames sampled across each track's crop video, averaged and normalised. They
live only in memory for the job (invariant 3 / ADR-0008).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

BUNDLE = "buffalo_sc"
SAMPLES_PER_TRACK = 8
MIN_DET_SCORE = 0.5


def load_embedder() -> Callable[[np.ndarray], np.ndarray | None]:
    """A function from a BGR face crop to a unit ArcFace vector (None if no face)."""
    from insightface.app import FaceAnalysis

    app: Any = FaceAnalysis(
        name=BUNDLE,
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(224, 224))

    def embed(crop: np.ndarray) -> np.ndarray | None:
        faces = [f for f in app.get(crop) if float(f.det_score) >= MIN_DET_SCORE]
        if not faces:
            return None
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        v = np.asarray(face.normed_embedding, dtype=np.float64)
        return v / (np.linalg.norm(v) + 1e-12)

    return embed


def track_vector(
    crop_video: Path,
    embed: Callable[[np.ndarray], np.ndarray | None],
    samples: int = SAMPLES_PER_TRACK,
) -> np.ndarray | None:
    """Mean face embedding over frames sampled evenly through a track's crops."""
    import cv2

    cap = cv2.VideoCapture(str(crop_video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if n <= 0:
        cap.release()
        return None
    vectors = []
    for i in np.linspace(0, n - 1, num=min(samples, n)).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, frame = cap.read()
        if not ok:
            continue
        v = embed(frame)
        if v is not None:
            vectors.append(v)
    cap.release()
    if not vectors:
        return None
    mean = np.mean(vectors, axis=0)
    return np.asarray(mean / (np.linalg.norm(mean) + 1e-12), dtype=np.float64)


def crop_video_for(track_wav: str) -> Path:
    """The runner writes a track's crops next to its estimate: est_3.wav <-> 00003.avi."""
    wav = Path(track_wav)
    index = int(wav.stem.split("_")[-1])
    return wav.parent / f"{index:05d}.avi"
