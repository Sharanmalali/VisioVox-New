"""Per-track captions with cross-stream word deduplication, on AMI-AV.

Scores, from one set of per-track Whisper transcripts with word timings:

    stream        every word of every track (the deployed design)
    stream_dedup  duplicates removed by pipeline/word_dedup.py
    hybrid_dedup  the same on the SEAVE gap-filled tracks

against AMI's manual transcript, with cpWER and seat-attributed WER (see
bench_ami_seat.py). Whisper settings are those of bench_ami_asr.py.

    uv run python scripts/bench_ami_dedup.py
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

from bench_ami_av import N_SEATS, OUT, RATE, meetings  # noqa: E402

from eval.ami_words import words  # noqa: E402
from pipeline.word_dedup import deduplicate  # noqa: E402

Word = tuple[float, float, str]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    from faster_whisper import WhisperModel
    from meeteval.wer import cp_word_error_rate, siso_word_error_rate
    from whisper_normalizer.english import EnglishTextNormalizer

    normalise = EnglishTextNormalizer()
    asr = WhisperModel("large-v3", device=args.device, compute_type="float16")

    def transcribe(audio: np.ndarray) -> list[Word]:
        if not np.any(audio):
            return []
        peak = float(np.abs(audio).max())
        segments, _ = asr.transcribe(
            (audio / peak * 0.5).astype(np.float32),
            language="en",
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return [(w.start, w.end, w.word) for s in segments for w in (s.words or [])]

    def text(ws: list[Word]) -> str:
        return " ".join(normalise(" ".join(w[2] for w in ws)).split())

    def score(reference: list[str], hyp: list[str]) -> dict[str, Any]:
        cp = cp_word_error_rate(reference=reference, hypothesis=hyp)
        sa = sum(
            int(siso_word_error_rate(reference=r, hypothesis=h).errors)
            for r, h in zip(reference, hyp, strict=True)
        )
        n = sum(len(r.split()) for r in reference)
        return {
            "cp_errors": int(cp.errors),
            "sa_errors": sa,
            "length": n,
            "insertions": int(cp.insertions),
            "deletions": int(cp.deletions),
            "substitutions": int(cp.substitutions),
            "cpwer": float(cp.error_rate),
            "sawer": sa / n if n else float("nan"),
        }

    rows: list[dict[str, Any]] = []
    todo = meetings()[: args.limit or None]
    for i, (split, meeting, start_s) in enumerate(todo, 1):
        work = OUT / meeting.name
        t0 = time.time()
        eq = dict(np.load(work / "equalised.npz"))
        av = dict(np.load(work / "av_tracks.npz"))
        mixture = eq["mixture"]
        duration = len(mixture) / RATE
        spoken = [words(meeting.name, k, start_s, start_s + duration) for k in range(N_SEATS)]
        reference = [text([(w.start, w.end, w.text) for w in ws]) for ws in spoken]

        tracks = {"av": np.asarray(av["estimates"], dtype=np.float32)}
        hybrid = tracks["av"].copy()
        for k in range(N_SEATS):
            g = np.load(work / f"gapfill_seat{k}.npz")
            if "hybrid_self" in g.files:
                hybrid[k] = g["hybrid_self"].astype(np.float32)
        tracks["hybrid"] = hybrid

        for name, streams in tracks.items():
            per = [transcribe(s) for s in streams]
            keep = deduplicate(per, streams, mixture, normalise=lambda t: normalise(t).strip())
            kept = [
                [w for w, ok in zip(ws, ks, strict=True) if ok]
                for ws, ks in zip(per, keep, strict=True)
            ]
            for system, hyp_words in ((f"{name}_stream", per), (f"{name}_dedup", kept)):
                rows.append(
                    {
                        "meeting": meeting.name,
                        "split": split,
                        "system": system,
                        **score(reference, [text(ws) for ws in hyp_words]),
                    }
                )
        summary = "  ".join(
            f"{r['system']} sa {r['sawer']:.1%} cp {r['cpwer']:.1%}"
            for r in rows
            if r["meeting"] == meeting.name
        )
        print(f"[{i}/{len(todo)}] {meeting.name}: {summary}; {time.time() - t0:.0f}s", flush=True)
        (OUT / "dedup.json").write_text(json.dumps(rows, indent=1, default=float))
    print("DEDUP_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
