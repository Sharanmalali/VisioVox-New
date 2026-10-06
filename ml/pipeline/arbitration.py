"""Cross-stream arbitration: who is speaking, decided by the extracted streams together.

A meeting is extracted once per person, every extractor reading the same
mixture. Each extractor on its own is unreliable about *when* its person
speaks: measured on AMI-AV, AV-MossFormer2 keeps the room only ~3 dB down
while its person is silent, so every per-person transcript contains most of
the meeting (cpWER > 100 %, almost all insertions).

But the streams are not independent. A stretch of speech belongs to one person,
and the extractor for that person keeps more of it than the others do. So the
question "is person k speaking now?" is answered by comparing stream k with
the other streams, not with a fixed threshold.

For each person k and each 10 ms frame:

    r_k(t) = smoothed energy of stream k / smoothed energy of the mixture   (dB)

Stream k is kept where

    r_k(t) >= max_j r_j(t) - margin_db      (competitive: near the strongest)
    r_k(t) >= floor_db                       (absolute: kept a real share)
    the mixture is not silent

then dilated by `hold_s` so word edges survive, and applied with short ramps.
Three parameters, fitted on the AMI training meetings only (fit_arbitration.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

FRAME = 160  # 10 ms at 16 kHz
RATE = 16_000


@dataclass(frozen=True)
class ArbitrationConfig:
    margin_db: float = 6.0
    floor_db: float = -10.0
    smooth_s: float = 0.15
    hold_s: float = 0.15
    silence_db: float = 35.0  # mixture frames this far below its loud level are silence
    ramp_s: float = 0.01

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def _frame_energy(x: np.ndarray, n: int) -> np.ndarray:
    x = np.pad(x[: n * FRAME], (0, max(0, n * FRAME - len(x))))
    return np.asarray((x.reshape(n, FRAME).astype(np.float64) ** 2).mean(axis=1))


def _smooth(e: np.ndarray, frames: int) -> np.ndarray:
    if frames <= 1:
        return e
    kernel = np.ones(frames) / frames
    return np.convolve(e, kernel, mode="same")


def _dilate(mask: np.ndarray, frames: int) -> np.ndarray:
    if frames <= 0:
        return mask
    kernel = np.ones(2 * frames + 1)
    return np.convolve(mask.astype(float), kernel, mode="same") > 0


def retention(streams: np.ndarray, mixture: np.ndarray, smooth_s: float) -> np.ndarray:
    """(n_streams, frames) r_k(t) in dB: each stream's share of the mixture energy.

    -inf for a stream that is all zeros (nobody tracked).
    """
    n = len(mixture) // FRAME
    smooth = max(1, round(smooth_s * RATE / FRAME))
    mix = _smooth(_frame_energy(mixture, n), smooth) + 1e-12
    ratios = np.full((streams.shape[0], n), -np.inf)
    for k, s in enumerate(streams):
        if np.any(s):
            ratios[k] = 10 * np.log10(_smooth(_frame_energy(s, n), smooth) / mix + 1e-12)
    return ratios


def activity(
    streams: np.ndarray, mixture: np.ndarray, cfg: ArbitrationConfig | None = None
) -> np.ndarray:
    """(n_streams, frames) bool: where each stream is judged to be its person speaking.

    A stream that is all zeros (nobody tracked) is never active.
    """
    cfg = cfg or ArbitrationConfig()
    n = len(mixture) // FRAME
    smooth = max(1, round(cfg.smooth_s * RATE / FRAME))
    mix = _smooth(_frame_energy(mixture, n), smooth) + 1e-12
    ratios = retention(streams, mixture, cfg.smooth_s)
    best = ratios.max(axis=0)
    loud = 10 * np.log10(np.percentile(mix, 95))
    voiced = 10 * np.log10(mix) > loud - cfg.silence_db
    active = (ratios >= best - cfg.margin_db) & (ratios >= cfg.floor_db) & voiced[None, :]
    hold = round(cfg.hold_s * RATE / FRAME)
    return np.stack([_dilate(a, hold) for a in active])


def apply(
    streams: np.ndarray, mixture: np.ndarray, cfg: ArbitrationConfig | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Gate each stream to where it is judged active. Returns (gated, activity)."""
    cfg = cfg or ArbitrationConfig()
    act = activity(streams, mixture, cfg)
    ramp = max(1, int(cfg.ramp_s * RATE))
    kernel = np.ones(ramp) / ramp
    out = np.zeros_like(streams)
    for k in range(streams.shape[0]):
        gate = np.repeat(act[k].astype(np.float32), FRAME)
        gate = np.pad(gate, (0, max(0, streams.shape[1] - len(gate))))[: streams.shape[1]]
        gate = np.convolve(gate, kernel, mode="same")
        out[k] = streams[k] * gate
    return out, act
