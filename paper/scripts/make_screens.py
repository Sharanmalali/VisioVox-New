"""Crop the end-to-end test screenshots into paper figures.

Sources are Playwright captures of the production site (paper/data/screens/),
taken at 2x, element by element, from a test account processing AMI meeting
ES2002a (~/runs/paper-shots/capture.mjs). Faces in the player frame are blurred:
the paper describes the interface, not the people in the test recording.

    processing.png          the progress panel while a job  -> ui_processing.png
                            runs (separation stage)
    player-transcript.png   the player and transcript       -> ui_player.png,
                            (translated into Hindi)            ui_transcript.png
    player-video-box.json   where the video sits in it (CSS px, scale)
    insights.png            the conversation-insights panel -> ui_insights.png
    faces.png, faces.json   the stage with a target on each -> ui_faces.png
                            face (CSS px boxes, scale)
    mixer.png               the voice mixer                 -> ui_mixer.png
    dub.png, dub.json       the dub panel; its video and    -> ui_dub.png
                            photo boxes
"""

import json
from pathlib import Path

from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "data" / "screens"
OUT = HERE / "figures"


def save(img: Image.Image, name: str) -> None:
    img.save(OUT / f"{name}.png", dpi=(300, 300))
    print(name, img.size)


def processing() -> None:
    """The progress panel: stage, progress, time left and the five steps."""
    im = Image.open(SRC / "processing.png").convert("RGB")
    save(im, "ui_processing")


def player_and_transcript() -> None:
    im = Image.open(SRC / "player-transcript.png").convert("RGB")
    box = json.loads((SRC / "player-video-box.json").read_text())
    s = box["scale"]
    video = (
        round(box["x"] * s),
        round(box["y"] * s),
        round((box["x"] + box["width"]) * s),
        round((box["y"] + box["height"]) * s),
    )
    # Blur heavily so no face is identifiable; the controls stay legible
    # enough to read as a player. The speaker cards carry face photos too.
    im.paste(im.crop(video).filter(ImageFilter.GaussianBlur(36)), video)
    _blur_photos(im, box.get("photos", []), s)
    # The player column is as wide as the video; the transcript panel starts
    # one grid gap (1.25 rem = 20 CSS px) to its right.
    split = video[2]
    save(im.crop((0, 0, split, im.height)), "ui_player")
    save(im.crop((split + 20 * s, 0, im.width, im.height)), "ui_transcript")


def _px(box: dict[str, float], s: float, inset: float = 0.0) -> tuple[int, int, int, int]:
    """A CSS-pixel box at scale `s`, shrunk by `inset` of its size on each side."""
    dx, dy = box["width"] * inset, box["height"] * inset
    return (
        round((box["x"] + dx) * s),
        round((box["y"] + dy) * s),
        round((box["x"] + box["width"] - dx) * s),
        round((box["y"] + box["height"] - dy) * s),
    )


def _blur_photos(im: Image.Image, boxes: list[dict[str, float]], s: float) -> None:
    """Blur each speaker card's face photo (CSS-pixel boxes at scale `s`)."""
    for b in boxes:
        px = _px(b, s)
        im.paste(im.crop(px).filter(ImageFilter.GaussianBlur(24)), px)


def faces() -> None:
    """The stage with its face targets. Faces are blurred inside the target
    rings so that the rings and name tags, which are the point, stay sharp."""
    im = Image.open(SRC / "faces.png").convert("RGB")
    meta = json.loads((SRC / "faces.json").read_text())
    s = meta["scale"]
    # A light blur over the whole frame, a heavy one over each face.
    video = _px(meta["video"], s)
    im.paste(im.crop(video).filter(ImageFilter.GaussianBlur(10)), video)
    for box in meta["faces"]:
        inner = _px(box, s, inset=0.12)
        im.paste(im.crop(inner).filter(ImageFilter.GaussianBlur(28)), inner)
    save(im, "ui_faces")


def mixer_and_dub() -> None:
    """The mixer, and the dub panel with its video and photos blurred.

    Saved apart: the figure sets them side by side, since stacked they are
    taller than a column."""
    save(Image.open(SRC / "mixer.png").convert("RGB"), "ui_mixer")
    dub = Image.open(SRC / "dub.png").convert("RGB")
    meta = json.loads((SRC / "dub.json").read_text())
    video = _px(meta["video"], meta["scale"])
    dub.paste(dub.crop(video).filter(ImageFilter.GaussianBlur(36)), video)
    _blur_photos(dub, meta.get("photos", []), meta["scale"])
    save(dub, "ui_dub")


def insights() -> None:
    im = Image.open(SRC / "insights.png").convert("RGB")
    save(im, "ui_insights")


if __name__ == "__main__":
    processing()
    player_and_transcript()
    insights()
    faces()
    mixer_and_dub()
