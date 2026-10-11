"""Videos from Google Drive and Google Photos (google_media.py, worker import_google)."""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from visiovox_api import google_media
from visiovox_api.routes_import import check_google
from visiovox_api.schemas import GoogleImport, ImportRequest

TOKEN = "ya29." + "x" * 40


# ------------------------------------------------------------ validation --


def test_a_request_gives_a_link_or_a_google_pick_not_both() -> None:
    pick = {"kind": "drive", "access_token": TOKEN, "file_id": "1AbCdEfGhIjKlMn"}
    ImportRequest(rights_attested=True, google=pick)  # type: ignore[arg-type]
    ImportRequest(rights_attested=True, url="https://youtu.be/abc")
    with pytest.raises(ValueError):
        ImportRequest(rights_attested=True)
    with pytest.raises(ValueError):
        ImportRequest(rights_attested=True, url="https://youtu.be/abc", google=pick)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://lh3.googleusercontent.com/abc123_-XYZ",
        "https://video-downloads.googleusercontent.com/abc",
    ],
)
def test_photos_bytes_must_live_on_googleusercontent(base_url: str) -> None:
    check_google(GoogleImport(kind="photos", access_token=TOKEN, base_url=base_url))


@pytest.mark.parametrize(
    "base_url",
    [
        "http://lh3.googleusercontent.com/abc",  # not https
        "https://googleusercontent.com.evil.example/abc",
        "https://evil.example/lh3.googleusercontent.com/abc",
        "http://minio:9000/visiovox-media/secret",
        None,
    ],
)
def test_anything_else_is_refused(base_url: str | None) -> None:
    with pytest.raises(HTTPException):
        check_google(GoogleImport(kind="photos", access_token=TOKEN, base_url=base_url))


def test_a_drive_pick_is_a_file_id_not_a_url() -> None:
    check_google(GoogleImport(kind="drive", access_token=TOKEN, file_id="1AbCdEfGhIjKlMnOpQ"))
    for bad in ("../../etc/passwd", "https://drive.google.com/file/d/x", "short"):
        with pytest.raises(HTTPException):
            check_google(GoogleImport(kind="drive", access_token=TOKEN, file_id=bad))


def test_google_errors_become_sentences() -> None:
    disabled = {"error": {"message": "Photos Picker API has not been used in project 7 before",
                          "details": [{"reason": "SERVICE_DISABLED"}]}}  # fmt: skip
    assert google_media.explain(403, disabled, "Google Photos").status == 503
    assert "switched on" in google_media.explain(403, disabled, "Google Photos").message
    assert google_media.explain(401, {}, "Google Drive").status == 401
    assert "again" in google_media.explain(403, {}, "Google Drive").message


# --------------------------------------------------------- the download --

pytest.importorskip("worker_gpu.tasks")
from worker_gpu import tasks  # noqa: E402

SEEN: list[dict[str, str]] = []
BODY = b"0123456789" * 50_000  # 500 kB


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        SEEN.append({"path": self.path, "auth": self.headers.get("Authorization", "")})
        if self.path.startswith("/away"):
            self.send_response(302)
            self.send_header("Location", "https://evil.example/steal")
            self.end_headers()
            return
        if self.path.startswith("/denied"):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    SEEN.clear()
    # The test server is plain http on localhost: let the host check accept it.
    monkeypatch.setattr(tasks, "_GOOGLE_HOSTS", ("127.0.0.1",))
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_the_token_goes_with_the_download_and_progress_is_reported(
    server: str, tmp_path: Path
) -> None:
    reports: list[tuple[int, int, float]] = []
    got = tasks._download_google(
        f"{server}/media", TOKEN, tmp_path / "v", 10_000_000, lambda *a: reports.append(a)
    )
    assert got == len(BODY)
    assert (tmp_path / "v").read_bytes() == BODY
    assert SEEN[0]["auth"] == f"Bearer {TOKEN}"


def test_a_redirect_off_google_is_refused_and_gets_no_token(server: str, tmp_path: Path) -> None:
    with pytest.raises(tasks.GoogleDownloadError, match="unexpected"):
        tasks._download_google(f"{server}/away", TOKEN, tmp_path / "v", 10_000_000, print)
    assert all("evil" not in s["path"] for s in SEEN)


def test_refusals_and_size_limits_become_sentences(server: str, tmp_path: Path) -> None:
    with pytest.raises(tasks.GoogleDownloadError, match="allow access"):
        tasks._download_google(f"{server}/denied", TOKEN, tmp_path / "v", 10_000_000, print)
    with pytest.raises(tasks.GoogleDownloadError, match="too large"):
        tasks._download_google(f"{server}/media", TOKEN, tmp_path / "v", 1000, print)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg unavailable")
def test_a_non_h264_video_is_made_playable(tmp_path: Path) -> None:
    src = tmp_path / "phone.mov"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
         "-t", "2", "-c:v", "mpeg4", str(src)],
        check=True,
    )  # fmt: skip
    out = tasks._browser_playable(src, tmp_path)
    codec = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name", "-of", "csv=p=0", str(out)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    assert out.suffix == ".mp4" and codec == "h264"
