"""Conversation insights on AMI-AV: are the numbers VisioVox shows right?

The project page reports, per person, talk time and share, turns, and who
interrupted whom, plus when people talked over each other
(ml/pipeline/insights.py, mirrored in the browser). This scores those numbers
against the same insights computed from AMI's hand-aligned words.

Systems, on the 120 s windows of all 30 AMI-AV meetings (bench_ami_av.py):

    reference  AMI's forced-aligned words for each seat (ground truth)
    visiovox   as deployed: AV-MossFormer2 track per seat (cached), Whisper
               large-v3 per track, cross-track word dedup (ADR-0017); a track
               belongs to a seat by face position, never by score
    pyannote   pyannote 3.1 on the mixture - the usual audio-only way to get
               who-spoke-when. Diarized speakers are mapped to seats by
               *oracle* (each to the seat it overlaps most in the reference),
               which flatters it: in a product nothing tells you which label
               is which face.
    face_anchored  pyannote's segments for *when*, VisioVox's face-matched
               tracks for *who* (pipeline.diarize.anchor: labels paired one
               to one with seats by overlap with their captioned speech).
               No reference is used.

The product shows face_anchored when the job ran diarization (every job
since it shipped) and falls back to visiovox otherwise; the design was chosen
on the fitting meetings by scripts/select_insights_design.py.

Metrics per meeting:

    share_mae_pp   mean over seats of |share - reference share|, in points
    talk_mae_s     mean over seats of |talk time - reference|, in seconds
    overlap_f1     10 ms frames where two or more people speak
    interruption   precision / recall / F1; a hypothesis matches a reference
                   interruption with the same interrupter and interrupted
                   person within +-1 s

    uv run python scripts/bench_ami_insights.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "scripts"))

from bench_ami_asr import DIARIZATION_MODEL, hf_token  # noqa: E402
from bench_ami_av import N_SEATS, OUT, RATE, meetings  # noqa: E402

from eval.ami_words import words  # noqa: E402
from pipeline.diarize import anchor  # noqa: E402
from pipeline.insights import Insights, compute  # noqa: E402
from pipeline.word_dedup import deduplicate  # noqa: E402

Interval = tuple[int, int]
FRAME_MS = 10
MATCH_MS = 1000


def frames(ins: Insights, n: int) -> np.ndarray:
    """(seats, frames) speaking masks from an insight's spurts."""
    mask = np.zeros((N_SEATS, n), dtype=bool)
    for s in ins.spurts:
        mask[s.speaker, s.start // FRAME_MS : -(-s.end // FRAME_MS)] = True
    return mask


def region_mask(regions: Sequence[tuple[int, int]], n: int) -> np.ndarray:
    mask = np.zeros(n, dtype=bool)
    for a, b in regions:
        mask[a // FRAME_MS : -(-b // FRAME_MS)] = True
    return mask


def f1(tp: int, n_hyp: int, n_ref: int) -> dict[str, float]:
    p = tp / n_hyp if n_hyp else float("nan")
    r = tp / n_ref if n_ref else float("nan")
    return {
        "precision": p,
        "recall": r,
        "f1": 2 * p * r / (p + r) if n_hyp and n_ref and (p + r) > 0 else float("nan"),
        "tp": tp,
        "n_hyp": n_hyp,
        "n_ref": n_ref,
    }


def score(hyp: Insights, ref: Insights) -> dict[str, Any]:
    seats = range(N_SEATS)
    share = [abs(hyp.speakers[k].share - ref.speakers[k].share) * 100 for k in seats]
    talk = [abs(hyp.speakers[k].talk_ms - ref.speakers[k].talk_ms) / 1000 for k in seats]

    # The overlap regions as displayed (joined, >= MIN_OVERLAP_MS), not raw
    # frame coincidences.
    n = -(-ref.duration_ms // FRAME_MS)
    ov_h = region_mask(hyp.overlaps, n)
    ov_r = region_mask(ref.overlaps, n)
    overlap = f1(int((ov_h & ov_r).sum()), int(ov_h.sum()), int(ov_r.sum()))

    ref_events = [e for e in ref.events if e.kind == "interruption"]
    hyp_events = [e for e in hyp.events if e.kind == "interruption"]
    used: set[int] = set()
    tp = 0
    for e in ref_events:
        for i, h in enumerate(hyp_events):
            if i not in used and (h.by, h.of) == (e.by, e.of) and abs(h.at - e.at) <= MATCH_MS:
                used.add(i)
                tp += 1
                break
    return {
        "share_mae_pp": float(np.mean(share)),
        "talk_mae_s": float(np.mean(talk)),
        "overlap": overlap,
        "interruption": f1(tp, len(hyp_events), len(ref_events)),
        "talk_s": {
            "hyp": [hyp.speakers[k].talk_ms / 1000 for k in seats],
            "ref": [ref.speakers[k].talk_ms / 1000 for k in seats],
        },
        "turns": {
            "hyp": [hyp.speakers[k].turns for k in seats],
            "ref": [ref.speakers[k].turns for k in seats],
        },
    }


def to_ms(ws: Sequence[Any]) -> list[Interval]:
    out = []
    for w in ws:
        a, b = (w.start, w.end) if hasattr(w, "start") else (w[0], w[1])
        out.append((round(a * 1000), round(b * 1000)))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    import torch
    from faster_whisper import WhisperModel
    from pyannote.audio import Pipeline
    from whisper_normalizer.english import EnglishTextNormalizer

    normalise = EnglishTextNormalizer()
    asr: Any = None
    diarizer: Any = None

    def transcribe(audio: np.ndarray) -> list[tuple[float, float, str]]:
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

    rows: list[dict[str, Any]] = []
    todo = meetings()[: args.limit or None]
    for i, (split, meeting, start_s) in enumerate(todo, 1):
        work = OUT / meeting.name
        if not (work / "av_tracks.npz").exists():
            print(f"[{i}/{len(todo)}] {meeting.name}: no cached tracks, skipped", flush=True)
            continue
        t0 = time.time()
        cache = work / "insights_inputs.json"
        eq = dict(np.load(work / "equalised.npz"))
        mixture = eq["mixture"].astype(np.float32)
        duration_ms = round(len(mixture) / RATE * 1000)

        if cache.exists():
            inputs = json.loads(cache.read_text())
        else:
            if asr is None:
                asr = WhisperModel("large-v3", device=args.device, compute_type="float16")
                diarizer = Pipeline.from_pretrained(DIARIZATION_MODEL, token=hf_token())
                diarizer.to(torch.device(args.device))
            av = dict(np.load(work / "av_tracks.npz"))
            streams = np.asarray(av["estimates"], dtype=np.float32)
            per = [transcribe(s) for s in streams]
            keep = deduplicate(per, streams, mixture, normalise=lambda t: normalise(t).strip())
            kept = [
                [w for w, ok in zip(ws, ks, strict=True) if ok]
                for ws, ks in zip(per, keep, strict=True)
            ]
            diar = diarizer({"waveform": torch.from_numpy(mixture)[None], "sample_rate": RATE})
            annotation: Any = getattr(diar, "speaker_diarization", diar)
            segments = [
                (round(seg.start * 1000), round(seg.end * 1000), str(label))
                for seg, _, label in annotation.itertracks(yield_label=True)
            ]
            inputs = {
                "visiovox": [to_ms(ws) for ws in kept],
                "pyannote_segments": segments,
            }
            cache.write_text(json.dumps(inputs))

        reference = [
            to_ms(words(meeting.name, k, start_s, start_s + duration_ms / 1000))
            for k in range(N_SEATS)
        ]
        ref = compute(dict(enumerate(reference)), duration_ms)
        vv = compute(
            {k: [tuple(w) for w in ws] for k, ws in enumerate(inputs["visiovox"])}, duration_ms
        )

        segments = [(int(a), int(b), str(label)) for a, b, label in inputs["pyannote_segments"]]
        # Both baselines name pyannote's labels with the deployed function
        # (pipeline.diarize.anchor); they differ only in whose speech it is
        # given. Oracle: the reference's - an upper bound on the naming.
        oracle = anchor(segments, reference, duration_ms)
        py = compute(dict(enumerate(oracle)), duration_ms)
        # Face-anchored: VisioVox's own caption words per face. No reference.
        anchored = anchor(
            segments, [[(int(a), int(b)) for a, b in ws] for ws in inputs["visiovox"]], duration_ms
        )
        fa = compute(dict(enumerate(anchored)), duration_ms)

        for system, hyp in (
            ("visiovox", vv),
            ("pyannote_oracle", py),
            ("face_anchored", fa),
        ):
            rows.append(
                {"meeting": meeting.name, "split": split, "system": system, **score(hyp, ref)}
            )
        summary = "  ".join(
            f"{r['system']}: share {r['share_mae_pp']:.1f}pp ov-F1 {r['overlap']['f1']:.2f} "
            f"int-F1 {r['interruption']['f1']:.2f} ({r['interruption']['n_ref']} ref)"
            for r in rows
            if r["meeting"] == meeting.name
        )
        print(f"[{i}/{len(todo)}] {meeting.name}: {summary}; {time.time() - t0:.0f}s", flush=True)
        (OUT / "insights.json").write_text(json.dumps(rows, indent=1, default=float))

    print("INSIGHTS_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
