"""F6 (code agent's own tests): the rules of the rate limits, with no PostgreSQL or Redis.

A fake Redis stands in for the script of `COUNT_SCRIPT`; the real one is used by the tests of
tests/api/platform/test_F6_rate_limits.py. Covers: the IP address from `X-Forwarded-For`,
`Retry-After` rounding, the counting of each key, `503`, the contract (`429` with `Retry-After`
and `503` on every operation but the health check), and the setting `RATE_LIMIT_PER_MINUTE`.
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI, Request
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import NoScriptError

from app.platform import rate_limits
from app.platform.access import (
    Caller,
    install_access_contract,
    install_access_keys,
    install_device_id,
    public,
    signed_in,
)
from app.platform.config import SettingsError, load_settings
from app.platform.errors import (
    PLATFORM_ERROR_CODES,
    AppError,
    install_error_handlers,
    install_openapi,
    install_request_id,
)
from app.platform.health import Resources
from app.platform.modules import Registry
from app.platform.rate_limits import (
    COUNT_SCRIPT,
    COUNT_SCRIPT_SHA,
    client_ip,
    count_limit,
    enforce,
    install_rate_limits,
    install_rate_limits_contract,
    rate_limit,
    retry_after_seconds,
)

ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5432/d",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "RELEASE_VERSION": "x",
}
ERROR_REF = {"$ref": "#/components/schemas/ErrorResponse"}


class FakeRedis:
    """Counts like `COUNT_SCRIPT`: INCR, and the expiry set when the key is new."""

    def __init__(
        self, *, left_ms: int = 60_000, fail: Exception | None = None, loaded: bool = True
    ) -> None:
        self.counts: dict[str, int] = {}
        self.calls: list[tuple[Any, ...]] = []  # (script or sha, numkeys, *keys and windows)
        self.left_ms = left_ms
        self.fail = fail
        self.loaded = loaded

    def _run(self, numkeys: int, args: tuple[Any, ...]) -> list[int]:
        if self.fail is not None:
            raise self.fail
        result: list[int] = []
        for key in args[:numkeys]:
            self.counts[key] = self.counts.get(key, 0) + 1
            result += [self.counts[key], self.left_ms]
        return result

    async def evalsha(self, sha: str, numkeys: int, *args: Any) -> list[int]:
        self.calls.append((sha, numkeys, *args))
        if not self.loaded:
            raise NoScriptError("NOSCRIPT")
        return self._run(numkeys, args)

    async def eval(self, script: str, numkeys: int, *args: Any) -> list[int]:
        self.calls.append((script, numkeys, *args))
        self.loaded = True
        return self._run(numkeys, args)


def _status(error: AppError) -> tuple[int, str, dict[str, str] | None]:
    return error.status, error.code, error.headers


# -- The IP address ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("forwarded", "connection", "expected"),
    [
        ("1.1.1.1", "9.9.9.9", "1.1.1.1"),
        ("1.1.1.1, 2.2.2.2", "9.9.9.9", "2.2.2.2"),
        ("1.1.1.1,2.2.2.2,   3.3.3.3  ", "9.9.9.9", "3.3.3.3"),
        ("  4.4.4.4 ", "9.9.9.9", "4.4.4.4"),
        (None, "9.9.9.9", "9.9.9.9"),
        ("", "9.9.9.9", "9.9.9.9"),
        ("1.1.1.1, ", "9.9.9.9", "9.9.9.9"),
        (None, None, "unknown"),
    ],
)
def test_F6_client_ip_is_the_last_forwarded_entry_else_the_connection(
    forwarded: str | None, connection: str | None, expected: str
) -> None:
    assert client_ip(forwarded, connection) == expected


# -- Retry-After ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("milliseconds", "seconds"),
    [
        (0, 1),
        (1, 1),
        (999, 1),
        (1000, 1),
        (1001, 2),
        (1999, 2),
        (2000, 2),
        (59_001, 60),
        (60_000, 60),
    ],
)
def test_F6_retry_after_is_whole_seconds_rounded_up_and_never_0(
    milliseconds: int, seconds: int
) -> None:
    assert retry_after_seconds(milliseconds) == seconds


# -- Counting ------------------------------------------------------------------------------


def test_F6_the_script_counts_every_key_and_sets_the_expiry_in_one_step() -> None:
    for word in ("INCR", "PTTL", "PEXPIRE", "KEYS[i]", "ARGV[i]", "#KEYS", "return"):
        assert word in COUNT_SCRIPT
    assert len(COUNT_SCRIPT_SHA) == 40


async def test_F6_up_to_the_limit_passes_and_one_over_waits_the_seconds_left() -> None:
    redis = FakeRedis(left_ms=2_500)
    await enforce(redis, [("k", 2, 10)])
    await enforce(redis, [("k", 2, 10)])
    with pytest.raises(AppError) as raised:
        await enforce(redis, [("k", 2, 10)])
    assert _status(raised.value) == (429, "rate.limited", {"Retry-After": "3"})
    assert redis.calls[0] == (COUNT_SCRIPT_SHA, 1, "rate:k", 10_000)


async def test_F6_all_keys_go_in_one_call_and_the_script_is_sent_when_redis_lacks_it() -> None:
    redis = FakeRedis(loaded=False)
    await enforce(redis, [("a", 5, 60), ("b", 5, 10)])
    assert redis.calls == [
        (COUNT_SCRIPT_SHA, 2, "rate:a", "rate:b", 60_000, 10_000),
        (COUNT_SCRIPT, 2, "rate:a", "rate:b", 60_000, 10_000),
    ]
    await enforce(redis, [("a", 5, 60)])
    assert redis.calls[-1][0] == COUNT_SCRIPT_SHA  # loaded now: no second `eval`
    assert len(redis.calls) == 3


async def test_F6_nothing_to_count_asks_redis_nothing() -> None:
    await enforce(FakeRedis(fail=OSError("never called")), [])


async def test_F6_redis_failing_is_503_server_unavailable() -> None:
    for failure in (RedisConnectionError("x"), OSError("x"), TimeoutError()):
        with pytest.raises(AppError) as raised:
            await enforce(FakeRedis(fail=failure), [("k", 5, 10)])
        assert _status(raised.value) == (503, "server.unavailable", None)


async def test_F6_every_key_is_counted_and_the_longest_wait_is_sent() -> None:
    redis = FakeRedis(left_ms=5_000)
    redis.counts = {"rate:a": 9, "rate:b": 9}
    with pytest.raises(AppError) as raised:
        await enforce(redis, [("a", 1, 60), ("b", 1, 60), ("c", 1, 60)])
    assert _status(raised.value) == (429, "rate.limited", {"Retry-After": "5"})
    assert redis.counts == {"rate:a": 10, "rate:b": 10, "rate:c": 1}


async def test_F6_the_longest_of_two_waits_is_the_retry_after() -> None:
    class Waits(FakeRedis):
        def _run(self, numkeys: int, args: tuple[Any, ...]) -> list[int]:
            result: list[int] = []
            for key in args[:numkeys]:
                result += [5, 59_000 if key.endswith("long") else 9_000]
            return result

    with pytest.raises(AppError) as raised:
        await enforce(Waits(), [("long", 1, 60), ("short", 1, 10)])
    assert raised.value.headers == {"Retry-After": "59"}
    with pytest.raises(AppError) as raised:
        await enforce(Waits(), [("short", 1, 10), ("long", 1, 60)])
    assert raised.value.headers == {"Retry-After": "59"}


async def test_F6_a_key_within_its_limit_does_not_make_the_request_wait() -> None:
    await enforce(FakeRedis(), [("a", 1, 60), ("b", 3, 60)])


def _request(app: FastAPI, **headers: str) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "method": "GET",
            "path": "/",
        }
    )


def _app_with(redis: FakeRedis) -> FastAPI:
    app = FastAPI()
    app.state.resources = Resources("x", None, redis)  # type: ignore[arg-type]
    return app


async def test_F6_a_routes_own_limit_counts_its_own_key() -> None:
    redis = FakeRedis()
    request = _request(_app_with(redis), **{"x-key": "abc"})
    check = rate_limit(limit=1, window_seconds=7, key=lambda r: "m:" + r.headers["x-key"])
    await check(request)
    with pytest.raises(AppError):
        await check(request)
    assert [call[2:] for call in redis.calls] == [("rate:route:m:abc", 7000)] * 2


async def test_F6_a_limit_not_declared_on_the_route_is_counted_when_the_ceiling_is_on() -> None:
    redis = FakeRedis()
    request = _request(_app_with(redis))
    request.scope["state"] = {"_tripmate_platform_rate_limits_counted": {id(object())}}
    check = rate_limit(limit=1, window_seconds=7, key=lambda r: "m:x")
    await check(request)
    with pytest.raises(AppError):
        await check(request)
    assert redis.counts == {"rate:route:m:x": 2}


@pytest.mark.parametrize(("limit", "window"), [(0, 5), (-1, 5), (5, 0), (5, -3), (0, 0)])
async def test_F6_a_limit_or_window_under_1_is_refused_when_it_is_declared(
    limit: int, window: int
) -> None:
    with pytest.raises(
        ValueError, match=r"^a rate limit needs a limit and a window of at least 1$"
    ):
        rate_limit(limit=limit, window_seconds=window, key=lambda r: "k")
    with pytest.raises(
        ValueError, match=r"^a rate limit needs a limit and a window of at least 1$"
    ):
        await count_limit(
            _request(_app_with(FakeRedis())), key="k", limit=limit, window_seconds=window
        )


def test_F6_a_limit_and_window_of_1_are_accepted() -> None:
    rate_limit(limit=1, window_seconds=1, key=lambda r: "k")


async def test_F6_a_service_counts_a_key_itself_with_its_own_prefix_and_ceiling_off() -> None:
    redis = FakeRedis(left_ms=1_200)
    request = _request(_app_with(redis))
    await count_limit(request, key="signin:a@b.c", limit=2, window_seconds=900)
    await count_limit(request, key="signin:a@b.c", limit=2, window_seconds=900)
    with pytest.raises(AppError) as raised:
        await count_limit(request, key="signin:a@b.c", limit=2, window_seconds=900)
    assert _status(raised.value) == (429, "rate.limited", {"Retry-After": "2"})
    assert redis.counts == {"rate:count:signin:a@b.c": 3}
    assert redis.calls[0] == (COUNT_SCRIPT_SHA, 1, "rate:count:signin:a@b.c", 900_000)
    with pytest.raises(AppError) as down:
        await count_limit(
            _request(_app_with(FakeRedis(fail=OSError("x")))), key="k", limit=1, window_seconds=1
        )
    assert down.value.status == 503


# -- The pipeline --------------------------------------------------------------------------


def _pem(key: Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


def _token(key: Ed25519PrivateKey, user: uuid.UUID) -> str:
    now = int(time.time())
    claims = {"sub": str(user), "sid": str(uuid.uuid4()), "iat": now, "exp": now + 900}
    return jwt.encode(claims, key, algorithm="EdDSA")


def _app(
    key: Ed25519PrivateKey, redis: FakeRedis, ceiling: str | None = None
) -> tuple[FastAPI, list[str]]:
    ran: list[str] = []
    env = {**ENV, "ACCESS_TOKEN_PUBLIC_KEYS": _pem(key)}
    if ceiling is not None:
        env["RATE_LIMIT_PER_MINUTE"] = ceiling
    app = FastAPI()
    install_error_handlers(app)
    install_openapi(app)
    install_access_contract(app)
    install_rate_limits_contract(app)
    install_rate_limits(app)
    app.state.settings = load_settings(env)
    install_access_keys(app, app.state.settings.access_token_public_keys)
    app.state.resources = Resources("x", None, redis)  # type: ignore[arg-type]
    registry = Registry()

    async def active(session_id: uuid.UUID) -> bool:
        return True

    registry.set_session_hook(active)
    app.state.registry = registry

    @app.get("/v1/health", dependencies=[Depends(public)])
    async def health() -> dict[str, str]:
        ran.append("health")
        return {}

    @app.get("/v1/open", dependencies=[Depends(public)])
    async def open_route() -> dict[str, str]:
        ran.append("open")
        return {}

    @app.get("/v1/me")
    async def me(caller: Annotated[Caller, Depends(signed_in)]) -> dict[str, str]:
        ran.append("me")
        return {}

    @app.get(
        "/v1/own",
        dependencies=[
            Depends(public),
            Depends(rate_limit(limit=1, window_seconds=5, key=lambda r: "m:" + r.headers["x-k"])),
        ],
    )
    async def own() -> dict[str, str]:
        ran.append("own")
        return {}

    @app.get("/v1/attempt", dependencies=[Depends(public)])
    async def attempt(request: Request, ok: bool = True) -> dict[str, str]:
        ran.append("attempt")
        if not ok:  # only a failed attempt counts
            await count_limit(request, key="attempt:a", limit=1, window_seconds=30)
        return {}

    install_device_id(app)
    install_request_id(app)
    return app, ran


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("7.7.7.7", 1)), base_url="http://test"
    )


def _headers(**extra: str) -> dict[str, str]:
    return {"X-Device-Id": str(uuid.uuid4()), **extra}


async def test_F6_a_public_route_counts_the_ip_only_and_the_health_check_nothing() -> None:
    redis = FakeRedis()
    app, ran = _app(Ed25519PrivateKey.generate(), redis, ceiling="2")
    async with _client(app) as client:
        await client.get("/v1/health")
        first = await client.get(
            "/v1/open", headers=_headers(**{"X-Forwarded-For": "1.1.1.1, 2.2.2.2"})
        )
        await client.get("/v1/open", headers=_headers())
        await client.get("/v1/open", headers=_headers())
        over = await client.get("/v1/open", headers=_headers())
        missing = await client.get("/v1/nothing", headers=_headers())
    assert first.status_code == 200
    assert redis.counts == {"rate:ip:2.2.2.2": 1, "rate:ip:7.7.7.7": 3}
    assert missing.status_code == 404  # an unknown path is not counted
    assert over.status_code == 429
    assert over.headers["Retry-After"] == "60"
    assert over.json()["error"]["code"] == "rate.limited"
    assert ran == ["health", "open", "open", "open"]
    assert all(call[-1] == 60_000 for call in redis.calls)
    assert (
        len(redis.calls) == 4
    )  # one round trip per counted request; health and the unknown path none


async def test_F6_a_signed_in_route_counts_the_ip_before_the_token_check_and_the_user_after() -> (
    None
):
    key = Ed25519PrivateKey.generate()
    user = uuid.uuid4()
    redis = FakeRedis()
    app, ran = _app(key, redis, ceiling="2")
    async with _client(app) as client:
        no_token = await client.get("/v1/me", headers=_headers())
        # the IP address is counted before the token check; the user only after it
        assert redis.counts == {"rate:ip:7.7.7.7": 1}
        assert len(redis.calls) == 1
        auth = {"Authorization": f"Bearer {_token(key, user)}"}
        first = await client.get("/v1/me", headers=_headers(**auth))
        over = await client.get("/v1/me", headers=_headers(**auth))
    assert no_token.status_code == 401
    assert first.status_code == 200
    assert over.status_code == 429
    assert redis.counts == {
        "rate:ip:7.7.7.7": 3,
        f"rate:user:{user}": 1,
    }  # over by IP: no user count
    assert ran == ["me"]


async def test_F6_the_default_ceiling_is_300_and_0_turns_it_off() -> None:
    redis = FakeRedis()
    redis.counts = {"rate:ip:7.7.7.7": 300}
    app, _ = _app(Ed25519PrivateKey.generate(), redis)
    async with _client(app) as client:
        over = await client.get("/v1/open", headers=_headers())
    assert over.status_code == 429

    off = FakeRedis(fail=OSError("never called"))
    app, ran = _app(Ed25519PrivateKey.generate(), off, ceiling="0")
    async with _client(app) as client:
        response = await client.get("/v1/open", headers=_headers())
    assert response.status_code == 200
    assert off.calls == []
    assert ran == ["open"]


async def test_F6_redis_down_is_503_and_the_handler_does_not_run_but_health_is_not_counted() -> (
    None
):
    app, ran = _app(Ed25519PrivateKey.generate(), FakeRedis(fail=OSError("down")), ceiling="5")
    async with _client(app) as client:
        down = await client.get("/v1/open", headers=_headers())
        health = await client.get("/v1/health")
    assert down.status_code == 503
    assert down.json()["error"]["code"] == "server.unavailable"
    assert "Retry-After" not in down.headers
    assert health.status_code == 200
    assert ran == ["health"]


async def test_F6_a_route_over_both_its_own_limit_and_the_ceiling_is_counted_once_per_key() -> None:
    redis = FakeRedis()
    redis.counts = {"rate:ip:7.7.7.7": 5}
    app, ran = _app(Ed25519PrivateKey.generate(), redis, ceiling="5")
    async with _client(app) as client:
        response = await client.get("/v1/own", headers=_headers(**{"X-K": "a"}))
    assert response.status_code == 429
    assert ran == []
    assert redis.counts == {"rate:ip:7.7.7.7": 6, "rate:route:m:a": 1}


async def test_F6_a_routes_own_limit_is_counted_by_its_own_check_when_the_ceiling_is_off() -> None:
    redis = FakeRedis()
    app, ran = _app(Ed25519PrivateKey.generate(), redis, ceiling="0")
    async with _client(app) as client:
        first = await client.get("/v1/own", headers=_headers(**{"X-K": "a"}))
        over = await client.get("/v1/own", headers=_headers(**{"X-K": "a"}))
    assert (first.status_code, over.status_code) == (200, 429)
    assert over.headers["Retry-After"] == "60"
    assert redis.counts == {"rate:route:m:a": 2}
    assert ran == ["own"]


# -- The contract --------------------------------------------------------------------------


def test_F6_every_operation_but_health_documents_429_with_retry_after_and_503() -> None:
    app, _ = _app(Ed25519PrivateKey.generate(), FakeRedis())
    contract = app.openapi()
    assert app.openapi() is contract
    seen = 0
    for path, item in contract["paths"].items():
        for method, operation in item.items():
            seen += 1
            responses = operation["responses"]
            if path == "/v1/health":
                assert "429" not in responses
                assert "503" not in responses
                continue
            too_many = responses["429"]
            assert too_many["description"] == "Too many requests", (method, path)
            assert too_many["content"]["application/json"]["schema"] == ERROR_REF
            assert too_many["headers"]["Retry-After"] == {
                "description": "Whole seconds until the limit's window ends",
                "schema": {"type": "integer", "minimum": 1},
            }
            unavailable = responses["503"]
            assert unavailable["description"] == "A service the request needs is down"
            assert unavailable["content"]["application/json"]["schema"] == ERROR_REF
            assert "headers" not in unavailable
    assert seen == 5


def test_F6_a_response_already_documented_is_not_overwritten() -> None:
    app, _ = _app(Ed25519PrivateKey.generate(), FakeRedis())
    contract = app.openapi()
    contract["paths"]["/v1/open"]["get"]["responses"]["503"] = {"description": "mine"}
    assert app.openapi()["paths"]["/v1/open"]["get"]["responses"]["503"] == {"description": "mine"}


def test_F6_rate_limited_is_a_platform_error_code() -> None:
    assert rate_limits.RATE_LIMITED in PLATFORM_ERROR_CODES
    assert "rate.limited" in PLATFORM_ERROR_CODES


# -- The setting ---------------------------------------------------------------------------


def test_F6_the_setting_defaults_to_300_and_takes_0_and_more() -> None:
    assert load_settings(ENV).rate_limit_per_minute == 300
    assert load_settings({**ENV, "RATE_LIMIT_PER_MINUTE": "0"}).rate_limit_per_minute == 0
    assert load_settings({**ENV, "RATE_LIMIT_PER_MINUTE": "45"}).rate_limit_per_minute == 45


@pytest.mark.parametrize("value", ["-1", "-300", "many", "1.5"])
def test_F6_a_wrong_setting_is_rejected_by_name_and_never_shows_its_value(value: str) -> None:
    with pytest.raises(SettingsError) as raised:
        load_settings({**ENV, "RATE_LIMIT_PER_MINUTE": value})
    assert "RATE_LIMIT_PER_MINUTE" in str(raised.value)
    assert value not in str(raised.value)


async def test_F6_a_passing_request_counts_a_routes_own_key_once_with_the_ceiling_on() -> None:
    redis = FakeRedis()
    app, ran = _app(Ed25519PrivateKey.generate(), redis, ceiling="5")
    async with _client(app) as client:
        response = await client.get("/v1/own", headers=_headers(**{"X-K": "a"}))
    assert response.status_code == 200
    assert ran == ["own"]
    assert redis.counts == {"rate:ip:7.7.7.7": 1, "rate:route:m:a": 1}


async def test_F6_a_request_with_no_connection_address_counts_for_unknown() -> None:
    redis = FakeRedis()
    app, _ = _app(Ed25519PrivateKey.generate(), redis, ceiling="5")
    scope = {"type": "http", "app": app, "headers": [], "method": "GET", "path": "/v1/open"}
    await rate_limits.general_ceiling(Request(scope))
    assert redis.counts == {"rate:ip:unknown": 1}


async def test_F6_two_forwarded_for_lines_count_the_last_entry_of_the_last_line() -> None:
    redis = FakeRedis()
    app, _ = _app(Ed25519PrivateKey.generate(), redis, ceiling="5")
    headers = [
        ("X-Device-Id", str(uuid.uuid4())),
        ("X-Forwarded-For", "10.9.9.9"),  # the client's own line
        ("X-Forwarded-For", "3.3.3.3"),  # the proxy's line
    ]
    async with _client(app) as client:
        await client.get("/v1/open", headers=headers)
    assert redis.counts == {"rate:ip:3.3.3.3": 1}


async def test_F6_a_bad_token_is_limited_by_ip_before_the_token_check() -> None:
    redis = FakeRedis()
    redis.counts = {"rate:ip:7.7.7.7": 2}
    app, ran = _app(Ed25519PrivateKey.generate(), redis, ceiling="2")
    async with _client(app) as client:
        response = await client.get("/v1/me", headers=_headers(Authorization="Bearer nope"))
    assert response.status_code == 429
    assert ran == []


async def test_F6_a_service_counts_only_the_failed_attempts_with_the_ceiling_off() -> None:
    redis = FakeRedis()
    app, ran = _app(Ed25519PrivateKey.generate(), redis, ceiling="0")
    async with _client(app) as client:
        good = [await client.get("/v1/attempt", headers=_headers()) for _ in range(3)]
        first_failure = await client.get("/v1/attempt?ok=false", headers=_headers())
        second_failure = await client.get("/v1/attempt?ok=false", headers=_headers())
    assert [r.status_code for r in good] == [200, 200, 200]
    assert first_failure.status_code == 200
    assert second_failure.status_code == 429
    assert second_failure.headers["Retry-After"] == "60"
    assert redis.counts == {"rate:count:attempt:a": 2}
    assert ran == ["attempt"] * 5


async def test_F6_a_caller_planted_in_the_request_state_is_not_taken_for_a_token() -> None:
    app, ran = _app(Ed25519PrivateKey.generate(), FakeRedis(), ceiling="0")

    @app.middleware("http")
    async def plant(request: Request, call_next: Any) -> Any:
        request.state.caller = Caller(uuid.uuid4(), uuid.uuid4())
        return await call_next(request)

    async with _client(app) as client:
        response = await client.get("/v1/me", headers=_headers())
    assert response.status_code == 401
    assert ran == []


async def test_F6_the_wait_of_the_key_that_is_over_is_the_one_sent() -> None:
    class Lefts(FakeRedis):
        def _run(self, numkeys: int, args: tuple[Any, ...]) -> list[int]:
            return [1, 11_000, 4, 22_000, 1, 33_000][: 2 * numkeys]

    with pytest.raises(AppError) as raised:
        await enforce(Lefts(), [("a", 1, 60), ("b", 3, 60), ("c", 1, 60)])
    assert raised.value.headers == {"Retry-After": "22"}


def test_F6_the_counted_limits_are_kept_in_the_requests_state_under_a_private_key() -> None:
    request = _request(_app_with(FakeRedis()))
    rate_limits._counted(request).add(7)
    assert rate_limits._counted(request) == {7}
    assert request.scope["state"] == {"_tripmate_platform_rate_limits_counted": {7}}
