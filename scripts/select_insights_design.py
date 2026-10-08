"""Choose how the insights combine captions and diarization - on the fitting split only.

bench_ami_insights.py found that caption words give better talk time while
face-anchored diarization gives better overlap and interruptions. This
compares designs on the 20 fitting meetings (ES/IS, `split == "train"`) from
the cached inputs, so the held-out TS meetings stay untouched for reporting.

Naming (who each pyannote label is):
    argmax   the person whose caption words it overlaps most (pipeline.diarize)
    iou      the same, normalised by both sides' length (Jaccard)
    hungarian  one label per person, maximising total overlap

Sourcing:
    words    every number from caption words
    anchored every number from face-anchored diarization
    hybrid   talk time and turns from words; overlap and interruptions from
             face-anchored diarization

    uv run python scripts/select_insights_design.py
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

from bench_ami_av import N_SEATS, OUT, meetings  # noqa: E402
from bench_ami_insights import FRAME_MS, score, to_ms  # noqa: E402

from eval.ami_words import words  # noqa: E402
from pipeline.diarize import anchor  # noqa: E402
from pipeline.insights import compute  # noqa: E402

Interval = tuple[int, int]


def mask(intervals: list[Interval], n: int) -> np.ndarray:
    m = np.zeros(n, dtype=bool)
    for a, b in intervals:
        m[a // FRAME_MS : -(-b // FRAME_MS)] = True
    return m


def name_labels(
    segments: list[tuple[int, int, str]], speech: list[list[Interval]], duration: int, how: str
) -> list[list[Interval]]:
    if how == "hungarian":
        # What shipped: pipeline.diarize.anchor.
        return anchor(segments, speech, duration)
    n = -(-duration // FRAME_MS)
    people = np.stack([mask(s, n) for s in speech])
    labels: dict[str, list[Interval]] = {}
    for a, b, lab in segments:
        labels.setdefault(lab, []).append((a, b))
    names = list(labels)
    lm = np.stack([mask(labels[k], n) for k in names])
    inter = (lm[:, None, :] & people[None, :, :]).sum(-1).astype(float)
    out: list[list[Interval]] = [[] for _ in speech]
    if how == "iou":
        union = (lm[:, None, :] | people[None, :, :]).sum(-1)
        score_m = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
        for i, k in enumerate(names):
            if inter[i].max() > 0:
                out[int(np.argmax(score_m[i]))].extend(labels[k])
    elif how == "argmax":
        for i, k in enumerate(names):
            if inter[i].max() > 0:
                out[int(np.argmax(inter[i]))].extend(labels[k])
    return out


def main() -> int:
    results: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for split, meeting, start_s in meetings():
        cache = OUT / meeting.name / "insights_inputs.json"
        if not cache.exists():
            continue
        inputs = json.loads(cache.read_text())
        eq = np.load(OUT / meeting.name / "equalised.npz")
        duration = round(len(eq["mixture"]) / 16_000 * 1000)
        reference = [
            to_ms(words(meeting.name, k, start_s, start_s + duration / 1000))
            for k in range(N_SEATS)
        ]
        ref = compute(dict(enumerate(reference)), duration)
        speech = [[(int(a), int(b)) for a, b in ws] for ws in inputs["visiovox"]]
        segments = [(int(a), int(b), str(lab)) for a, b, lab in inputs["pyannote_segments"]]
        by_words = score(compute(dict(enumerate(speech)), duration), ref)
        for how in ("argmax", "iou", "hungarian"):
            named = compute(dict(enumerate(name_labels(segments, speech, duration, how))), duration)
            by_diar = score(named, ref)
            hybrid = {
                **by_words,
                "overlap": by_diar["overlap"],
                "interruption": by_diar["interruption"],
            }
            for sourcing, row in (("anchored", by_diar), ("hybrid", hybrid)):
                results.setdefault(split, {}).setdefault(f"{how}/{sourcing}", []).append(row)
        results.setdefault(split, {}).setdefault("words", []).append(by_words)

    def pooled(rows: list[dict[str, Any]], key: str) -> float:
        tp = sum(r[key]["tp"] for r in rows)
        p = tp / max(1, sum(r[key]["n_hyp"] for r in rows))
        r = tp / max(1, sum(r[key]["n_ref"] for r in rows))
        return 2 * p * r / (p + r) if p + r else 0.0

    table: dict[str, Any] = {}
    for split in ("train", "eval"):
        print(f"\n{split} ({len(results[split]['words'])} meetings)")
        print(f"{'design':22} {'share MAE':>10} {'overlap F1':>11} {'interrupt F1':>13}")
        for design, rows in sorted(results[split].items()):
            share = float(np.mean([r["share_mae_pp"] for r in rows]))
            ov, it = pooled(rows, "overlap"), pooled(rows, "interruption")
            table.setdefault(split, {})[design] = {
                "share_mae_pp": share,
                "overlap_f1": ov,
                "interruption_f1": it,
            }
            print(f"{design:22} {share:10.1f} {ov:11.2f} {it:13.2f}")
    (OUT / "insights_design.json").write_text(json.dumps(table, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
