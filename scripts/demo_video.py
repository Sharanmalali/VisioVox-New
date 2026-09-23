"""Run the extractor over a real video and write something you can listen to.

`demo_audio.py` renders simulated mixtures, where a ground-truth target exists
because we built the mixture. This is the other question: take an actual
recording of actual people interrupting each other, run the product path over
it, and hear what comes back.

The AMI clips carry per-speaker reference tracks alongside the mixture, so the
result can be scored as well as heard — on real room audio rather than a
simulation of one.

Two honest notes about what this does and does not demonstrate:

- **Routing is oracle.** Who is speaking when comes from the reference tracks,
  not from S2a diarisation. That isolates the separator: a poor result here is
  the model's fault rather than the diariser's. The product path runs the real
  diariser, and its errors are additive on top of whatever this shows.
- **Enrolment is not.** Each speaker's cue is built from single-talker regions
  of the *mixture*, which is what S4 does at inference. Taking it from the
  reference track would leak the answer and flatter the result.

Usage:
    uv run python scripts/demo_video.py --clip ES2002a --checkpoint ~/runs/c3/best.pt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml"))

from pipeline import s5_extract
from pipeline.mouth_roi import MouthRois, decode_frames, rois_from_frames
from pipeline.passthrough import FRAME_SAMPLES

CLIPS = Path.home() / "data" / "testvideos" / "clips"
RATE = 16_000


def si_sdr(estimate: np.ndarray, reference: np.ndarray) -> float:
    """Scale-invariant SDR in dB, on the region the reference is active."""
    ref = reference - reference.mean()
    est = estimate - estimate.mean()
    denom = float(np.dot(ref, ref)) + 1e-12
    projection = (float(np.dot(est, ref)) / denom) * ref
    noise = est - projection
    num = float(np.dot(projection, projection)) + 1e-12
    den = float(np.dot(noise, noise)) + 1e-12
    return 10.0 * float(np.log10(num / den))


def active_frames(reference: np.ndarray, n_frames: int, floor: float = 0.15) -> np.ndarray:
    """Per-frame speech activity for one reference track.

    Thresholded against the track's own *speech* level -- 15% of its 95th
    percentile frame energy -- rather than against its peak.

    `passthrough.frame_activity` uses 5% of peak, which is right for a
    normalised training chunk and wrong for an AMI headset. These tracks have
    enormous crest factors: ES2002a speaker 0 has an RMS of 0.0024 against a
    peak of 0.167, so one loud transient sets a threshold that the speaker's
    actual speech never reaches. Measured that way the clip showed 0.1% overlap
    and a +12 dB mixture SI-SDR -- i.e. nothing to separate -- which is the same
    mistake that made the first round of AMI numbers meaningless. A percentile
    is robust to exactly that: a single transient cannot move it.
    """
    n = len(reference) // FRAME_SAMPLES
    energy = np.sqrt((reference[: n * FRAME_SAMPLES].reshape(n, FRAME_SAMPLES) ** 2).mean(axis=1))
    threshold = max(float(np.percentile(energy, 95)) * floor, 1e-4)
    mask = (energy > threshold).astype(np.float32)
    if len(mask) < n_frames:
        mask = np.concatenate([mask, np.zeros(n_frames - len(mask), dtype=np.float32)])
    return mask[:n_frames]


def enrol_from_mixture(
    mixture: np.ndarray, own: np.ndarray, others: np.ndarray, encoder: object
) -> np.ndarray | None:
    """ECAPA embedding from the mixture's single-talker regions for this speaker.

    Deliberately not taken from the reference track. At inference there is no
    reference, only the mixture, and a cue built from clean isolated audio is a
    cue the product cannot obtain -- scoring against it would measure a system
    nobody can deploy.
    """
    import torch

    solo = (own > 0.5) & (others <= 0.5)
    if solo.sum() < 25:  # under a second of clean speech
        return None
    chunks = [mixture[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES] for i in np.flatnonzero(solo)]
    audio = np.concatenate(chunks)[: RATE * 20]
    with torch.no_grad():
        emb = encoder.encode_batch(torch.from_numpy(audio).float().unsqueeze(0))  # type: ignore[attr-defined]
    vector = emb.squeeze().cpu().numpy().astype(np.float32)
    norm = float(np.linalg.norm(vector))
    unit: np.ndarray = vector / norm if norm > 1e-9 else vector
    return unit


def enrol_from_reference(
    reference: np.ndarray, own: np.ndarray, encoder: object
) -> np.ndarray | None:
    """Oracle cue, from the speaker's own clean track. Diagnostic only."""
    import torch

    if own.sum() < 25:
        return None
    chunks = [reference[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES] for i in np.flatnonzero(own)]
    audio = np.concatenate(chunks)[: RATE * 20]
    with torch.no_grad():
        emb = encoder.encode_batch(torch.from_numpy(audio).float().unsqueeze(0))  # type: ignore[attr-defined]
    vector = emb.squeeze().cpu().numpy().astype(np.float32)
    norm = float(np.linalg.norm(vector))
    unit: np.ndarray = vector / norm if norm > 1e-9 else vector
    return unit


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clip", default="ES2002a")
    ap.add_argument("--checkpoint", type=Path, default=Path.home() / "runs" / "c3" / "best.pt")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--no-video", action="store_true", help="force the audio-only path")
    ap.add_argument(
        "--oracle-enrol",
        action="store_true",
        help="build the cue from the reference track -- diagnostic only, leaks the answer",
    )
    args = ap.parse_args(argv)

    clip = CLIPS / args.clip
    mixture, rate = sf.read(clip / "mixture.wav", dtype="float32")
    assert rate == RATE, f"expected {RATE} Hz, got {rate}"
    mixture = np.asarray(mixture, dtype=np.float32)
    n_frames = len(mixture) // FRAME_SAMPLES

    refs = sorted(clip.glob("ref_spk*.wav"))
    references = [np.asarray(sf.read(p, dtype="float32")[0], dtype=np.float32) for p in refs]
    activity = [active_frames(r, n_frames) for r in references]
    print(f"{args.clip}: {len(mixture) / RATE:.0f}s, {len(refs)} reference speakers")
    for i, a in enumerate(activity):
        print(f"  spk{i} speaks in {a.mean():.0%} of frames")

    # Overlap is what the product exists for, so report how much there is.
    stack = np.stack(activity)
    overlapped = (stack.sum(axis=0) >= 2).mean()
    print(f"  two or more at once: {overlapped:.1%} of the timeline")

    out = args.out or (clip / "demo" / args.checkpoint.parent.name)
    out.mkdir(parents=True, exist_ok=True)

    extractor = s5_extract.SeaveExtractor.load(args.checkpoint)
    print(f"checkpoint {args.checkpoint} (video pathway: {extractor.has_video})")

    from speechbrain.inference.speaker import EncoderClassifier

    encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(Path.home() / "models" / "ecapa"),
        run_opts={"device": "cpu"},
    )

    rois_by_track: list[MouthRois] = []
    if not args.no_video and extractor.has_video:
        from pipeline.s2b_video import analyse_video

        video, _ = analyse_video(clip / "input.mp4", max_frames=n_frames)
        frames = decode_frames(clip / "input.mp4", n_frames)
        rois_by_track = [rois_from_frames(frames, t, n_frames) for t in video.tracks]
        print(f"  {len(video.tracks)} face tracks found")

    sf.write(out / "00_mixture.wav", mixture, RATE)
    print(f"\n{'speaker':>8}  {'mixture':>8}  {'output':>8}  {'gain':>7}  {'routed':>7}")

    for i, reference in enumerate(references):
        own = activity[i]
        others = np.clip(sum(a for j, a in enumerate(activity) if j != i), 0, 1)

        if args.oracle_enrol:
            # Separates "the separator cannot do this" from "the cue was bad".
            # Not a product number: at inference no reference exists.
            emb = enrol_from_reference(reference, own, encoder)
        else:
            emb = enrol_from_mixture(mixture, own, others, encoder)
        if emb is None:
            print(f"{'spk' + str(i):>8}  no clean enrolment region -- skipped")
            continue

        # Face tracks are not bound to diarised speakers here; with one track
        # per speaker the i-th is used, and with any other count the visual
        # cue is dropped rather than guessed at. S3 does this binding properly
        # in the product, using audio-visual activity agreement.
        rois = rois_by_track[i] if len(rois_by_track) == len(references) else None

        result = s5_extract.extract_speaker(
            mixture,
            emb,
            extractor,
            target_active=own,
            others_active=others,
            mouth=rois.frames if rois is not None else None,
            visual_confidence=rois.confidence if rois is not None else None,
        )

        # Scored only where this speaker actually talks: including their silence
        # would average in regions the router sends to zero, which says nothing
        # about separation quality.
        voiced = np.repeat(own > 0.5, FRAME_SAMPLES)[: len(mixture)]
        if voiced.sum() < RATE:
            continue
        before = si_sdr(mixture[voiced], reference[voiced])
        after = si_sdr(result.audio[voiced], reference[voiced])

        sf.write(out / f"spk{i}_isolated.wav", result.audio, RATE)
        sf.write(out / f"spk{i}_reference.wav", reference, RATE)
        print(
            f"{'spk' + str(i):>8}  {before:>+7.2f}  {after:>+7.2f}  {after - before:>+6.2f}  "
            f"{result.processed_fraction:>6.0%}"
        )

    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
