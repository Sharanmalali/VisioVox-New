"""Off-screen speech, filled from the recording when the diarizer says who (docs/29 §12).

The extractor follows faces, so a person's speech while their face is not
tracked - turned away, off screen, occluded - is missing from their track
(14.4 % of all speech on AMI-AV, §2). Filling it with a voice-only extractor
was worse than the mixture itself (§4: -5.0 vs +3.3 dB in those stretches).

This tests the plain alternative. Face-anchored diarization (§9,
pipeline.diarize.anchor) already says *when* each person speaks, faces or
not. Where it says person k alone is speaking and k's face is not visible,
the recording at that moment is k's voice, so k's track takes it (at the
track's own level, with 20 ms fades). Variants:

  current    the deployed tracks
  fill_solo  fill only where k is the only person diarized as speaking
  fill_any   fill wherever k is diarized as speaking and not visible
  verified   fill_any, piece by piece (<= 4 s), only where the piece's voice
             (ECAPA, the embedder S3 already uses) is closest to k's and at
             least TAU similar - k's voice enrolled from the recording where
             k's face is visible and k alone is diarized as speaking.
             TAU is chosen on the fitting meetings.

Scored per reliable seat against the equalised headset (bench_ami_av rules):
SI-SDR over the frames where k speaks unseen, whole-clip SI-SDRi, and the
precision of the fill (filled frames in which k really speaks). The rule is
chosen on the 20 fitting meetings; the 10 held-out meetings are reported.

    uv run python scripts/bench_ami_offscreen_fill.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "scripts"))

from bench_ami_av import (  # noqa: E402
    MAX_BLEED_DB,
    MIN_SPEECH_S,
    N_SEATS,
    OUT,
    VAD_RATE,
    meetings,
    segment_si_sdr,
)

from eval.metrics import si_sdr  # noqa: E402
from pipeline.diarize import anchor  # noqa: E402
from pipeline.vad import FRAME_SAMPLES  # noqa: E402

FADE = 320  # samples, 20 ms at 16 kHz
PIECE = 4 * VAD_RATE  # verify at most 4 s at a time
MIN_PIECE = 80  # frames: shorter pieces are too short to verify
MIN_ENROL = 2 * VAD_RATE  # frames of clean, visible, solo speech to enrol a voice
MAX_ENROL = 30 * VAD_RATE
TAUS = (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7)
_EMBED: Any = None


def embedder() -> Any:
    global _EMBED
    if _EMBED is None:
        sys.path.insert(0, str(ROOT / "services" / "worker-gpu" / "src"))
        sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
        from visiovox_api.config import Settings
        from worker_gpu.real_pipeline import _load_embedder

        _EMBED = _load_embedder(Settings())
    return _EMBED


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True runs as (start, end) frame indices."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))


def samples_of(x: np.ndarray, frames: np.ndarray) -> np.ndarray:
    keep = np.repeat(frames, FRAME_SAMPLES)[: len(x)]
    return np.asarray(x[: len(keep)][keep], dtype=np.float32)


def unit(v: np.ndarray) -> np.ndarray:
    return np.asarray(v / (np.linalg.norm(v) + 1e-9), dtype=np.float32)


def frames_of(intervals: list[tuple[int, int]], n: int) -> np.ndarray:
    m = np.zeros(n, dtype=bool)
    for a, b in intervals:
        m[max(0, a * VAD_RATE // 1000) : min(n, b * VAD_RATE // 1000)] = True
    return m


def to_samples(frames: np.ndarray, length: int) -> np.ndarray:
    s = np.repeat(frames.astype(np.float32), FRAME_SAMPLES)[:length]
    s = np.pad(s, (0, max(0, length - len(s))))
    # Fades rather than hard switches between the track and the recording.
    kernel = np.ones(FADE, dtype=np.float32) / FADE
    return np.asarray(np.clip(np.convolve(s, kernel, mode="same"), 0.0, 1.0), dtype=np.float32)


def fill(
    track: np.ndarray, mixture: np.ndarray, where: np.ndarray, level_frames: np.ndarray
) -> np.ndarray:
    """`track` with the recording in the frames `where`, at the track's level."""
    if not where.any():
        return track
    s = to_samples(level_frames, len(track)) > 0.5
    rt = float(np.sqrt(np.mean(np.square(track[s])))) if s.any() else 0.0
    rm = float(np.sqrt(np.mean(np.square(mixture[s])))) if s.any() else 0.0
    gain = rt / rm if rt > 0 and rm > 0 else 1.0
    w = to_samples(where, len(track))
    return np.asarray((1 - w) * track + w * gain * mixture, dtype=np.float32)


