"""Presigned URLs are signed for the origin a browser can reach (docs/track-w/W9).

In production the store is `minio:9000` on the compose network. A URL signed
for that host is unreachable from a browser, and re-pointing it afterwards
breaks the signature, because SigV4 signs the Host header. Signing makes no
request, so none of this needs a running store.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from visiovox_api.config import Settings
from visiovox_api.storage import ObjectStore


def _store(**overrides: str) -> ObjectStore:
    return ObjectStore(Settings(s3_endpoint_url="http://minio:9000", **overrides))  # type: ignore[arg-type]


async def test_read_urls_use_the_public_origin_when_set() -> None:
    store = _store(s3_public_endpoint_url="https://visiovox.example.me")
    url = urlsplit(await store.presign_get("projects/p1/master.m3u8"))
    assert (url.scheme, url.netloc) == ("https", "visiovox.example.me")
    assert url.path == "/visiovox-media/projects/p1/master.m3u8"


async def test_upload_part_urls_use_the_public_origin_when_set() -> None:
    store = _store(s3_public_endpoint_url="http://localhost:8080")
    parts = await store.presign_parts("u/1/p/2/source/a.mp4", "upload-id", [1, 2])
    assert [p.part_number for p in parts] == [1, 2]
    assert all(urlsplit(p.url).netloc == "localhost:8080" for p in parts)


async def test_without_a_public_origin_the_store_endpoint_is_used() -> None:
    """Development: the browser reaches MinIO on its own published port."""
    url = urlsplit(await _store().presign_get("k"))
    assert url.netloc == "minio:9000"


async def test_no_part_numbers_means_no_urls() -> None:
    assert await _store().presign_parts("k", "id", []) == []
