"""Caption designs under heavy overlap: the three VoxCeleb2 two-face videos.

AMI is mostly one person at a time. VisioVox's hardest case is the opposite -
two people talking straight over each other - and a captioning design that
wins on AMI can fail here. This runs the deployed extraction (S5 + S3) on each
test video and scores, per side, the words placed under each face:

    stream        transcribe each person's track, keep every word
    stream_dedup  transcribe each track, drop cross-stream duplicates
    attrib        transcribe the mixture once, attribute each word by track

There is no human transcript for these clips. The reference for each side is
Whisper on that speaker's clean source, so this is a consistency check of the
designs against each other under overlap, not an absolute WER.

    uv run python scripts/check_overlap_captions.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))

from pipeline.word_dedup import DEFAULT_SMOOTH_S, deduplicate  # noqa: E402

BASE = Path.home() / "runs" / "moss-overlap-test"
OUT = Path.home() / "runs" / "overlap-captions"
RATE = 16_000
Word = tuple[float, float, str]


def read(path: Path) -> np.ndarray:
    x, sr = sf.read(path, dtype="float32", always_2d=False)
    assert sr == RATE
    return np.asarray(x if x.ndim == 1 else x.mean(axis=1), dtype=np.float32)


def main() -> int:
    from faster_whisper import WhisperModel
    from meeteval.wer import siso_word_error_rate
    from whisper_normalizer.english import EnglishTextNormalizer

    from pipeline.arbitration import FRAME, retention
    from visiovox_api.config import Settings
    from worker_gpu import av_tse
    from worker_gpu.real_pipeline import _load_embedder

    settings = Settings()
    normalise = EnglishTextNormalizer()
    asr = WhisperModel("large-v3", device="cuda", compute_type="float16")
    embed = _load_embedder(settings)

    def transcribe(audio: np.ndarray) -> list[Word]:
        if not np.any(audio):
            return []
        peak = float(np.abs(audio).max())
        segments, _ = asr.transcribe(
            (audio / peak * 0.5).astype(np.float32),
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return [(w.start, w.end, w.word) for s in segments for w in (s.words or [])]

    def text(ws: list[Word]) -> str:
        return " ".join(normalise(" ".join(w[2] for w in ws)).split())

    rows: list[dict[str, Any]] = []
    for pair in ("pair1", "pair2", "pair3"):
        mixture = read(BASE / "gt" / f"{pair}_mixture.wav")
        clean = {side: read(BASE / "gt" / f"{pair}_{side}_clean.wav") for side in ("left", "right")}
        work = OUT / pair
        work.mkdir(parents=True, exist_ok=True)
        desc = av_tse.run_extractor(
            BASE / "in" / f"{pair}.mp4", BASE / "gt" / f"{pair}_mixture.wav", work / "av", settings
        )
        placed, _lag = av_tse.place_tracks(desc, mixture, read)
        speaking = [t for t in placed if not av_tse.is_silent(t, mixture)]
        people = av_tse.group_speakers(speaking, embed, settings.av_tse_merge_threshold)
        # Left/right by where the person's face is.
        side_of = {}
        for p in people:
            cx = np.average(
                [t.center_x for t in p.tracks], weights=[t.end - t.start for t in p.tracks]
            )
            side_of[id(p)] = "left" if cx < 0.5 else "right"
        streams = np.stack([p.audio[: len(mixture)] for p in people])
        sides = [side_of[id(p)] for p in people]

        reference = {side: text(transcribe(x)) for side, x in clean.items()}
        per = [transcribe(s) for s in streams]
        keep = deduplicate(per, streams, mixture, normalise=lambda t: normalise(t).strip())
        mix_words = transcribe(mixture)
        ratios = retention(streams, mixture, DEFAULT_SMOOTH_S)
        owner = []
        for s, e, _ in mix_words:
            a = min(max(int(s * RATE / FRAME), 0), ratios.shape[1] - 1)
            b = max(min(int(np.ceil(e * RATE / FRAME)), ratios.shape[1]), a + 1)
            owner.append(int(np.argmax(ratios[:, a:b].mean(axis=1))))

        designs: dict[str, list[list[Word]]] = {
            "stream": per,
            "stream_dedup": [
                [w for w, ok in zip(ws, ks, strict=True) if ok]
                for ws, ks in zip(per, keep, strict=True)
            ],
            "attrib": [
                [w for w, o in zip(mix_words, owner, strict=True) if o == k]
                for k in range(len(people))
            ],
        }
        for design, hyp in designs.items():
            by_side = {"left": "", "right": ""}
            for k, side in enumerate(sides):
                by_side[side] = (by_side[side] + " " + text(hyp[k])).strip()
            for side in ("left", "right"):
                er = siso_word_error_rate(reference=reference[side], hypothesis=by_side[side])
                rows.append(
                    {
                        "pair": pair,
                        "side": side,
                        "design": design,
                        "errors": int(er.errors),
                        "length": int(er.length),
                        "wer": float(er.error_rate),
                        "reference": reference[side],
                        "hypothesis": by_side[side],
                    }
                )
        print(
            pair,
            "  ".join(
                f"{r['design']}/{r['side']} {r['wer']:.0%}" for r in rows if r["pair"] == pair
            ),
            flush=True,
        )
    for design in ("stream", "stream_dedup", "attrib"):
        rs = [r for r in rows if r["design"] == design]
        print(
            f"{design:14s} pooled WER {sum(r['errors'] for r in rs) / sum(r['length'] for r in rs):.1%}"
        )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
