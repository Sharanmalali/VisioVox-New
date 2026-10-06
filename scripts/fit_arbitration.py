"""Fit the cross-stream arbitration parameters on the AMI *training* meetings.

The target is AMI's hand-aligned word activity: for every seat with a stream,
how well does arbitration recover when that person is speaking? Scored as
frame-level F1, averaged over seats. The 10 TS evaluation meetings are never
read here; the chosen parameters are frozen to arbitration.json and the
evaluation scripts use them unchanged.

    uv run python scripts/fit_arbitration.py --streams av
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "scripts"))

from bench_ami_av import OUT, meetings  # noqa: E402

from pipeline.arbitration import ArbitrationConfig, activity  # noqa: E402

GRID = {
    "margin_db": [2.0, 4.0, 6.0, 9.0, 12.0],
    "floor_db": [-20.0, -15.0, -10.0, -6.0, -3.0],
    "smooth_s": [0.05, 0.15, 0.3],
    "hold_s": [0.0, 0.15, 0.3],
}


def load(split: str, streams: str) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    data = []
    for sp, meeting, _start in meetings():
        work = OUT / meeting.name
        if sp != split or not (work / "av_tracks.npz").exists():
            continue
        eq = np.load(work / "equalised.npz")
        av = np.load(work / "av_tracks.npz")
        est = np.array(av["estimates"], dtype=np.float32)
        if streams == "hybrid":
            for k in range(est.shape[0]):
                g = work / f"gapfill_seat{k}.npz"
                if g.exists():
                    z = np.load(g)
                    if "hybrid_self" in z.files:
                        est[k] = z["hybrid_self"].astype(np.float32)
        data.append((est, eq["mixture"], eq["masks"].astype(bool)))
    return data


def f1(data: list[tuple[np.ndarray, np.ndarray, np.ndarray]], cfg: ArbitrationConfig) -> float:
    scores = []
    for est, mix, truth in data:
        act = activity(est, mix, cfg)
        n = min(act.shape[1], truth.shape[1])
        for k in range(est.shape[0]):
            if not np.any(est[k]):
                continue
            a, t = act[k, :n], truth[k, :n]
            tp = float((a & t).sum())
            denom = float(a.sum() + t.sum())
            scores.append(2 * tp / denom if denom else 1.0)
    return float(np.mean(scores)) if scores else float("nan")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--streams", default="av", choices=("av", "hybrid"))
    args = ap.parse_args(argv)

    train = load("train", args.streams)
    print(f"fitting on {len(train)} training meetings ({args.streams} streams)")
    base = ArbitrationConfig()
    no_gate = ArbitrationConfig(margin_db=1e9, floor_db=-1e9, hold_s=0.0)
    print(f"  ungated (every stream always on, voiced frames): F1 {f1(train, no_gate):.3f}")
    best: tuple[float, ArbitrationConfig] = (-1.0, base)
    keys = list(GRID)
    for values in itertools.product(*(GRID[k] for k in keys)):
        cfg = ArbitrationConfig(**dict(zip(keys, values, strict=True)))
        score = f1(train, cfg)
        if score > best[0]:
            best = (score, cfg)
    print(f"  best: F1 {best[0]:.3f} with {best[1].as_dict()}")
    out = OUT / f"arbitration-{args.streams}.json"
    out.write_text(
        json.dumps(
            {"train_f1": best[0], "config": best[1].as_dict(), "meetings": len(train)}, indent=1
        )
    )
    print(f"  frozen to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
