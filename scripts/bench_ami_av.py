"""AMI-AV: a real-recording audio-visual extraction benchmark (paper Sec. VII).

AMI records every participant twice: a close-up camera on the face and a
headset on the mouth. Tiling the four close-ups of a meeting into one 2x2
video and pairing it with the sum of the four headsets gives a real
multi-speaker video - real faces, real conversation, real overlap, people
turning away and walking to the whiteboard - in which every speaker's ground
truth is known. Nothing is simulated except the tiling.

Three corrections to the raw AMI sets (each measured before it was made):

  * Speech activity from the hand-aligned AMI words (eval/ami_words.py), not
    from headset energy. Some headsets are nearly dead (-55 dBFS) and carry
    the room within 5 dB of their wearer; an energy detector reported 47 s of
    speech for a participant who says nothing in the window.

  * Equal levels. AMI headset gains differ by up to 30 dB inside one meeting,
    so a raw sum is one or two loud people with the rest 20-27 dB under them.
    Each headset is scaled to the same active-speech level (-26 dBFS) before
    summing. That is the condition the product meets after a loudness-
    normalised upload, and it is the one where every speaker matters.
  * Unreliable references. A few headsets carry other people's voices within
    a few dB of their wearer (loose mic, hot gain). They cannot serve as ground
    truth. Rule, fixed before any system was scored: a reference whose bleed
    is within 10 dB of its own speech, or with under 2 s of speech, is not
    scored. The count is reported.

Each system is run exactly as production runs it. AV-MossFormer2 goes through
`worker_gpu.av_tse`: face tracking and extraction (S5), then placement,
silent-face rejection and voice-based track merging (S3). Each person is
assigned to a headset by where their face sits in the grid - never by which
output scores best, which would hide identity errors.

Per speaker, against the (equalised) headset:

    coverage         fraction of their speech during which their face is tracked
    si_sdri          whole clip
    si_sdri_active   frames where they speak
    si_sdri_overlap  frames where they AND someone else speak
    leak_db          output level where they are silent, relative to where they
                     speak (lower is better); mixture as baseline
    stoi             whole clip. No PESQ: P.862 is specified for 8-12 s
                     samples, not two-minute conversations, and the `pesq`
                     C library aborts the process on some of these clips.
    fragments        production people assigned to their seat (1 is right)

Everything is cached per meeting (equalised audio, placed tracks, visibility),
so later experiments reuse it.

    uv run python scripts/bench_ami_av.py                 # all 30 meetings
    uv run python scripts/bench_ami_av.py --limit 2       # smoke test
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))

from eval.ami_words import activity  # noqa: E402
from eval.metrics import si_sdr, silence_leakage_db, stoi  # noqa: E402
from pipeline.vad import FRAME_SAMPLES  # noqa: E402

AMI = Path.home() / "data" / "ami" / "sets"
OUT = Path.home() / "runs" / "ami-av-bench"
RATE = 16_000
FPS = 25
VAD_RATE = RATE // FRAME_SAMPLES  # 100 mask frames per second
N_SEATS = 4
TARGET_DBFS = -26.0
MAX_BLEED_DB = -10.0
MIN_SPEECH_S = 2.0
MIN_SEGMENT_S = 1.0  # a sub-clip SI-SDR needs at least this much audio


def read(path: Path) -> np.ndarray:
    x, sr = sf.read(path, dtype="float32", always_2d=False)
    assert sr == RATE, (path, sr)
    return np.asarray(x if x.ndim == 1 else x.mean(axis=1), dtype=np.float32)


def meetings() -> list[tuple[str, Path, float]]:
    out = []
    for split in ("eval", "train"):
        for m in json.loads((AMI / f"{split}.json").read_text()):
            out.append((split, AMI / split / m["meeting"], float(m["start_s"])))
    return out


def frame_energy(x: np.ndarray) -> np.ndarray:
    n = x.shape[-1] // FRAME_SAMPLES
    return (x[..., : n * FRAME_SAMPLES].reshape(*x.shape[:-1], n, FRAME_SAMPLES) ** 2).mean(-1)


def equalise(meeting: Path, work: Path, start_s: float) -> dict[str, np.ndarray]:
    """Equal-level references and their sum, plus masks and bleed. Cached."""
    cache = work / "equalised.npz"
    if cache.exists():
        z = np.load(cache)
        return {k: z[k] for k in z.files}
    refs = np.stack([read(meeting / f"ref_spk{k}.wav") for k in range(N_SEATS)])
    # Who speaks when comes from the hand-aligned AMI words, not from energy:
    # once gains are equalised, bleed is loud enough to pass an energy test.
    masks = activity(meeting.name, start_s, refs.shape[1] / RATE, N_SEATS)
    masks = masks[:, : refs.shape[1] // FRAME_SAMPLES]
    energy = frame_energy(refs)
    gains = np.ones(N_SEATS, dtype=np.float32)
    for k in range(N_SEATS):
        if masks[k].any():
            level = 10 * np.log10(energy[k][masks[k]].mean() + 1e-12)
            gains[k] = 10 ** ((TARGET_DBFS - level) / 20)
    refs = refs * gains[:, None]
    mixture = refs.sum(axis=0)
    peak = float(np.abs(mixture).max())
    if peak > 0.99:
        refs, mixture = refs * (0.99 / peak), mixture * (0.99 / peak)
    energy = frame_energy(refs)
    bleed = np.full(N_SEATS, np.nan, dtype=np.float32)
    for k in range(N_SEATS):
        other_only = (~masks[k]) & (masks.sum(axis=0) == 1)
        if masks[k].any() and other_only.any():
            bleed[k] = 10 * np.log10(energy[k][other_only].mean() / energy[k][masks[k]].mean())
    result: dict[str, Any] = {
        "refs": refs.astype(np.float32),
        "mixture": mixture.astype(np.float32),
        "masks": masks,
        "gains_db": 20 * np.log10(gains),
        "bleed_db": bleed,
    }
    sf.write(work / "mixture_eq.wav", result["mixture"], RATE)
    np.savez_compressed(cache, **result)
    return result


def build_mosaic(meeting: Path, dest: Path) -> None:
    """2x2 grid: seat 0 | seat 1 over seat 2 | seat 3, 25 fps, no soundtrack."""
    if dest.exists():
        return
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for k in range(N_SEATS):
        cmd += ["-i", str(meeting / f"face_spk{k}.mp4")]
    cmd += [
        "-filter_complex",
        "[0:v][1:v][2:v][3:v]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[v]",
        "-map", "[v]", "-an", "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "20", str(dest),
    ]  # fmt: skip
    subprocess.run(cmd, check=True)  # noqa: S603 - fixed argv


def seat_of(cx: float, cy: float) -> int:
    return (2 if cy >= 0.5 else 0) + (1 if cx >= 0.5 else 0)


def run_av(work: Path, n_samples: int) -> dict[str, Any]:
    """AV-MossFormer2 through the production S5 + S3 code. Cached."""
    cache = work / "av_tracks.npz"
    if cache.exists():
        z = np.load(cache)
        return {k: z[k] for k in z.files}

    from visiovox_api.config import Settings
    from worker_gpu import av_tse
    from worker_gpu.real_pipeline import _load_embedder

    settings = Settings()
    mixture_path = work / "mixture_eq.wav"
    mixture = read(mixture_path)[:n_samples]
    video = work / "mosaic.mp4"

    t0 = time.time()
    desc = av_tse.run_extractor(video, mixture_path, work / "av_tse", settings)
    s5 = time.time() - t0
    placed, lag = av_tse.place_tracks(desc, mixture, read)
    speaking = [t for t in placed if not av_tse.is_silent(t, mixture)]
    embed = _load_embedder(settings) if len(speaking) > 1 else None
    people = av_tse.group_speakers(speaking, embed, settings.av_tse_merge_threshold)

    by_index = {int(t["index"]): t for t in desc["tracks"]}
    n_vad = n_samples // FRAME_SAMPLES
    # Where a face is tracked in each seat, whether or not it was kept.
    visible = np.zeros((N_SEATS, n_vad), dtype=bool)
    for t in desc["tracks"]:
        s = seat_of(float(t["center_x"]), float(t["center_y"]))
        a = int(t["start_frame"]) * VAD_RATE // FPS
        b = int(t["end_frame"]) * VAD_RATE // FPS
        visible[s, a:b] = True

    estimates = np.zeros((N_SEATS, n_samples), dtype=np.float32)
    owners = np.zeros(N_SEATS, dtype=np.int32)
    purity = []
    for person in people:
        weights = np.zeros(N_SEATS)
        for tr in person.tracks:
            d = by_index[tr.index]
            weights[seat_of(float(d["center_x"]), float(d["center_y"]))] += tr.end - tr.start
        s = int(np.argmax(weights))
        estimates[s] += person.audio[:n_samples]
        owners[s] += 1
        purity.append(weights[s] / weights.sum())
    result: dict[str, Any] = {
        "estimates": estimates,
        "visible": visible,
        "owners": owners,
        "purity": np.asarray(purity, dtype=np.float32),
        "n_tracks": np.array(len(desc["tracks"])),
        "n_speaking": np.array(len(speaking)),
        "n_people": np.array(len(people)),
        "lag": np.array(lag),
        "s5_seconds": np.array(s5),
    }
    np.savez_compressed(cache, **result)
    return result


def segment_si_sdr(x: np.ndarray, ref: np.ndarray, frames: np.ndarray) -> float:
    """SI-SDR over the concatenation of the selected 10 ms frames."""
    n = min(len(frames), len(x) // FRAME_SAMPLES)
    idx = np.flatnonzero(frames[:n])
    if len(idx) * FRAME_SAMPLES < MIN_SEGMENT_S * RATE:
        return float("nan")
    take = (idx[:, None] * FRAME_SAMPLES + np.arange(FRAME_SAMPLES)[None, :]).reshape(-1)
    if not np.any(x[take]):
        return float("-inf")
    return si_sdr(x[take], ref[take])


def segment_si_sdri(est: np.ndarray, ref: np.ndarray, mix: np.ndarray, frames: np.ndarray) -> float:
    return segment_si_sdr(est, ref, frames) - segment_si_sdr(mix, ref, frames)


def score(
    estimates: np.ndarray,
    eq: dict[str, np.ndarray],
    visible: np.ndarray,
) -> list[dict[str, Any]]:
    refs, mixture, masks = eq["refs"], eq["mixture"], eq["masks"]
    others = masks.sum(axis=0)
    rows: list[dict[str, Any]] = []
    for k in range(N_SEATS):
        est, ref = estimates[k], refs[k]
        active = masks[k]
        n = min(len(active), visible.shape[1])
        overlap = active & ((others - active) >= 1)
        speech_s = float(active.sum()) / VAD_RATE
        bleed = float(eq["bleed_db"][k])
        reliable = speech_s >= MIN_SPEECH_S and np.isfinite(bleed) and bleed <= MAX_BLEED_DB
        has_output = bool(np.any(est))
        vis = np.zeros_like(active)
        vis[:n] = visible[k, :n]
        rows.append(
            {
                "speaker": k,
                "reliable": reliable,
                "bleed_db": bleed,
                "speech_s": speech_s,
                "overlap_s": float(overlap.sum()) / VAD_RATE,
                "coverage": float((active & vis).sum() / max(active.sum(), 1)),
                "has_output": has_output,
                "si_sdri": si_sdr(est, ref) - si_sdr(mixture, ref) if has_output else float("-inf"),
                "si_sdri_active": segment_si_sdri(est, ref, mixture, active),
                "si_sdri_overlap": segment_si_sdri(est, ref, mixture, overlap),
                "si_sdri_overlap_visible": segment_si_sdri(est, ref, mixture, overlap & vis),
                "mix_si_sdr": si_sdr(mixture, ref),
                "mix_si_sdr_overlap": segment_si_sdr(mixture, ref, overlap),
                "leak_db": silence_leakage_db(est, active, FRAME_SAMPLES)
                if has_output
                else float("nan"),
                "leak_db_mixture": silence_leakage_db(mixture, active, FRAME_SAMPLES),
                "stoi": stoi(est, ref) if has_output else float("nan"),
                "stoi_mixture": stoi(mixture, ref),
            }
        )
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    todo = meetings()[: args.limit or None]
    results: list[dict[str, Any]] = []
    for i, (split, meeting, start_s) in enumerate(todo, 1):
        work = OUT / meeting.name
        work.mkdir(exist_ok=True)
        t0 = time.time()
        eq = equalise(meeting, work, start_s)
        build_mosaic(meeting, work / "mosaic.mp4")
        n = len(eq["mixture"])
        av = run_av(work, n)
        rows = score(np.asarray(av["estimates"]), eq, np.asarray(av["visible"]))
        for r in rows:
            r.update(
                meeting=meeting.name,
                split=split,
                system="av_mossformer2",
                people=int(av["n_people"]),
                tracks=int(av["n_tracks"]),
                fragments=int(np.asarray(av["owners"])[r["speaker"]]),
            )
        results += rows
        ok = [r for r in rows if r["reliable"]]
        ov = [r["si_sdri_overlap"] for r in ok if np.isfinite(r["si_sdri_overlap"])]
        print(
            f"[{i}/{len(todo)}] {meeting.name}: tracks {int(av['n_tracks'])} -> people "
            f"{int(av['n_people'])}; seats {np.asarray(av['owners']).tolist()}; "
            f"coverage {[round(r['coverage'], 2) for r in rows]}; reliable {len(ok)}/4; "
            f"overlap SI-SDRi {np.mean(ov) if ov else float('nan'):+.2f} dB; "
            f"{time.time() - t0:.0f}s",
            flush=True,
        )
        (OUT / "results.json").write_text(json.dumps(results, indent=1, default=float))
    print("BENCH_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
