"""Does cross-stream arbitration make the per-person audio tracks better?

The audio product is one switchable track per person. A track should carry its
person and go quiet when they are not speaking. This scores AV-MossFormer2's
tracks before and after arbitration (parameters frozen on the training
meetings) on the held-out meetings, per reliable speaker, paired:

    leak_db   track level where the person is silent, relative to where they
              speak (lower = quieter when it should be)
    si_sdri   whole clip
    stoi      whole clip

    uv run python scripts/bench_ami_arbitration_signal.py
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

from bench_ami_av import MAX_BLEED_DB, MIN_SPEECH_S, N_SEATS, OUT, VAD_RATE, meetings  # noqa: E402

from eval.harness import bootstrap_ci  # noqa: E402
from eval.metrics import si_sdr, silence_leakage_db, stoi  # noqa: E402
from pipeline.arbitration import ArbitrationConfig, apply  # noqa: E402
from pipeline.vad import FRAME_SAMPLES  # noqa: E402


def main() -> int:
    cfg = ArbitrationConfig(**json.loads((OUT / "arbitration-av.json").read_text())["config"])
    rows: list[dict[str, Any]] = []
    for split, meeting, _start in meetings():
        if split != "eval":
            continue
        eq = np.load(OUT / meeting.name / "equalised.npz")
        av = np.load(OUT / meeting.name / "av_tracks.npz")
        mix, refs, masks = eq["mixture"], eq["refs"], eq["masks"].astype(bool)
        est = np.asarray(av["estimates"], dtype=np.float32)
        gated, _ = apply(est, mix, cfg)
        for k in range(N_SEATS):
            bleed = float(eq["bleed_db"][k])
            if not (
                masks[k].sum() / VAD_RATE >= MIN_SPEECH_S
                and np.isfinite(bleed)
                and bleed <= MAX_BLEED_DB
            ):
                continue
            if not np.any(est[k]):
                continue
            row: dict[str, Any] = {"meeting": meeting.name, "speaker": k}
            for name, x in (("av", est[k]), ("arb", gated[k])):
                row[f"{name}_leak_db"] = silence_leakage_db(x, masks[k], FRAME_SAMPLES)
                row[f"{name}_si_sdri"] = (
                    si_sdr(x, refs[k]) - si_sdr(mix, refs[k]) if np.any(x) else float("-inf")
                )
                row[f"{name}_stoi"] = stoi(x, refs[k]) if np.any(x) else float("nan")
            rows.append(row)
    out: dict[str, Any] = {"n": len(rows), "config": cfg.as_dict()}
    for key in ("leak_db", "si_sdri", "stoi"):
        a = [r[f"arb_{key}"] for r in rows]
        b = [r[f"av_{key}"] for r in rows]
        diffs = [x - y for x, y in zip(a, b, strict=True) if np.isfinite(x) and np.isfinite(y)]
        out[key] = {
            "av": bootstrap_ci([v for v in b if np.isfinite(v)]),
            "arbitrated": bootstrap_ci([v for v in a if np.isfinite(v)]),
            "delta": bootstrap_ci(diffs),
            "n_pairs": len(diffs),
            "arb_no_output": sum(1 for v in a if v == float("-inf")),
        }
    (OUT / "arbitration_signal.json").write_text(
        json.dumps({"summary": out, "rows": rows}, indent=1, default=float)
    )
    print(json.dumps(out, indent=1, default=lambda v: round(float(v), 3)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
