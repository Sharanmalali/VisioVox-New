"""Crop the end-to-end test screenshots into paper figures.

Sources are Playwright captures of the production site (paper/data/screens/),
taken by the browser tests used to validate the system. Faces in the player
frame are blurred: the paper describes the interface, not the people in the
test recording.
"""

from pathlib import Path

from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "data" / "screens"
OUT = HERE / "figures"


def find_box(img: Image.Image) -> tuple[int, int, int, int]:
    return (0, 0, img.width, img.height)


def studio() -> None:
    im = Image.open(SRC / "studio-mid-processing.png").convert("RGB")
    # Ingest module + activity panel + checklist, without the page chrome.
    box = (196, 200, 1088, 790)
    out = im.crop(box)
    out.save(OUT / "ui_studio.png", dpi=(300, 300))
    print("ui_studio", out.size)


def player() -> None:
    im = Image.open(SRC / "preview-playing.png").convert("RGB")
    # The video frame: blur it heavily so no face is identifiable.
    video = (221, 263, 1059, 738)
    region = im.crop(video).filter(ImageFilter.GaussianBlur(28))
    im.paste(region, video)
    box = (200, 200, 1080, 1110)
    out = im.crop(box)
    out.save(OUT / "ui_player.png", dpi=(300, 300))
    print("ui_player", out.size)


if __name__ == "__main__":
    studio()
    player()
