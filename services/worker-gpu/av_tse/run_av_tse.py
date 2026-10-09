# mypy: ignore-errors
"""AV-MossFormer2 target speaker extraction over one video (ClearerVoice-Studio).

Runs under ClearerVoice's own interpreter (AV_TSE_PYTHON), not the worker's:
its dependency set - scenedetect, python_speech_features, its own torch build -
has nothing to do with the rest of the pipeline, and keeping it in a separate
process also hands its GPU memory back before Whisper loads.

It is ClearerVoice's `video_process.main` without the demo parts: no annotated
preview video per face and no per-track mp4 re-encodes, which cost more than
the extraction on a long recording. What remains is scene cuts, S3FD face
detection, face tracking, lip crops, and one extraction per face track.

Output is a JSON description of every face track - which frames it covers,
where the face sat, and the path of the separated voice for exactly that span.
Placing those spans on the recording's timeline is the worker's job
(worker_gpu.av_tse), because only the worker holds the reference audio.

    python run_av_tse.py --clearvoice-dir DIR --video IN.mp4 [--audio IN.wav] \
        --work DIR --out tracks.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

MODEL = "AV_MossFormer2_TSE_16K"
FPS = 25

logging.basicConfig(
    format="%(asctime)s av_tse: %(message)s", datefmt="%H:%M:%S", level=logging.INFO
)
log = logging.getLogger("av_tse").info


# Face positions are kept for every BOX_STEP-th frame (25 fps / 6 = ~4 per second).
BOX_STEP = 6


def _ffmpeg(threads: int, *args: str) -> None:
    # List-form arguments, unlike ClearVoice's shell strings, so a path is
    # never re-parsed. Inputs are the worker's own S0 re-encode, not raw uploads.
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-y", "-loglevel", "error", "-threads", str(threads), *args],  # noqa: S607
        check=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clearvoice-dir", required=True, type=Path)
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--audio", type=Path, help="soundtrack to use instead of the video's own")
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--threads", type=int, default=8)
    opts = ap.parse_args()
    video = opts.video.resolve()
    work = opts.work.resolve()
    out = opts.out.resolve()

    # ClearVoice resolves its checkpoint directory relative to the working
    # directory and parses sys.argv for its own options; give it both.
    os.chdir(opts.clearvoice_dir)
    sys.path.insert(0, str(opts.clearvoice_dir.resolve()))
    sys.argv = [sys.argv[0]]

    import cv2
    import numpy as np
    import soundfile as sf
    import torch
    from clearvoice import ClearVoice
    from clearvoice.utils import video_process as vp

    t0 = time.time()
    cv = ClearVoice(task="target_speaker_extraction", model_names=[MODEL])
    speech_model = cv.models[0]
    # ClearVoice silently falls back to the CPU when its nvidia-smi probe
    # fails, but its S3FD face detector is hard-wired to CUDA: the job then
    # dies on a device mismatch minutes in. With a GPU present, use it.
    if torch.cuda.is_available() and speech_model.device.type != "cuda":
        log("ClearVoice chose the CPU although CUDA is available; moving the model to CUDA")
        speech_model.device = torch.device("cuda")
        speech_model.args.use_cuda = 1
        speech_model.model.to(speech_model.device)
    log(f"model loaded in {time.time() - t0:.1f}s on {speech_model.device}")

    paths = {
        "pyaviPath": work / "py_video",
        "pyframesPath": work / "pyframes",
        "pyworkPath": work / "pywork",
        "pycropPath": work / "py_faceTracks",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)

    # ClearVoice's helpers build paths by string formatting, so they get strings.
    video_args = vp.args_param()
    video_args.model = speech_model.model
    video_args.device = speech_model.device
    video_args.sampling_rate = speech_model.args.sampling_rate
    video_args.nDataLoaderThread = opts.threads
    video_args.savePath = str(work)
    video_args.videoPath = str(video)
    for name, p in paths.items():
        setattr(video_args, name, str(p))
    video_args.videoFilePath = str(paths["pyaviPath"] / "video.avi")
    video_args.audioFilePath = str(paths["pyaviPath"] / "audio.wav")
    speech_model.args.device = speech_model.device

    # The same re-encode ClearVoice does: a constant 25 fps grid that the face
    # tracks, the lip crops and the audio spans are all counted on.
    t = opts.threads
    _ffmpeg(
        t,
        "-i",
        str(video),
        "-qscale:v",
        "2",
        "-async",
        "1",
        "-r",
        str(FPS),
        video_args.videoFilePath,
    )
    # The audio comes from --audio when given: the worker's S0 video carries no
    # soundtrack, and its analysis wav is the timeline every later stage uses,
    # so extracting from it leaves nothing to re-align. Without it, the
    # soundtrack of the re-encode, as ClearVoice does.
    _ffmpeg(
        t,
        "-i",
        str(opts.audio.resolve()) if opts.audio else video_args.videoFilePath,
        "-ac",
        "1",
        "-vn",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        video_args.audioFilePath,
    )
    _ffmpeg(
        t,
        "-i",
        video_args.videoFilePath,
        "-qscale:v",
        "2",
        "-f",
        "image2",
        str(paths["pyframesPath"] / "%06d.jpg"),
    )
    frames = sorted(paths["pyframesPath"].glob("*.jpg"))
    if not frames:
        raise SystemExit("no video frames could be decoded")
    height, width = cv2.imread(str(frames[0])).shape[:2]
    log(f"decoded {len(frames)} frames at {width}x{height}")

    with torch.no_grad():
        scene = vp.scene_detect(video_args)
        faces = vp.inference_video(video_args)
        tracks = []
        for shot in scene:
            if shot[1].frame_num - shot[0].frame_num >= video_args.minTrack:
                tracks.extend(
                    vp.track_shot(video_args, faces[shot[0].frame_num : shot[1].frame_num])
                )
        log(f"{len(scene)} shot(s), {len(tracks)} face track(s)")

        crops = [
            vp.crop_video(video_args, track, str(paths["pycropPath"] / f"{i:05d}"))
            for i, track in enumerate(tracks)
        ]
        files = sorted(str(p) for p in paths["pycropPath"].glob("*.avi"))
        estimates = vp.evaluate_network(files, video_args, speech_model.args) if files else []

    # Frames are only needed for detection and cropping; on a long recording
    # they are most of the scratch space.
    shutil.rmtree(paths["pyframesPath"], ignore_errors=True)

    described = []
    for i, (crop, est) in enumerate(zip(crops, estimates, strict=True)):
        frame_idx = np.asarray(crop["track"]["frame"])
        dets = crop["proc_track"]
        wav = paths["pycropPath"] / f"est_{i}.wav"
        sf.write(wav, np.asarray(est, dtype=np.float32).reshape(-1), 16000)
        # Where the face is, every BOX_STEP frames: [frame, centre x, centre y,
        # half-width, half-height], as fractions of the frame. The player draws
        # these to make faces clickable; four per second follows a talking head
        # closely enough and keeps an hour-long track to a few hundred kB.
        xs, ys, ss = (np.asarray(dets[k], dtype=np.float64) for k in ("x", "y", "s"))
        boxes = [
            [
                int(frame_idx[j]),
                round(float(xs[j]) / width, 4),
                round(float(ys[j]) / height, 4),
                round(float(ss[j]) / width, 4),
                round(float(ss[j]) / height, 4),
            ]
            for j in range(0, len(frame_idx), BOX_STEP)
        ]
        described.append(
            {
                "index": i,
                "start_frame": int(frame_idx[0]),
                "end_frame": int(frame_idx[-1]) + 1,
                "center_x": float(np.mean(dets["x"]) / width),
                "center_y": float(np.mean(dets["y"]) / height),
                "face_size": float(np.mean(dets["s"]) * 2 / max(width, height)),
                "boxes": boxes,
                "wav": str(wav),
            }
        )

    out.write_text(
        json.dumps(
            {
                "model": MODEL,
                "fps": FPS,
                "sample_rate": 16000,
                "frames": len(frames),
                "frame_size": [width, height],
                "audio_wav": video_args.audioFilePath,
                "tracks": described,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    log(f"done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
