"""Off-screen speech: can the system tell that nobody on screen said a word?

After cross-track deduplication (ADR-0017) every caption word sits under some
visible face, including words said by someone the camera does not show (14.4 %
of AMI-AV speech). The candidate cue is the retention of the track that kept
the word, r = 10 log10 E(track)/E(mixture) over the word: the visible speaker's
own track keeps most of their speech, while off-screen speech reaches every
visible track only as leakage.

Ground truth per caption word (from AMI's aligned words and the face tracks):
    onscreen   someone speaking at the word's midpoint has their face tracked
    offscreen  someone is speaking, and nobody speaking is tracked
    (words with nobody speaking are not labelled)

Rule fixed before scoring: on the 20 fitting meetings, choose the threshold on
r that minimises seat-attributed errors when words below it move to an
"off-screen" caption stream (not bound to any seat); score the 10 held-out
meetings once. Also reported: ROC AUC of r for offscreen vs onscreen.

    uv run python scripts/bench_ami_offscreen.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "scripts"))

from bench_ami_av import N_SEATS, OUT, RATE, VAD_RATE, meetings  # noqa: E402

from eval.ami_words import words  # noqa: E402
from pipeline.arbitration import FRAME, retention  # noqa: E402
from pipeline.word_dedup import DEFAULT_SMOOTH_S, deduplicate  # noqa: E402

THRESHOLDS = [-30.0, -20.0, -15.0, -12.0, -10.0, -8.0, -6.0, -5.0, -4.0, -3.0, -2.0]
Word = tuple[float, float, str]


def auc(pos: list[float], neg: list[float]) -> float:
    """P(score of a random offscreen word < score of a random onscreen word)."""
    if not pos or not neg:
        return float("nan")
    p, n = np.asarray(pos), np.asarray(neg)
    less = (p[:, None] < n[None, :]).mean()
    ties = (p[:, None] == n[None, :]).mean()
    return float(less + 0.5 * ties)


def main() -> int:
    from faster_whisper import WhisperModel
    from meeteval.wer import cp_word_error_rate, siso_word_error_rate
    from whisper_normalizer.english import EnglishTextNormalizer

    normalise = EnglishTextNormalizer()
    asr = WhisperModel("large-v3", device="cuda", compute_type="float16")

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

    data: list[dict[str, Any]] = []
    for split, meeting, start_s in meetings():
        t0 = time.time()
        work = OUT / meeting.name
        eq = np.load(work / "equalised.npz")
        av = np.load(work / "av_tracks.npz")
        mixture = eq["mixture"]
        masks = eq["masks"].astype(bool)
        visible = av["visible"].astype(bool)
        streams = np.asarray(av["estimates"], dtype=np.float32)
        duration = len(mixture) / RATE
        spoken = [words(meeting.name, k, start_s, start_s + duration) for k in range(N_SEATS)]
        reference = [text([(w.start, w.end, w.text) for w in ws]) for ws in spoken]

        per = [transcribe(s) for s in streams]
        keep = deduplicate(per, streams, mixture, normalise=lambda t: str(normalise(t)).strip())
        ratios = retention(streams, mixture, DEFAULT_SMOOTH_S)
        kept: list[dict[str, Any]] = []
        for k, (ws, ks) in enumerate(zip(per, keep, strict=True)):
            for (s, e, t), ok in zip(ws, ks, strict=True):
                if not ok:
                    continue
                a = min(max(int(s * RATE / FRAME), 0), ratios.shape[1] - 1)
                b = max(min(int(np.ceil(e * RATE / FRAME)), ratios.shape[1]), a + 1)
                r = float(ratios[k, a:b].mean())
                mid = min(int((s + e) / 2 * VAD_RATE), masks.shape[1] - 1)
                speaking = [j for j in range(N_SEATS) if masks[j, mid]]
                if not speaking:
                    label = "none"
                elif any(visible[j, min(mid, visible.shape[1] - 1)] for j in speaking):
                    label = "onscreen"
                else:
                    label = "offscreen"
                kept.append({"seat": k, "start": s, "end": e, "text": t, "r": r, "label": label})
        data.append(
            {"meeting": meeting.name, "split": split, "reference": reference, "words": kept}
        )
        n_off = sum(w["label"] == "offscreen" for w in kept)
        print(
            f"{meeting.name}: {len(kept)} caption words, {n_off} off-screen; {time.time() - t0:.0f}s",
            flush=True,
        )
        (OUT / "offscreen_words.json").write_text(json.dumps(data, indent=1))

    def evaluate(split: str, threshold: float | None) -> dict[str, float]:
        sa = cp = n = 0
        for m in data:
            if m["split"] != split:
                continue
            seats: list[list[Word]] = [[] for _ in range(N_SEATS)]
            off: list[Word] = []
            for w in m["words"]:
                item = (w["start"], w["end"], w["text"])
                if threshold is not None and w["r"] < threshold:
                    off.append(item)
                else:
                    seats[w["seat"]].append(item)
            for ws in seats:
                ws.sort()
            hyp = [text(ws) for ws in seats]
            sa += sum(
                int(siso_word_error_rate(reference=r, hypothesis=h).errors)
                for r, h in zip(m["reference"], hyp, strict=True)
            )
            off.sort()
            cp += int(
                cp_word_error_rate(reference=m["reference"], hypothesis=[*hyp, text(off)]).errors
            )
            n += sum(len(r.split()) for r in m["reference"])
        return {"sawer": sa / n, "cpwer": cp / n}

    scores = {
        split: {
            "auc": auc(
                [
                    w["r"]
                    for m in data
                    if m["split"] == split
                    for w in m["words"]
                    if w["label"] == "offscreen"
                ],
                [
                    w["r"]
                    for m in data
                    if m["split"] == split
                    for w in m["words"]
                    if w["label"] == "onscreen"
                ],
            ),
            "offscreen_words": sum(
                w["label"] == "offscreen" for m in data if m["split"] == split for w in m["words"]
            ),
            "onscreen_words": sum(
                w["label"] == "onscreen" for m in data if m["split"] == split for w in m["words"]
            ),
        }
        for split in ("train", "eval")
    }
    grid = {th: evaluate("train", th)["sawer"] for th in THRESHOLDS}
    base_train = evaluate("train", None)["sawer"]
    best = min(grid, key=lambda th: (grid[th], th))
    chosen = best if grid[best] < base_train else None
    result = {
        "separability": scores,
        "grid_train_sawer": grid,
        "train": {"none": evaluate("train", None), "chosen": evaluate("train", chosen)},
        "chosen_threshold": chosen,
        "eval": {"none": evaluate("eval", None), "chosen": evaluate("eval", chosen)},
    }
    (OUT / "offscreen.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    print("OFFSCREEN_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
