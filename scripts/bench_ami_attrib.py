"""Transcribe once, attribute by face: word-level attribution from extracted streams.

bench_ami_asr.py measured that transcribing each person's extracted stream
fails on real meetings: Whisper writes down cross-talk 13-25 dB below the
talker, so even near-clean close-talk headsets score cpWER > 100 %, almost all
insertions. No separator leaks little enough for per-stream transcription.

So the streams are used for what they are good at - deciding *who* - and the
words come from a single transcription of the mixture, where every word
appears once. A word spanning [s, e] goes to the person whose stream keeps the
largest share of the mixture's energy over that span:

    speaker(word) = argmax_k  mean_{t in [s,e]}  r_k(t),
    r_k(t) = 10 log10( E(stream_k)(t) / E(mixture)(t) )

with E smoothed exactly as in the frozen arbitration config (fitted on the
training meetings). There is no other parameter, and every word lands on a
face, unlike a diarizer's anonymous clusters.

Systems (all from the same mixture transcript):

    attrib_av       argmax over the AV-MossFormer2 streams
    attrib_hybrid   argmax over the hybrid streams (SEAVE where the face is lost)
    attrib_oracle   each word to the participant whose aligned AMI words
                    overlap it most - the ceiling for any attribution of this
                    transcript, not a system

The audio-only baseline (pyannote 3.1 + Whisper) is read from asr.json, which
used the same transcript settings.

    uv run python scripts/bench_ami_attrib.py
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
from pipeline.arbitration import FRAME, ArbitrationConfig, retention  # noqa: E402

Word = tuple[float, float, str]


def attribute(
    transcript: list[Word], streams: np.ndarray, mixture: np.ndarray, smooth_s: float
) -> list[int]:
    ratios = retention(streams, mixture, smooth_s)  # (k, frames), -inf for empty streams
    out = []
    for start, end, _ in transcript:
        a = max(int(start * RATE / FRAME), 0)
        b = max(min(int(np.ceil(end * RATE / FRAME)), ratios.shape[1]), a + 1)
        out.append(int(np.argmax(ratios[:, a:b].mean(axis=1))))
    return out


def oracle(transcript: list[Word], spoken: list[list[Any]]) -> list[int]:
    out = []
    for start, end, _ in transcript:
        best, best_overlap, best_gap = 0, 0.0, float("inf")
        for k, ws in enumerate(spoken):
            for w in ws:
                overlap = min(end, w.end) - max(start, w.start)
                gap = max(w.start - end, start - w.end, 0.0)
                if overlap > best_overlap or (
                    best_overlap == 0.0 and overlap <= 0 and gap < best_gap
                ):
                    best, best_overlap, best_gap = k, max(overlap, 0.0), gap
        out.append(best)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    from faster_whisper import WhisperModel
    from meeteval.wer import cp_word_error_rate
    from whisper_normalizer.english import EnglishTextNormalizer

    normalise = EnglishTextNormalizer()
    asr = WhisperModel("large-v3", device=args.device, compute_type="float16")
    configs = {
        name: ArbitrationConfig(
            **json.loads((OUT / f"arbitration-{name}.json").read_text())["config"]
        )
        for name in ("av", "hybrid")
    }

    def text(ws: list[Word]) -> str:
        return " ".join(normalise(" ".join(w[2] for w in ws)).split())

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

        peak = float(np.abs(mixture).max())
        segments, _ = asr.transcribe(
            (mixture / peak * 0.5).astype(np.float32),
            language="en",
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        transcript: list[Word] = [
            (w.start, w.end, w.word) for s in segments for w in (s.words or [])
        ]

        hybrid = np.array(av["estimates"], dtype=np.float32)
        for k in range(N_SEATS):
            g = np.load(work / f"gapfill_seat{k}.npz")
            if "hybrid_self" in g.files:
                hybrid[k] = g["hybrid_self"].astype(np.float32)
        labels = {
            "attrib_av": attribute(
                transcript, np.asarray(av["estimates"]), mixture, configs["av"].smooth_s
            ),
            "attrib_hybrid": attribute(transcript, hybrid, mixture, configs["hybrid"].smooth_s),
            "attrib_oracle": oracle(transcript, spoken),
        }
        for system, lab in labels.items():
            hyp = [
                text([w for w, s in zip(transcript, lab, strict=True) if s == k])
                for k in range(N_SEATS)
            ]
            er = cp_word_error_rate(reference=reference, hypothesis=hyp)
            rows.append(
                {
                    "meeting": meeting.name,
                    "split": split,
                    "system": system,
                    "errors": int(er.errors),
                    "length": int(er.length),
                    "insertions": int(er.insertions),
                    "deletions": int(er.deletions),
                    "substitutions": int(er.substitutions),
                    "cpwer": float(er.error_rate),
                    "n_streams": N_SEATS,
                    "hypothesis": hyp,
                }
            )
        summary = "  ".join(
            f"{r['system']} {r['cpwer']:.1%}" for r in rows if r["meeting"] == meeting.name
        )
        print(f"[{i}/{len(todo)}] {meeting.name}: {summary}; {time.time() - t0:.0f}s", flush=True)
        (OUT / "attrib.json").write_text(json.dumps(rows, indent=1, default=float))
    print("ATTRIB_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
