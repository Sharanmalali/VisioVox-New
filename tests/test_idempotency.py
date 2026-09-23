"""Idempotency keys (docs/11 §1).

Against a fake Redis: the behaviour worth pinning is the state machine, which
is deterministic and would only be obscured by real infrastructure.

The exponential-backoff tests that lived here went with ADR-0016 — Firebase
rate-limits sign-in attempts now, and `authguard` no longer exists.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException

from visiovox_api import idempotency
from visiovox_api.config import Settings


class FakeRedis:
    """Enough of the client for these two modules, including TTL semantics."""

    def __init__(self, fail: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = fail

    def _guard(self) -> None:
        if self.fail:
            raise ConnectionError("redis is down")

    async def set(
        self, key: str, value: str, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        self._guard()
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex:
            self.ttls[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        self._guard()
        return self.store.get(key)

    async def delete(self, *keys: str) -> int:
        self._guard()
        n = 0
        for key in keys:
            n += 1 if self.store.pop(key, None) is not None else 0
            self.ttls.pop(key, None)
        return n

    async def incr(self, key: str) -> int:
        self._guard()
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, ttl: int) -> bool:
        self._guard()
        self.ttls[key] = ttl
        return True

    async def ttl(self, key: str) -> int:
        self._guard()
        return self.ttls.get(key, -2)


def _settings(**overrides: Any) -> Settings:
    return Settings(**overrides)


# --------------------------------------------------------------------------
# idempotency: key validation
# --------------------------------------------------------------------------


def test_absent_key_is_allowed_when_optional() -> None:
    assert idempotency.validate_key(None, required=False) is None


def test_absent_key_is_rejected_when_required() -> None:
    with pytest.raises(HTTPException) as exc:
        idempotency.validate_key(None, required=True)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("bad", ["short", "x" * 201])
def test_key_length_is_bounded(bad: str) -> None:
    """It becomes part of a Redis key and is client-supplied."""
    with pytest.raises(HTTPException):
        idempotency.validate_key(bad, required=False)


def test_key_is_trimmed() -> None:
    assert idempotency.validate_key("  abcdefgh  ", required=False) == "abcdefgh"


# --------------------------------------------------------------------------
# idempotency: the state machine
# --------------------------------------------------------------------------


async def test_first_request_wins_the_reservation() -> None:
    redis = FakeRedis()
    replay = await idempotency.begin(
        redis, _settings(), user_id="u1", endpoint="e", client_key="k" * 10, body_fingerprint="f"
    )
    assert replay is None


async def test_second_request_while_in_flight_is_409() -> None:
    """Replaying a half-finished operation is worse than telling the client to
    wait."""
    redis = FakeRedis()
    args: dict[str, str] = {
        "user_id": "u1",
        "endpoint": "e",
        "client_key": "k" * 10,
        "body_fingerprint": "f",
    }
    await idempotency.begin(redis, _settings(), **args)
    with pytest.raises(HTTPException) as exc:
        await idempotency.begin(redis, _settings(), **args)
    assert exc.value.status_code == 409
    assert exc.value.headers is not None
    assert "Retry-After" in exc.value.headers


async def test_completed_request_is_replayed() -> None:
    redis = FakeRedis()
    args: dict[str, str] = {
        "user_id": "u1",
        "endpoint": "e",
        "client_key": "k" * 10,
        "body_fingerprint": "f",
    }
    await idempotency.begin(redis, _settings(), **args)
    await idempotency.complete(redis, **args, status_code=201, body={"id": "prj_1"})

    replay = await idempotency.begin(redis, _settings(), **args)
    assert replay is not None
    assert replay.status_code == 201
    assert replay.body == {"id": "prj_1"}


async def test_same_key_different_body_is_422() -> None:
    """The same key naming two operations is a client bug, not a retry."""
    redis = FakeRedis()
    await idempotency.begin(
        redis, _settings(), user_id="u1", endpoint="e", client_key="k" * 10, body_fingerprint="a"
    )
    with pytest.raises(HTTPException) as exc:
        await idempotency.begin(
            redis,
            _settings(),
            user_id="u1",
            endpoint="e",
            client_key="k" * 10,
            body_fingerprint="b",
        )
    assert exc.value.status_code == 422


async def test_keys_are_scoped_per_user() -> None:
    """Clients pick predictable keys. Without the user in the hash, one person
    would read another's stored response - a leak, not a collision."""
    redis = FakeRedis()
    args: dict[str, str] = {"endpoint": "e", "client_key": "k" * 10, "body_fingerprint": "f"}
    await idempotency.begin(redis, _settings(), user_id="u1", **args)
    assert await idempotency.begin(redis, _settings(), user_id="u2", **args) is None


async def test_keys_are_scoped_per_endpoint() -> None:
    redis = FakeRedis()
    args: dict[str, str] = {"user_id": "u1", "client_key": "k" * 10, "body_fingerprint": "f"}
    await idempotency.begin(redis, _settings(), endpoint="a", **args)
    assert await idempotency.begin(redis, _settings(), endpoint="b", **args) is None


async def test_release_allows_a_retry_after_a_failed_handler() -> None:
    """Without release, a transient error parks the key for 24 hours and the
    client can never retry that operation."""
    redis = FakeRedis()
    args: dict[str, str] = {"user_id": "u1", "endpoint": "e", "client_key": "k" * 10}
    await idempotency.begin(redis, _settings(), **args, body_fingerprint="f")
    await idempotency.release(redis, **args)
    assert await idempotency.begin(redis, _settings(), **args, body_fingerprint="f") is None


async def test_redis_down_degrades_to_no_idempotency() -> None:
    """Which is exactly where the endpoint was before this existed."""
    redis = FakeRedis(fail=True)
    replay = await idempotency.begin(
        redis,
        _settings(rate_limit_fail_open=True),
        user_id="u1",
        endpoint="e",
        client_key="k" * 10,
        body_fingerprint="f",
    )
    assert replay is None


async def test_complete_never_raises_when_redis_is_down() -> None:
    await idempotency.complete(
        FakeRedis(fail=True),
        user_id="u1",
        endpoint="e",
        client_key="k" * 10,
        body_fingerprint="f",
        status_code=201,
        body={},
    )


def test_fingerprint_is_order_independent() -> None:
    """Otherwise a client that serialises its JSON differently on retry would be
    told its key was reused for a different request."""
    assert idempotency.fingerprint({"a": 1, "b": 2}) == idempotency.fingerprint({"b": 2, "a": 1})


def test_fingerprint_distinguishes_different_bodies() -> None:
    assert idempotency.fingerprint({"a": 1}) != idempotency.fingerprint({"a": 2})


async def test_stored_record_carries_no_raw_key() -> None:
    """The Redis key is a hash; the value should not reintroduce the plaintext."""
    redis = FakeRedis()
    args: dict[str, str] = {
        "user_id": "u1",
        "endpoint": "e",
        "client_key": "secret-key-value",
        "body_fingerprint": "f",
    }
    await idempotency.begin(redis, _settings(), **args)
    for stored_key, stored in redis.store.items():
        assert "secret-key-value" not in stored_key
        assert "secret-key-value" not in json.dumps(stored)
