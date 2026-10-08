"""Language detection: once per recording, or once per extracted track?

All AMI meetings are in English (TNO participants are native Dutch speakers).
S7 used to let Whisper detect the language of each extracted track on its own;
a quiet, leaky track can be detected as Dutch, and Whisper then writes Dutch
for English speech. This counts how often that happens on AMI-AV, per track
and once on the mixture, with the detection settings S7 uses.

    uv run python scripts/bench_ami_language.py
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

from bench_ami_av import OUT, meetings  # noqa: E402

from pipeline.s7_transcribe import detect_language, load_transcriber  # noqa: E402


def main() -> int:
    model = load_transcriber(device="cuda")
    rows: list[dict[str, Any]] = []
    for split, meeting, _start in meetings():
        work = OUT / meeting.name
        mixture = np.load(work / "equalised.npz")["mixture"]
        tracks = np.load(work / "av_tracks.npz")["estimates"]
        mix = detect_language(mixture, model)
        per = [detect_language(t, model) if np.any(t) else None for t in tracks]
        rows.append(
            {
                "meeting": meeting.name,
                "split": split,
                "mixture": mix,
                "tracks": per,
            }
        )
        print(meeting.name, "mixture", mix, "tracks", per, flush=True)
    tracks_all = [t for r in rows for t in r["tracks"] if t is not None]
    wrong_tracks = sum(1 for t in tracks_all if t[0] != "en")
    wrong_mix = sum(1 for r in rows if r["mixture"] is None or r["mixture"][0] != "en")
    summary = {
        "tracks": len(tracks_all),
        "tracks_not_english": wrong_tracks,
        "meetings": len(rows),
        "mixture_not_english": wrong_mix,
    }
    (OUT / "language.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
