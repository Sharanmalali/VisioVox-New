"""Crop the end-to-end test screenshots into paper figures.

Sources are Playwright captures of the production site (paper/data/screens/),
taken at 2x by the browser test that validates the system (the PAPER step of
the end-to-end run). Faces in the player frame are blurred: the paper describes
the interface, not the people in the test recording.

    processing.png          full page while a job runs      -> ui_studio.png
    player-transcript.png   the player and transcript       -> ui_player.png,
                            (translated into Hindi)            ui_transcript.png
    player-video-box.json   where the video sits in it (CSS px, scale)
    insights.png            the conversation-insights panel -> ui_insights.png
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


def studio() -> None:
    """The processing view: stage, progress, estimate and the five steps."""
    im = Image.open(SRC / "processing.png").convert("RGB")
    save(im.crop((40, 415, 2520, 1000)), "ui_studio")


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
    # enough to read as a player.
    im.paste(im.crop(video).filter(ImageFilter.GaussianBlur(36)), video)
    # The player column is as wide as the video; the transcript panel starts
    # one grid gap (1.25 rem = 20 CSS px) to its right.
    split = video[2]
    save(im.crop((0, 0, split, im.height)), "ui_player")
    save(im.crop((split + 20 * s, 0, im.width, im.height)), "ui_transcript")


def insights() -> None:
    im = Image.open(SRC / "insights.png").convert("RGB")
    save(im, "ui_insights")


if __name__ == "__main__":
    studio()
    player_and_transcript()
    insights()
