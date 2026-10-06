"""Filling the off-screen gaps: AV-MossFormer2 where the face is seen, SEAVE where it is not.

A lip-reading extractor has nothing to work from when the speaker turns away,
walks to the whiteboard, or leaves the frame - and real meetings do all three
(measured on AMI-AV: see bench_ami_av.py, `coverage`). Production today
returns silence for those stretches. Words are lost.

SEAVE needs no face, only a voice print. The voice print comes from the same
video: the AV-MossFormer2 output during the stretches where the face *was*
tracked. No enrolment recording, no oracle, nothing outside the clip.

Per seat, five outputs are scored against the equal-level headset:

    av              AV-MossFormer2 through production (silence when unseen)
    seave_self      SEAVE over the whole clip, self-enrolled
    hybrid_self     av where the face is tracked, seave_self elsewhere
    seave_oracle    SEAVE enrolled from the clean headset (upper bound)
    hybrid_oracle   av where tracked, seave_oracle elsewhere

The headline is `hidden`: the frames where the person speaks and their face is
not tracked. That is the speech production currently drops.

Reads the per-meeting caches written by bench_ami_av.py.

    uv run python scripts/bench_ami_gapfill.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
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
    RATE,
    VAD_RATE,
    meetings,
    segment_si_sdr,
    segment_si_sdri,
)

from eval.metrics import si_sdr, silence_leakage_db, stoi  # noqa: E402
from pipeline.vad import FRAME_SAMPLES  # noqa: E402

CHUNK_S = 4.0  # SEAVE's training length
ENROL_MAX_S = 30.0
ENROL_MIN_S = 2.0
XFADE = 160  # 10 ms crossfade at a switch between sources
ACTIVE_DB = -30.0  # self-enrolment keeps frames within this of the output's loud level


def load_seave(checkpoint: Path, device: str) -> Any:
    import torch

    from models.seave import Seave, SeaveConfig

    state = torch.load(checkpoint, map_location=device, weights_only=False)
    model = Seave(SeaveConfig()).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    return model


def seave_extract(
    model: Any, mixture: np.ndarray, embedding: np.ndarray, device: str
) -> np.ndarray:
    """Whole clip in 4 s windows, 50 % overlap, Hann overlap-add."""
    import torch

    win = int(CHUNK_S * RATE)
    hop = win // 2
    n = len(mixture)
    padded = np.pad(mixture, (hop, hop + win))
    out = np.zeros_like(padded)
    norm = np.zeros_like(padded)
    taper = np.hanning(win).astype(np.float32)
    starts = list(range(0, len(padded) - win + 1, hop))
    emb = torch.from_numpy(embedding.astype(np.float32))[None].to(device)
    one = torch.ones(1, device=device)
    with torch.no_grad():
        for batch in range(0, len(starts), 16):
            idx = starts[batch : batch + 16]
            x = torch.from_numpy(np.stack([padded[s : s + win] for s in idx])).to(device)
            y = model(x, emb.expand(len(idx), -1), one.expand(len(idx)))["estimate"]
            y = y.cpu().numpy()
            for s, seg in zip(idx, y, strict=True):
                out[s : s + win] += seg[:win] * taper
                norm[s : s + win] += taper
    result = out / np.maximum(norm, 1e-3)
    return np.asarray(result[hop : hop + n], dtype=np.float32)


def splice(primary: np.ndarray, fill: np.ndarray, use_primary: np.ndarray) -> np.ndarray:
    """primary where use_primary (10 ms frames), fill elsewhere, short crossfades."""
    gate = np.repeat(use_primary.astype(np.float32), FRAME_SAMPLES)
    gate = np.pad(gate, (0, max(0, len(primary) - len(gate))))[: len(primary)]
    kernel = np.ones(XFADE, dtype=np.float32) / XFADE
    smooth = np.convolve(gate, kernel, mode="same")
    return np.asarray(smooth * primary + (1 - smooth) * fill, dtype=np.float32)


def loud_frames(x: np.ndarray) -> np.ndarray:
    n = len(x) // FRAME_SAMPLES
    level = 10 * np.log10((x[: n * FRAME_SAMPLES].reshape(n, FRAME_SAMPLES) ** 2).mean(1) + 1e-12)
    return np.asarray(level > np.percentile(level, 95) + ACTIVE_DB, dtype=bool)


def gather(x: np.ndarray, frames: np.ndarray, max_s: float) -> np.ndarray:
    idx = np.flatnonzero(frames)[: int(max_s * VAD_RATE)]
    if len(idx) == 0:
        return np.zeros(0, dtype=np.float32)
    take = (idx[:, None] * FRAME_SAMPLES + np.arange(FRAME_SAMPLES)[None, :]).reshape(-1)
    return np.asarray(x[take], dtype=np.float32)


def unit(v: np.ndarray) -> np.ndarray:
    return np.asarray(v / (np.linalg.norm(v) + 1e-9), dtype=np.float32)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkpoint", type=Path, default=Path.home() / "runs" / "c3" / "best.pt")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    model = load_seave(args.checkpoint, args.device)
    encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(Path.home() / "models" / "ecapa"),
        run_opts={"device": args.device},
    )

    def embed(x: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            v = encoder.encode_batch(torch.from_numpy(x)[None].to(args.device))
        return unit(v.squeeze().cpu().numpy())

    rows: list[dict[str, Any]] = []
    todo = meetings()[: args.limit or None]
    for i, (split, meeting, _start) in enumerate(todo, 1):
        work = OUT / meeting.name
        if not (work / "av_tracks.npz").exists():
            print(f"[{i}/{len(todo)}] {meeting.name}: no AV cache yet, skipped", flush=True)
            continue
        t0 = time.time()
        eq = dict(np.load(work / "equalised.npz"))
        av = dict(np.load(work / "av_tracks.npz"))
        refs, mixture, masks = eq["refs"], eq["mixture"], eq["masks"].astype(bool)
        n = len(mixture)
        others = masks.sum(axis=0)
        for k in range(N_SEATS):
            active = masks[k]
            visible = np.zeros_like(active)
            m = min(len(active), av["visible"].shape[1])
            visible[:m] = av["visible"][k, :m]
            hidden = active & ~visible
            overlap = active & ((others - active) >= 1)
            bleed = float(eq["bleed_db"][k])
            speech_s = float(active.sum()) / VAD_RATE
            reliable = speech_s >= MIN_SPEECH_S and np.isfinite(bleed) and bleed <= MAX_BLEED_DB

            av_est = av["estimates"][k][:n]
            # Self-enrolment: the AV output where the face is tracked and the
            # output itself is loud. Nothing from the headset.
            sample = gather(av_est, visible & loud_frames(av_est)[: len(visible)], ENROL_MAX_S)
            outputs: dict[str, np.ndarray] = {"av": av_est}
            self_emb = None
            if len(sample) >= ENROL_MIN_S * RATE:
                self_emb = embed(sample)
                seave_self = seave_extract(model, mixture, self_emb, args.device)
                outputs["seave_self"] = seave_self
                outputs["hybrid_self"] = splice(av_est, seave_self, visible)
            # Oracle: the clean headset, only where this person alone speaks.
            alone = active & (others == 1)
            clean = gather(
                refs[k], alone if alone.sum() >= ENROL_MIN_S * VAD_RATE else active, ENROL_MAX_S
            )
            oracle_emb = embed(clean) if len(clean) >= RATE else None
            if oracle_emb is not None:
                seave_oracle = seave_extract(model, mixture, oracle_emb, args.device)
                outputs["seave_oracle"] = seave_oracle
                outputs["hybrid_oracle"] = splice(av_est, seave_oracle, visible)

            # Kept for the ASR benchmark (bench_ami_asr.py).
            kept: dict[str, Any] = {
                s: o.astype(np.float16) for s, o in outputs.items() if s != "av"
            }
            np.savez_compressed(work / f"gapfill_seat{k}.npz", **kept)
            ref = refs[k]
            for system, est in outputs.items():
                has = bool(np.any(est))
                rows.append(
                    {
                        "meeting": meeting.name,
                        "split": split,
                        "speaker": k,
                        "system": system,
                        "reliable": reliable,
                        "speech_s": speech_s,
                        "hidden_s": float(hidden.sum()) / VAD_RATE,
                        "coverage": float((active & visible).sum() / max(active.sum(), 1)),
                        "enrol_cos": float(self_emb @ oracle_emb)
                        if self_emb is not None and oracle_emb is not None
                        else float("nan"),
                        "si_sdri": si_sdr(est, ref) - si_sdr(mixture, ref)
                        if has
                        else float("-inf"),
                        "si_sdri_active": segment_si_sdri(est, ref, mixture, active),
                        "si_sdri_overlap": segment_si_sdri(est, ref, mixture, overlap),
                        "si_sdri_hidden": segment_si_sdri(est, ref, mixture, hidden),
                        "si_sdr_hidden": segment_si_sdr(est, ref, hidden),
                        "mix_si_sdr_hidden": segment_si_sdr(mixture, ref, hidden),
                        "leak_db": silence_leakage_db(est, active, FRAME_SAMPLES)
                        if has
                        else float("nan"),
                        "leak_db_mixture": silence_leakage_db(mixture, active, FRAME_SAMPLES),
                        "stoi": stoi(est, ref) if has else float("nan"),
                        "stoi_mixture": stoi(mixture, ref),
                    }
                )
        done = [r for r in rows if r["meeting"] == meeting.name and r["reliable"]]

        def mean_of(system: str, key: str, rs: list[dict[str, Any]] = done) -> float:
            v = [r[key] for r in rs if r["system"] == system and np.isfinite(r[key])]
            return float(np.mean(v)) if v else float("nan")

        print(
            f"[{i}/{len(todo)}] {meeting.name}: hidden SI-SDRi self {mean_of('hybrid_self', 'si_sdri_hidden'):+.2f} "
            f"oracle {mean_of('hybrid_oracle', 'si_sdri_hidden'):+.2f}; whole av {mean_of('av', 'si_sdri'):+.2f} "
            f"hybrid {mean_of('hybrid_self', 'si_sdri'):+.2f} seave {mean_of('seave_self', 'si_sdri'):+.2f}; "
            f"{time.time() - t0:.0f}s",
            flush=True,
        )
        (OUT / "gapfill.json").write_text(json.dumps(rows, indent=1, default=float))
    print("GAPFILL_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
