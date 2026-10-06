"""Words under the right face: seat-attributed WER on AMI-AV.

cpWER (bench_ami_asr.py) lets each system pair its streams with participants
in whatever way minimises errors. That rewards a diarizer's anonymous but
consistent clusters - "speaker 1", "speaker 2" - and it is the right metric for
meeting transcription. It is not what VisioVox has to deliver: each caption must
appear under the right face. A diarizer alone cannot do that at all.

Seat-attributed WER (saWER) removes the free permutation: the stream a system
emits for seat k is scored against participant k, the person whose face is in
that seat. Errors are summed over seats and divided by reference words.

Systems, all from one Whisper transcript of the mixture:

    attrib_av      each word to the seat whose AV-MossFormer2 stream keeps most
                   of it (bench_ami_attrib.py)
    diar_faces     pyannote 3.1 decides who spoke when; each diarized cluster is
                   then given to the seat whose AV stream keeps the most of that
                   cluster's speech, averaged over all its turns. Clusters may
                   share a seat (a diarizer over-splits people). Rule fixed
                   before scoring; no parameters.
    diar_faces_hybrid  the same, with the SEAVE gap-filled streams
    attrib_oracle  each word to the participant whose aligned words overlap it
                   most - the ceiling for this transcript

plus the per-stream systems from asr.json (av, av_arbitrated), whose streams are
already seat-bound.

    uv run python scripts/bench_ami_seat.py
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

from bench_ami_asr import DIARIZATION_MODEL, hf_token  # noqa: E402
from bench_ami_attrib import attribute, oracle  # noqa: E402
from bench_ami_av import N_SEATS, OUT, RATE, meetings  # noqa: E402

from eval.ami_words import words  # noqa: E402
from pipeline.arbitration import FRAME, ArbitrationConfig, retention  # noqa: E402

Word = tuple[float, float, str]


def word_cluster(w: Word, turns: list[tuple[float, float, str]]) -> str | None:
    """The diarized cluster a word falls in, else the nearest turn's."""
    mid = (w[0] + w[1]) / 2
    hit = [t for t in turns if t[0] <= mid < t[1]]
    if not hit:
        hit = sorted(turns, key=lambda t: min(abs(t[0] - mid), abs(t[1] - mid)))[:1]
    return hit[0][2] if hit else None


def cluster_seats(turns: list[tuple[float, float, str]], ratios: np.ndarray) -> dict[str, int]:
    """Each diarized cluster -> the seat whose stream keeps most of its speech."""
    out: dict[str, int] = {}
    for label in sorted({t[2] for t in turns}):
        frames = np.zeros(ratios.shape[1], dtype=bool)
        for start, end, lab in turns:
            if lab == label:
                frames[int(start * RATE / FRAME) : int(np.ceil(end * RATE / FRAME))] = True
        if not frames.any():
            continue
        out[label] = int(np.argmax(ratios[:, frames].mean(axis=1)))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    import torch
    from faster_whisper import WhisperModel
    from meeteval.wer import cp_word_error_rate, siso_word_error_rate
    from pyannote.audio import Pipeline
    from whisper_normalizer.english import EnglishTextNormalizer

    normalise = EnglishTextNormalizer()
    asr = WhisperModel("large-v3", device=args.device, compute_type="float16")
    diarizer = Pipeline.from_pretrained(DIARIZATION_MODEL, token=hf_token())
    if diarizer is None:
        raise PermissionError(f"pyannote returned no pipeline for {DIARIZATION_MODEL}")
    diarizer.to(torch.device(args.device))
    smooth = {
        name: ArbitrationConfig(
            **json.loads((OUT / f"arbitration-{name}.json").read_text())["config"]
        ).smooth_s
        for name in ("av", "hybrid")
    }
    per_stream = json.loads((OUT / "asr.json").read_text())

    def text(ws: list[Word]) -> str:
        return " ".join(normalise(" ".join(w[2] for w in ws)).split())

    def score(reference: list[str], hyp: list[str]) -> dict[str, Any]:
        cp = cp_word_error_rate(reference=reference, hypothesis=hyp)
        errors = sum(
            int(siso_word_error_rate(reference=r, hypothesis=h).errors)
            for r, h in zip(reference, hyp, strict=True)
        )
        length = sum(len(r.split()) for r in reference)
        return {
            "cp_errors": int(cp.errors),
            "sa_errors": errors,
            "length": length,
            "cpwer": float(cp.error_rate),
            "sawer": errors / length if length else float("nan"),
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
        diar = diarizer({"waveform": torch.from_numpy(mixture)[None], "sample_rate": RATE})
        annotation: Any = getattr(diar, "speaker_diarization", diar)
        turns = [
            (seg.start, seg.end, str(lab))
            for seg, _, lab in annotation.itertracks(yield_label=True)
        ]

        clusters = [word_cluster(w, turns) for w in transcript]
        hybrid = np.array(av["estimates"], dtype=np.float32)
        for k in range(N_SEATS):
            g = np.load(work / f"gapfill_seat{k}.npz")
            if "hybrid_self" in g.files:
                hybrid[k] = g["hybrid_self"].astype(np.float32)

        labels: dict[str, list[int]] = {
            "attrib_av": attribute(transcript, np.asarray(av["estimates"]), mixture, smooth["av"]),
            "attrib_oracle": oracle(transcript, spoken),
        }
        for name, streams, key in (
            ("diar_faces", np.asarray(av["estimates"]), "av"),
            ("diar_faces_hybrid", hybrid, "hybrid"),
        ):
            seat_of = cluster_seats(turns, retention(streams, mixture, smooth[key]))
            labels[name] = [seat_of.get(c, 0) if c is not None else 0 for c in clusters]

        for system, lab in labels.items():
            hyp = [
                text([w for w, s in zip(transcript, lab, strict=True) if s == k])
                for k in range(N_SEATS)
            ]
            rows.append(
                {"meeting": meeting.name, "split": split, "system": system, **score(reference, hyp)}
            )
        for r in per_stream:
            if r["meeting"] == meeting.name and r["system"] in (
                "av",
                "av_arbitrated",
                "hybrid_arbitrated",
            ):
                rows.append(
                    {
                        "meeting": meeting.name,
                        "split": split,
                        "system": f"stream_{r['system']}",
                        **score(reference, r["hypothesis"]),
                    }
                )
        summary = "  ".join(
            f"{r['system']} sa {r['sawer']:.1%} cp {r['cpwer']:.1%}"
            for r in rows
            if r["meeting"] == meeting.name
        )
        print(f"[{i}/{len(todo)}] {meeting.name}: {summary}; {time.time() - t0:.0f}s", flush=True)
        (OUT / "seat.json").write_text(json.dumps(rows, indent=1, default=float))
    print("SEAT_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
