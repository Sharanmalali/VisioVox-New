"""Signed streaming playlists (apps/api hls.py, routes_hls.py; docs/12 §4)."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from visiovox_api import hls
from visiovox_api.config import get_settings
from visiovox_api.main import app

SETTINGS = get_settings()
NOW = 1_800_000_000.0
KEY = "projects/prj_1/master.m3u8"

MASTER = """#EXTM3U
#EXT-X-VERSION:6

#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Speaker 1",DEFAULT=YES,AUTOSELECT=YES,URI="audio/a0.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Speaker 1",LANGUAGE="en",DEFAULT=NO,AUTOSELECT=NO,URI="subs/s0.m3u8"

#EXT-X-STREAM-INF:BANDWIDTH=1500000,AUDIO="aud",SUBTITLES="subs"
video/v.m3u8
"""

MEDIA = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXTINF:6.0,
a0_000.ts
#EXTINF:4.0,
a0_001.ts
#EXT-X-ENDLIST
"""


async def fake_presign(key: str) -> str:
    return f"https://store.test/{key}?X-Amz-Signature=abc"


def rewrite(text: str, key: str) -> str:
    return asyncio.run(hls.rewrite(text, key, SETTINGS, fake_presign, now=NOW))


def test_a_signed_link_verifies_until_it_expires() -> None:
    url = hls.playlist_url(SETTINGS, KEY, now=NOW)
    exp = int(url.split("exp=")[1].split("&")[0])
    sig = url.split("sig=")[1]
    assert hls.verify(SETTINGS, KEY, exp, sig, now=NOW)
    assert not hls.verify(SETTINGS, KEY, exp, sig, now=exp + 1)
    assert not hls.verify(SETTINGS, "projects/prj_2/master.m3u8", exp, sig, now=NOW)
    assert not hls.verify(SETTINGS, KEY, exp + 1, sig, now=NOW)


def test_master_children_become_signed_playlist_links() -> None:
    out = rewrite(MASTER, KEY)
    assert (
        f"/v1/hls/projects/prj_1/audio/a0.m3u8?exp={int(NOW) + SETTINGS.signed_url_ttl_seconds}"
        in out
    )
    assert "/v1/hls/projects/prj_1/subs/s0.m3u8?exp=" in out
    assert "/v1/hls/projects/prj_1/video/v.m3u8?exp=" in out
    assert 'URI="audio/a0.m3u8"' not in out
    assert out.count("#EXT-X-MEDIA") == 2, "tags are kept, only URIs change"


def test_segments_become_presigned_storage_urls() -> None:
    out = rewrite(MEDIA, "projects/prj_1/audio/a0.m3u8")
    assert "https://store.test/projects/prj_1/audio/a0_000.ts?X-Amz-Signature=abc" in out
    assert "#EXT-X-ENDLIST" in out


def test_nothing_outside_the_project_is_signed() -> None:
    assert hls.resolve("projects/prj_1/audio/a0.m3u8", "../../prj_2/x.ts") is None
    assert hls.resolve("projects/prj_1/master.m3u8", "https://elsewhere/x.ts") is None
    assert (
        hls.resolve("projects/prj_1/audio/a0.m3u8", "../subs/s0.vtt")
        == "projects/prj_1/subs/s0.vtt"
    )
    out = rewrite("#EXTM3U\n../../prj_2/secret.ts\n", "projects/prj_1/audio/a0.m3u8")
    assert "store.test" not in out


def test_legacy_streaming_manifests_fall_back_to_web_audio() -> None:
    legacy = {"playback_hint": "hls", "hls": {"master": "master.m3u8"}, "speakers": []}
    out = hls.normalise_playback(legacy)
    assert out["playback_hint"] == "webaudio"
    assert "hls" not in out
    current = {"playback_hint": "hls", "master_playlist": "https://m/projects/p/master.m3u8"}
    assert hls.normalise_playback(current)["playback_hint"] == "hls"


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/v1/hls/projects/prj_1/master.m3u8?exp=1&sig=00", 403),
        ("/v1/hls/projects/prj_1/video.mp4?exp=1&sig=00", 404),
        ("/v1/hls/u/usr_1/source.m3u8?exp=1&sig=00", 404),
    ],
)
def test_the_endpoint_refuses_bad_links(path: str, code: int) -> None:
    async def go() -> int:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return (await c.get(path)).status_code

    assert asyncio.run(go()) == code
