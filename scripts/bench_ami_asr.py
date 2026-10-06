"""Who said what: speaker-attributed ASR on AMI-AV (paper Sec. VII).

The product's promise is a transcript in which every line belongs to the right
person. That is scored with cpWER (concatenated minimum-permutation WER, the
CHiME-6 metric, via meeteval): each system emits one word stream per person it
found, the reference has one stream per participant, streams are paired up in
the way that minimises errors, and anything said by the wrong person or by
nobody counts against it.

The reference is AMI's hand transcript, not the headsets, so this metric is
untouched by headset bleed and every participant is scored.

Systems, all transcribed with the production Whisper (large-v3, beam 5):

    mixture_diarized   pyannote 3.1 on the mixture, Whisper on the mixture,
                       each word given to the diarized speaker it falls in -
                       the standard audio-only pipeline
    av                 one stream per seat: Whisper on that seat's AV-MossFormer2
                       output (production)
    hybrid_self        AV where the face is tracked, self-enrolled SEAVE elsewhere
    seave_self         self-enrolled SEAVE alone
    hybrid_mix         AV where the face is tracked, the unprocessed mixture
                       elsewhere - the naive gap fill
    av_arbitrated      av after cross-stream arbitration (pipeline/arbitration.py),
                       parameters frozen on the training meetings
    hybrid_arbitrated  the same, on the hybrid streams
    av_oracle_gate     av gated by the true word activity - the ceiling for any
                       arbitration of these streams, not a system
    headset            Whisper on each equal-level headset - not a system, the
                       floor set by the recognizer itself on near-clean input

Also reported: plain WER of Whisper on the mixture as one stream, ignoring
attribution - how much the recogniser gets right before anyone asks who said it.

Whisper runs with its Silero VAD on and without conditioning on previous text,
for every system alike: both curb hallucination on the long silences a
per-person stream contains.

    uv run python scripts/bench_ami_asr.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "scripts"))

from bench_ami_av import N_SEATS, OUT, RATE, meetings  # noqa: E402
from bench_ami_gapfill import splice  # noqa: E402

from eval.ami_words import words  # noqa: E402
from pipeline.arbitration import ArbitrationConfig  # noqa: E402
from pipeline.arbitration import apply as arbitrate  # noqa: E402

DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"


def hf_token() -> str:
    if os.environ.get("HF_TOKEN"):
        return os.environ["HF_TOKEN"]
    for name in (".env.local", ".env.production"):
        path = ROOT / name
        if path.exists():
            for line in path.read_text().splitlines():
                if line.startswith("HF_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("HF_TOKEN not set")


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

    def transcribe(audio: np.ndarray) -> list[tuple[float, float, str]]:
        if not np.any(audio):
            return []
        peak = float(np.abs(audio).max())
        audio = (audio / peak * 0.5).astype(np.float32) if peak > 0 else audio
        segments, _ = asr.transcribe(
            audio,
            language="en",
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return [(w.start, w.end, w.word) for s in segments for w in (s.words or [])]

    def text(ws: list[tuple[float, float, str]]) -> str:
        return " ".join(normalise(" ".join(w[2] for w in ws)).split())

    rows: list[dict[str, Any]] = []
    todo = meetings()[: args.limit or None]
    for i, (split, meeting, start_s) in enumerate(todo, 1):
        work = OUT / meeting.name
        if not (work / "av_tracks.npz").exists() or not (work / "gapfill_seat0.npz").exists():
            print(f"[{i}/{len(todo)}] {meeting.name}: caches missing, skipped", flush=True)
            continue
        t0 = time.time()
        eq = dict(np.load(work / "equalised.npz"))
        av = dict(np.load(work / "av_tracks.npz"))
        mixture = eq["mixture"]
        duration = len(mixture) / RATE
        spoken = [words(meeting.name, k, start_s, start_s + duration) for k in range(N_SEATS)]
        reference = [" ".join(normalise(" ".join(w.text for w in ws)).split()) for ws in spoken]
        # One stream in time order, for the unattributed WER: Whisper on the
        # mixture writes words chronologically, not speaker by speaker.
        timeline = sorted((w for ws in spoken for w in ws), key=lambda w: w.start)
        reference_timeline = " ".join(normalise(" ".join(w.text for w in timeline)).split())
        n_ref = sum(len(r.split()) for r in reference)

        streams: dict[str, list[str]] = {}
        # Audio-only baseline.
        mix_words = transcribe(mixture)
        diar = diarizer({"waveform": torch.from_numpy(mixture)[None], "sample_rate": RATE})
        # pyannote 4.x wraps the annotation; 3.x returned it directly.
        annotation: Any = getattr(diar, "speaker_diarization", diar)
        turns = [
            (seg.start, seg.end, str(lab))
            for seg, _, lab in annotation.itertracks(yield_label=True)
        ]
        labels = sorted({t[2] for t in turns})
        per: dict[str, list[tuple[float, float, str]]] = {lab: [] for lab in labels}
        unassigned: list[tuple[float, float, str]] = []
        for w in mix_words:
            mid = (w[0] + w[1]) / 2
            hit = [t for t in turns if t[0] <= mid < t[1]]
            if not hit:  # nearest turn, so a word is never silently dropped
                hit = sorted(turns, key=lambda t: min(abs(t[0] - mid), abs(t[1] - mid)))[:1]
            (per[hit[0][2]] if hit else unassigned).append(w)
        streams["mixture_diarized"] = [text(per[lab]) for lab in labels] + (
            [text(unassigned)] if unassigned else []
        )
        # Per-seat systems.
        seat_audio: dict[str, list[np.ndarray]] = {
            "av": [],
            "hybrid_self": [],
            "seave_self": [],
            "headset": [],
            "hybrid_mix": [],
        }
        visible = av["visible"].astype(bool)
        for k in range(N_SEATS):
            g = dict(np.load(work / f"gapfill_seat{k}.npz"))
            seat_audio["av"].append(av["estimates"][k])
            seat_audio["headset"].append(eq["refs"][k])
            for s in ("hybrid_self", "seave_self"):
                # No self-enrolment possible (face never tracked speaking):
                # the seat contributes what AV gave it.
                seat_audio[s].append(g[s].astype(np.float32) if s in g else av["estimates"][k])
            # The naive gap fill: the unprocessed mixture wherever the face is
            # lost. Only for people the system knows exist (tracked at least once).
            if visible[k].any():
                seat_audio["hybrid_mix"].append(splice(av["estimates"][k], mixture, visible[k]))
            else:
                seat_audio["hybrid_mix"].append(av["estimates"][k])
        # Cross-stream arbitration with parameters frozen on the training
        # meetings, and an oracle gate (true word activity) as its ceiling.
        for name, source in (("av", "av"), ("hybrid", "hybrid_self")):
            frozen = OUT / f"arbitration-{name}.json"
            if frozen.exists():
                cfg = ArbitrationConfig(**json.loads(frozen.read_text())["config"])
                gated, _ = arbitrate(np.stack(seat_audio[source]), mixture, cfg)
                seat_audio[f"{name}_arbitrated"] = list(gated)
        truth = eq["masks"].astype(bool)
        oracle = []
        for k in range(N_SEATS):
            gate = np.repeat(truth[k].astype(np.float32), len(mixture) // truth.shape[1])
            gate = np.pad(gate, (0, len(mixture) - len(gate)))
            oracle.append(np.asarray(seat_audio["av"][k], dtype=np.float32) * gate)
        seat_audio["av_oracle_gate"] = oracle
        for system, audios in seat_audio.items():
            streams[system] = [text(transcribe(a)) for a in audios]

        for system, hyp in streams.items():
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
                    "n_streams": len(hyp),
                    "hypothesis": hyp,
                }
            )
        plain = siso_word_error_rate(reference=reference_timeline, hypothesis=text(mix_words))
        rows.append(
            {
                "meeting": meeting.name,
                "split": split,
                "system": "mixture_unattributed_wer",
                "errors": int(plain.errors),
                "length": int(plain.length),
                "cpwer": float(plain.error_rate),
                "n_streams": 1,
            }
        )
        summary = "  ".join(
            f"{r['system']} {r['cpwer']:.1%}" for r in rows if r["meeting"] == meeting.name
        )
        print(
            f"[{i}/{len(todo)}] {meeting.name} ({n_ref} ref words): {summary}; {time.time() - t0:.0f}s",
            flush=True,
        )
        (OUT / "asr.json").write_text(json.dumps(rows, indent=1, default=float))
    print("ASR_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
