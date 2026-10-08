"""Who is the same person? Face + voice track merging on AMI-AV.

S3 turns face tracks into people. On AMI-AV, merging by voice alone gave 152
people for 120 seats. This reruns the production grouping
(`worker_gpu.av_tse.group_speakers`) on every meeting's cached extractor output
with and without face identity (`pipeline/face_identity.py`), and scores it
against the seats: a track's seat is where its face sits in the 2x2 grid.

    fragments    people beyond one per seat that has tracks (one person split)
    wrong        people whose tracks lie in more than one seat (two merged)

Rule fixed before scoring: parameters minimise fragments + 2 x wrong on the 20
fitting meetings (a wrong merge is worse than a split); ties go to the higher,
more conservative face threshold. The 10 TS meetings are scored once.

    uv run python scripts/bench_ami_identity.py
"""

from __future__ import annotations

import hashlib
import itertools
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "scripts"))

from bench_ami_av import OUT, meetings, seat_of  # noqa: E402

from pipeline.face_identity import crop_video_for, load_embedder, track_vector  # noqa: E402

FACE_THRESHOLDS = [0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6]
VETOES: list[float | None] = [None, 0.1, 0.2]


def read(path: Path) -> np.ndarray:
    x, _ = sf.read(path, dtype="float32", always_2d=False)
    return np.asarray(x if x.ndim == 1 else x.mean(axis=1), dtype=np.float32)


def score(people: list[Any], seat: dict[int, int]) -> dict[str, int]:
    seats_with_tracks = {seat[t.index] for p in people for t in p.tracks}
    wrong = sum(1 for p in people if len({seat[t.index] for t in p.tracks}) > 1)
    majority = []
    for p in people:
        weights: dict[int, int] = {}
        for t in p.tracks:
            weights[seat[t.index]] = weights.get(seat[t.index], 0) + (t.end - t.start)
        majority.append(max(weights, key=lambda s: weights[s]))
    fragments = sum(max(0, majority.count(s) - 1) for s in seats_with_tracks)
    return {
        "people": len(people),
        "seats": len(seats_with_tracks),
        "fragments": fragments,
        "wrong": wrong,
    }


def main() -> int:
    from visiovox_api.config import Settings
    from worker_gpu import av_tse
    from worker_gpu.real_pipeline import _load_embedder

    settings = Settings()
    voice = _load_embedder(settings)
    face = load_embedder()

    cases = []
    for split, meeting, _start in meetings():
        work = OUT / meeting.name
        desc = json.loads((work / "av_tse" / "tracks.json").read_text())
        mixture = read(work / "mixture_eq.wav")
        placed, _ = av_tse.place_tracks(desc, mixture, read)
        speaking = [t for t in placed if not av_tse.is_silent(t, mixture)]
        by_index = {int(t["index"]): t for t in desc["tracks"]}
        seat = {
            t.index: seat_of(
                float(by_index[t.index]["center_x"]), float(by_index[t.index]["center_y"])
            )
            for t in speaking
        }
        faces = {
            t.index: track_vector(crop_video_for(by_index[t.index]["wav"]), face) for t in speaking
        }
        cache: dict[str, np.ndarray] = {}

        def cached_voice(sample: np.ndarray, cache: dict[str, np.ndarray] = cache) -> np.ndarray:
            key = hashlib.sha256(sample.tobytes()).hexdigest()
            if key not in cache:
                cache[key] = voice(sample)
            return cache[key]

        cases.append((split, meeting.name, speaking, seat, faces, cached_voice))
        print(
            f"{meeting.name}: {len(speaking)} speaking tracks, "
            f"{sum(v is not None for v in faces.values())} with a face vector",
            flush=True,
        )

    def run(
        config: dict[str, Any] | None, split: str
    ) -> tuple[dict[str, int], list[dict[str, Any]]]:
        total = {"people": 0, "seats": 0, "fragments": 0, "wrong": 0}
        per = []
        for sp, name, speaking, seat, faces, embed in cases:
            if sp != split:
                continue
            if config is None:
                people = av_tse.group_speakers(speaking, embed, settings.av_tse_merge_threshold)
            else:
                people = av_tse.group_speakers(
                    speaking,
                    embed,
                    settings.av_tse_merge_threshold,
                    faces=faces,
                    face_threshold=config["face_threshold"],
                    face_veto=config["face_veto"],
                )
            s = score(people, seat)
            per.append({"meeting": name, **s})
            for k in total:
                total[k] += s[k]
        return total, per

    base_train, _ = run(None, "train")
    best: tuple[float, float, dict[str, Any]] | None = None
    grid = []
    for th, veto in itertools.product(FACE_THRESHOLDS, VETOES):
        cfg = {"face_threshold": th, "face_veto": veto}
        tot, _ = run(cfg, "train")
        cost = tot["fragments"] + 2 * tot["wrong"]
        grid.append({**cfg, **tot, "cost": cost})
        if best is None or cost < best[0] or (cost == best[0] and th > best[1]):
            best = (cost, th, cfg)
    assert best is not None
    chosen = best[2]
    base_eval, base_eval_per = run(None, "eval")
    face_eval, face_eval_per = run(chosen, "eval")
    face_train, _ = run(chosen, "train")
    result = {
        "chosen": chosen,
        "train": {"voice_only": base_train, "face_voice": face_train},
        "eval": {"voice_only": base_eval, "face_voice": face_eval},
        "eval_per_meeting": {"voice_only": base_eval_per, "face_voice": face_eval_per},
        "grid_train": grid,
    }
    (OUT / "identity.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("chosen", "train", "eval")}, indent=1))
    print("IDENTITY_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
