"""Card photos for projects packaged before they existed (ml/pipeline/face_card.py).

Reads each project's local copy (MEDIA_ROOT/projects/<id>): its packaged
video and each speaker's face boxes (caption JSON `faces.boxes`), cuts
`spk_<n>_face.jpg` and uploads it beside the project's other media. The
database is not touched: the API attaches a photo that exists in storage to a
speaker whose stored manifest has none (visiovox_api.photos).

Projects packaged before face boxes were kept (2026-10-09) have nothing to
cut from and are skipped.

    set -a; . ./.env.production; . ./.env.worker; set +a   # the worker's environment
    uv run python scripts/backfill_card_photos.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "services" / "worker-gpu" / "src"))

from pipeline.face_card import write_card_photo  # noqa: E402


def frame_size(video: Path) -> tuple[int, int] | None:
    proc = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video),
        ],
        check=False, capture_output=True, text=True,
    )  # fmt: skip
    try:
        w, h = (int(v) for v in proc.stdout.strip().split(",")[:2])
    except ValueError:
        return None
    return w, h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from visiovox_api.config import get_settings
    from worker_gpu.tasks import _s3_client

    settings = get_settings()

    client = None if args.dry_run else _s3_client(settings)
    root = Path(settings.media_root) / "projects"
    made = skipped = 0
    for project in sorted(p for p in root.iterdir() if p.is_dir()):
        video = project / "video.mp4"
        size = frame_size(video) if video.is_file() else None
        if size is None:
            continue
        everyone: dict[str, list[list[float]]] = {}
        for captions in sorted(project.glob("spk_*.json")):
            try:
                body = json.loads(captions.read_text())
                everyone[captions.stem.split("_")[1]] = body.get("faces", {}).get("boxes", [])
            except (json.JSONDecodeError, AttributeError):
                everyone[captions.stem.split("_")[1]] = []
        for ordinal, boxes in everyone.items():
            photo = project / f"spk_{ordinal}_face.jpg"
            others = [b for o, b in everyone.items() if o != ordinal]
            if not boxes:
                skipped += 1
                continue
            if args.dry_run:
                made += 1
                continue
            # Already cut on an earlier run: upload again rather than skip, so a
            # run interrupted between cutting and uploading still completes.
            if photo.is_file() or write_card_photo(video, boxes, size, photo, others=others):
                client.upload_file(  # type: ignore[union-attr]
                    str(photo),
                    settings.s3_bucket,
                    f"projects/{project.name}/{photo.name}",
                    ExtraArgs={"ContentType": "image/jpeg"},
                )
                made += 1
    sys.stdout.write(f"card photos made: {made}; speakers without face boxes: {skipped}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
