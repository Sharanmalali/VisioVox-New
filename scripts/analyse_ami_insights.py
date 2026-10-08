"""Summarise bench_ami_insights.py: means with bootstrap CIs over meetings.

uv run python scripts/analyse_ami_insights.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_ami_av import OUT

RNG = np.random.default_rng(0)
B = 10_000


def ci(values: np.ndarray) -> tuple[float, float, float]:
    boots = RNG.choice(values, size=(B, len(values)), replace=True).mean(1)
    return float(values.mean()), float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))


def pooled_f1(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    """Micro-averaged over meetings: sum the counts, then divide."""
    tp = sum(r[key]["tp"] for r in rows)
    n_hyp = sum(r[key]["n_hyp"] for r in rows)
    n_ref = sum(r[key]["n_ref"] for r in rows)
    p = tp / n_hyp if n_hyp else float("nan")
    r = tp / n_ref if n_ref else float("nan")
    return {
        "precision": p,
        "recall": r,
        "f1": 2 * p * r / (p + r) if p + r else 0.0,
        "n_ref": n_ref,
    }


def paired(rows: list[dict[str, Any]], a: str, b: str, metric: str) -> tuple[float, float, float]:
    """Mean of (a - b) per meeting, with a bootstrap CI."""
    by = {(r["meeting"], r["system"]): r for r in rows}
    meetings = sorted({r["meeting"] for r in rows})
    d = np.array([by[(m, a)][metric] - by[(m, b)][metric] for m in meetings])
    return ci(d)


def main() -> int:
    rows: list[dict[str, Any]] = json.loads((OUT / "insights.json").read_text())
    systems = sorted(
        {r["system"] for r in rows},
        key=["visiovox", "face_anchored", "pyannote_oracle"].index,
    )
    summary: dict[str, Any] = {"n_meetings": len({r["meeting"] for r in rows}), "systems": {}}
    print(f"{summary['n_meetings']} meetings\n")
    print(
        f"{'system':18} {'share MAE (pp)':>24} {'talk MAE (s)':>22} {'overlap P/R/F1':>20} {'interrupt P/R/F1':>20}"
    )
    for s in systems:
        rs = [r for r in rows if r["system"] == s]
        share = ci(np.array([r["share_mae_pp"] for r in rs]))
        talk = ci(np.array([r["talk_mae_s"] for r in rs]))
        ov = pooled_f1(rs, "overlap")
        it = pooled_f1(rs, "interruption")
        summary["systems"][s] = {
            "share_mae_pp": share,
            "talk_mae_s": talk,
            "overlap": ov,
            "interruption": it,
        }
        print(
            f"{s:18} {share[0]:6.1f} [{share[1]:4.1f}, {share[2]:4.1f}]   "
            f"{talk[0]:6.1f} [{talk[1]:4.1f}, {talk[2]:4.1f}]   "
            f"{ov['precision']:.2f}/{ov['recall']:.2f}/{ov['f1']:.2f}       "
            f"{it['precision']:.2f}/{it['recall']:.2f}/{it['f1']:.2f} (n={it['n_ref']})"
        )
    if {"face_anchored", "visiovox"} <= set(systems):
        d = paired(rows, "face_anchored", "visiovox", "share_mae_pp")
        summary["face_minus_words_share_pp"] = d
        print(
            f"\nshare MAE, face-anchored minus caption-words: {d[0]:+.1f} pp [{d[1]:+.1f}, {d[2]:+.1f}]"
        )
    if {"face_anchored", "pyannote_oracle"} <= set(systems):
        d = paired(rows, "face_anchored", "pyannote_oracle", "share_mae_pp")
        summary["face_minus_oracle_share_pp"] = d
        print(
            f"share MAE, face-anchored minus oracle naming:  {d[0]:+.1f} pp [{d[1]:+.1f}, {d[2]:+.1f}]"
        )
    (OUT / "insights_summary.json").write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