def score_meeting(work: Path) -> list[dict[str, Any]]:
    eq = np.load(work / "equalised.npz")
    av = np.load(work / "av_tracks.npz")
    inputs = json.loads((work / "insights_inputs.json").read_text())
    refs, mixture, masks = eq["refs"], eq["mixture"], eq["masks"]
    estimates = np.asarray(av["estimates"], dtype=np.float32)
    n = masks.shape[1]
    visible = np.zeros((N_SEATS, n), dtype=bool)
    vis = np.asarray(av["visible"], dtype=bool)
    visible[:, : min(n, vis.shape[1])] = vis[:, :n]
    duration_ms = n * 1000 // VAD_RATE

    segments = [(int(a), int(b), str(label)) for a, b, label in inputs["pyannote_segments"]]
    speech = [[(int(a), int(b)) for a, b in ws] for ws in inputs["visiovox"]]
    anchored = anchor(segments, speech, duration_ms)
    said = np.stack([frames_of(list(p), n) for p in anchored])  # (seats, frames)
    talking = said.sum(axis=0)

    embed = embedder()
    voice: dict[int, np.ndarray] = {}
    for j in range(N_SEATS):
        clean = said[j] & visible[j] & (talking == 1)
        if clean.sum() >= MIN_ENROL:
            idx = np.flatnonzero(clean)[:MAX_ENROL]
            only = np.zeros(n, dtype=bool)
            only[idx] = True
            voice[j] = unit(embed(samples_of(mixture, only)))
    # Each candidate piece: who it sounds most like, and how much.
    pieces: dict[int, list[tuple[int, int, int, float]]] = {}
    for k in range(N_SEATS):
        pieces[k] = []
        if k not in voice:
            continue
        for a, b in runs(said[k] & ~visible[k]):
            for start in range(a, b, PIECE):
                end = min(b, start + PIECE)
                if end - start < MIN_PIECE:
                    continue
                v = unit(embed(mixture[start * FRAME_SAMPLES : end * FRAME_SAMPLES]))
                sims = {j: float(v @ e) for j, e in voice.items()}
                best = max(sims, key=lambda j: sims[j])
                pieces[k].append((start, end, best, sims[k]))

    rows = []
    for k in range(N_SEATS):
        ref = refs[k]
        active = masks[k]
        speech_s = float(active.sum()) / VAD_RATE
        bleed = float(eq["bleed_db"][k])
        reliable = speech_s >= MIN_SPEECH_S and np.isfinite(bleed) and bleed <= MAX_BLEED_DB
        has = bool(np.any(estimates[k]))
        if not reliable or not has:
            continue
        hidden = active & ~visible[k]
        level = active & visible[k]
        verified = {}
        for tau in TAUS:
            m = np.zeros(n, dtype=bool)
            for a, b, best, sim in pieces[k]:
                if best == k and sim >= tau:
                    m[a:b] = True
            verified[tau] = m
        for tau, where in verified.items():
            rows.append(
                {
                    "seat": k,
                    "system": f"verified_{tau}_fill",
                    "filled_s": float(where.sum()) / VAD_RATE,
                    "precise_s": float((where & active).sum()) / VAD_RATE,
                    "hidden_recalled_s": float((where & hidden).sum()) / VAD_RATE,
                }
            )
        variants = {
            **{
                f"verified_{tau}": fill(estimates[k], mixture, w, level)
                for tau, w in verified.items()
            },
            "current": estimates[k],
            "fill_solo": fill(estimates[k], mixture, said[k] & ~visible[k] & (talking == 1), level),
            "fill_any": fill(estimates[k], mixture, said[k] & ~visible[k], level),
        }
        for name, where in (
            ("fill_solo", said[k] & ~visible[k] & (talking == 1)),
            ("fill_any", said[k] & ~visible[k]),
        ):
            rows.append(
                {
                    "seat": k,
                    "system": name + "_fill",
                    "filled_s": float(where.sum()) / VAD_RATE,
                    "precise_s": float((where & active).sum()) / VAD_RATE,
                    "hidden_recalled_s": float((where & hidden).sum()) / VAD_RATE,
                }
            )
        mix_whole = si_sdr(mixture, ref)
        for name, track in variants.items():
            rows.append(
                {
                    "seat": k,
                    "system": name,
                    "hidden_s": float(hidden.sum()) / VAD_RATE,
                    "speech_s": speech_s,
                    "si_sdr_hidden": segment_si_sdr(track, ref, hidden)
                    if hidden.sum() >= VAD_RATE
                    else float("nan"),
                    "si_sdri": si_sdr(track, ref) - mix_whole,
                }
            )
    return rows


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    systems = ["current", "fill_solo", "fill_any", *(f"verified_{t}" for t in TAUS)]
    for system in systems:
        r = [x for x in rows if x["system"] == system]
        hid = np.array([x["si_sdr_hidden"] for x in r if np.isfinite(x["si_sdr_hidden"])])
        sdri = np.array([x["si_sdri"] for x in r])
        out[system] = {
            "seats": len(r),
            "seats_with_hidden_speech": len(hid),
            "si_sdr_hidden_median": float(np.median(hid)) if hid.size else float("nan"),
            "si_sdri_mean": float(np.mean(sdri)) if sdri.size else float("nan"),
        }
    for system in systems[1:]:
        f = [x for x in rows if x["system"] == system + "_fill"]
        filled = sum(x["filled_s"] for x in f)
        out[system]["filled_s"] = filled
        out[system]["precision"] = (
            sum(x["precise_s"] for x in f) / filled if filled else float("nan")
        )
        hidden_total = sum(x["hidden_s"] for x in rows if x["system"] == "current")
        out[system]["hidden_recall"] = (
            sum(x["hidden_recalled_s"] for x in f) / hidden_total if hidden_total else float("nan")
        )
    # Paired: does the fill help the same seats?
    cur = {(x["meeting"], x["seat"]): x for x in rows if x["system"] == "current"}
    for system in systems[1:]:
        diffs = [
            x["si_sdri"] - cur[(x["meeting"], x["seat"])]["si_sdri"]
            for x in rows
            if x["system"] == system
        ]
        out[system]["si_sdri_change_mean"] = float(np.mean(diffs)) if diffs else float("nan")
        out[system]["seats_better"] = int(sum(d > 0 for d in diffs))
    return out


def main() -> int:
    rows: list[dict[str, Any]] = []
    for split, meeting, _ in meetings():
        work = OUT / meeting.name
        if not (work / "insights_inputs.json").is_file():
            continue
        for r in score_meeting(work):
            r.update(meeting=meeting.name, split=split)
            rows.append(r)
    result = {
        "fitting": summarise([r for r in rows if r["split"] == "train"]),
        "held_out": summarise([r for r in rows if r["split"] == "eval"]),
    }
    (OUT / "offscreen_fill.json").write_text(
        json.dumps({**result, "rows": rows}, indent=1, default=float)
    )
    sys.stdout.write(json.dumps(result, indent=1, default=float) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
