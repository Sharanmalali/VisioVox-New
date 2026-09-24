"""Run the product path over an arbitrary video and write listenable output.

`demo_video.py` scores against the AMI reference tracks, which only exist
because that corpus ships them. This is the case with nothing to lean on: a
file somebody uploaded, no ground truth, no oracle. Every decision -- who is
speaking, when they overlap, what each one sounds like -- comes from the
pipeline itself, which is the only honest way to hear what a user would get.

Because there is no reference there is also no SI-SDR. What can be reported is
how much of the timeline each speaker occupies, how much of it was actually
sent to the model rather than passed through, and whether the visual pathway
found a face to use. The rest is a listening test, which is the point.

Usage:
    uv run python scripts/demo_upload.py --video ~/data/testvideos/test_video.mp4
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml"))

from pipeline import s5_extract
from pipeline.mouth_roi import MouthRois, decode_frames, rois_from_frames
from pipeline.passthrough import FRAME_SAMPLES

RATE = 16_000
FPS = 25


def normalise(video: Path, work: Path) -> tuple[np.ndarray, Path]:
    """16 kHz mono audio and a 25 fps video, which is what the grid assumes.

    S0 does this in the product. Doing it here too, rather than reading the
    file as-is, because a 29.97 fps source would put every mouth ROI on a
    different clock from the audio it is supposed to explain -- a drift that
    grows to most of a second over a minute and silently destroys the visual
    cue rather than announcing itself.
    """
    work.mkdir(parents=True, exist_ok=True)
    wav = work / "mixture.wav"
    mp4 = work / "video25.mp4"
    # Resolved once rather than relying on PATH lookup inside the call, so the
    # argv is fully qualified and the failure mode is a clear error here
    # instead of whatever happens to be named ffmpeg at run time.
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not on PATH")
    subprocess.run(  # noqa: S603
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", str(RATE), str(wav)],
        check=True,
    )  # fmt: skip
    subprocess.run(  # noqa: S603
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(video),
         "-an", "-vf", f"fps={FPS}", str(mp4)],
        check=True,
    )  # fmt: skip
    audio = np.asarray(sf.read(wav, dtype="float32")[0], dtype=np.float32)
    return audio, mp4


def frame_masks(analysis: object, speaker: str, n_frames: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame activity for this speaker and for everyone else combined."""
    target = np.zeros(n_frames, dtype=np.float32)
    others = np.zeros(n_frames, dtype=np.float32)
    for turn in analysis.turns:  # type: ignore[attr-defined]
        lo = max(0, turn.interval.start // FRAME_SAMPLES)
        hi = min(n_frames, turn.interval.end // FRAME_SAMPLES)
        if hi <= lo:
            continue
        if turn.speaker == speaker:
            target[lo:hi] = 1.0
        else:
            others[lo:hi] = 1.0
    return target, others


def enrol(
    mixture: np.ndarray, own: np.ndarray, others: np.ndarray, encoder: object
) -> tuple[np.ndarray, str] | None:
    """ECAPA cue from this speaker cleanest available regions of the mixture.

    Solo frames are preferred and are what S4 looks for. They are not always
    there: an interjector who only ever speaks over someone else has none at
    all, which on this test video is the case for the second speaker. Rather
    than decline to isolate them, the cue falls back to their overlapped
    frames and says so -- a contaminated cue is still informative, and
    refusing produces nothing to listen to.

    This is precisely the situation the visual pathway exists for: when the
    voice cue is weakest, which mouth is moving is the evidence that remains.
    """
    import torch

    solo = (own > 0.5) & (others <= 0.5)
    quality = "clean"
    frames = solo
    if solo.sum() < 25:  # under a second of single-talker speech
        frames = own > 0.5
        quality = "overlapped"
    if frames.sum() < 10:
        return None
    chunks = [mixture[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES] for i in np.flatnonzero(frames)]
    audio = np.concatenate(chunks)[: RATE * 20]
    with torch.no_grad():
        vec = encoder.encode_batch(torch.from_numpy(audio).float().unsqueeze(0))  # type: ignore[attr-defined]
    v = vec.squeeze().cpu().numpy().astype(np.float32)
    n = float(np.linalg.norm(v))
    return (v / n if n > 1e-9 else v), quality


def peak_normalise(x: np.ndarray, peak: float = 0.7) -> np.ndarray:
    m = float(np.abs(x).max())
    return (x * (peak / m)).astype(np.float32) if m > 1e-9 else x


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, default=Path.home() / "runs" / "c3" / "best.pt")
    ap.add_argument("--out", type=Path, default=Path.home() / "runs" / "demo-upload")
    ap.add_argument("--max-speakers", type=int, default=2)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument(
        "--force-extract",
        action="store_true",
        help="send every frame to the model, bypassing the passthrough router",
    )
    args = ap.parse_args(argv)

    work = args.out / "work"
    mixture, video25 = normalise(args.video, work)
    n_frames = len(mixture) // FRAME_SAMPLES
    print(f"{args.video.name}: {len(mixture) / RATE:.1f}s, {n_frames} frames at {FPS} fps")

    # ---- who speaks when -------------------------------------------------
    import os

    from pipeline.s2a_audio import analyse

    # .env.local first: it is the one that carries real secrets, while .env
    # holds the committed placeholder. Reading only .env hands the diariser a
    # None token, and S2a then degrades to VAD with zero speakers rather than
    # failing loudly -- correct behaviour that makes a missing token look like
    # a video with nobody in it.
    root = Path(__file__).resolve().parents[1]
    token = None
    for name in (".env.local", ".env"):
        f = root / name
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            if line.startswith("HF_TOKEN="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                token = token or value or None
    analysis, r2a = analyse(mixture, hf_token=token or os.environ.get("HF_TOKEN"))
    print(f"S2a {r2a.status}: {r2a.detail}")
    print(f"  speakers {analysis.speakers}  overlap {analysis.overlap_ratio:.1%}")
    if not analysis.speakers:
        print("no speakers found; nothing to isolate")
        return 1

    # Busiest speakers first: with more than two present, those are the ones a
    # listener will recognise and can judge the separation on.
    talk_time = {
        s: sum(t.interval.end - t.interval.start for t in analysis.turns if t.speaker == s)
        for s in analysis.speakers
    }
    chosen = sorted(analysis.speakers, key=lambda s: -talk_time[s])[: args.max_speakers]
    print(f"  isolating {chosen}")

    extractor = s5_extract.SeaveExtractor.load(args.checkpoint)
    print(f"checkpoint {args.checkpoint.parent.name} (visual pathway: {extractor.has_video})")

    rois_by_track: list[MouthRois] = []
    if not args.no_video and extractor.has_video:
        from pipeline.s2b_video import analyse_video

        video, _ = analyse_video(video25, max_frames=n_frames)
        frames = decode_frames(video25, n_frames)
        rois_by_track = [rois_from_frames(frames, t, n_frames) for t in video.tracks]
        cover = [f"{r.coverage:.0%}" for r in rois_by_track]
        print(f"  {len(video.tracks)} face tracks, coverage {cover}")

    from speechbrain.inference.speaker import EncoderClassifier

    encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(Path.home() / "models" / "ecapa"),
        run_opts={"device": "cpu"},
    )

    args.out.mkdir(parents=True, exist_ok=True)
    sf.write(args.out / "00_original_mixture.wav", peak_normalise(mixture), RATE)
    print(f"\n{'speaker':>10}  {'talks':>6}  {'routed':>7}  {'video':>6}  {'cue':>11}")

    for k, speaker in enumerate(chosen):
        own, others = frame_masks(analysis, speaker, n_frames)
        got = enrol(mixture, own, others, encoder)
        if got is None:
            print(f"{speaker:>10}  too little speech to enrol from -- skipped")
            continue
        cue, quality = got

        # One face track per isolated speaker, in the same order, or no visual
        # cue at all. S3 binds tracks to voices properly using audio-visual
        # agreement; guessing a binding here would be worse than declining one.
        rois = rois_by_track[k] if len(rois_by_track) >= len(chosen) else None

        # Diagnostic: the router passes single-talker regions through
        # untouched, which is correct and is most of a real timeline -- but it
        # means a listener judging "how well does the model isolate" is mostly
        # hearing their own upload. Forcing every frame through the separator
        # answers the different question of what the model alone can do.
        routed_own, routed_others = own, others
        if args.force_extract:
            routed_own = np.ones_like(own)
            routed_others = np.ones_like(others)

        result = s5_extract.extract_speaker(
            mixture,
            cue,
            extractor,
            target_active=routed_own,
            others_active=routed_others,
            mouth=rois.frames if rois is not None else None,
            visual_confidence=rois.confidence if rois is not None else None,
        )
        name = f"{k + 1}_isolated_{speaker.replace(' ', '_')}.wav"
        sf.write(args.out / name, peak_normalise(result.audio), RATE)
        print(
            f"{speaker:>10}  {own.mean():>5.0%}  {result.processed_fraction:>6.0%}  "
            f"{'yes' if rois is not None else 'no':>6}  {quality:>11}"
        )

    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
