"""Rate limits counted in Redis (EPIC-11 spec, section 2.2, row "Rate limits"; section 5.1).

- The general ceiling (`RATE_LIMIT_PER_MINUTE`, default 300, `0` turns it off) counts every
  route except the health check, for the IP address and, on a `signed_in` route, also for the
  user (the token's `sub`). `install_rate_limits` adds it to the app as a dependency of every
  route. It runs after the device ID check (a middleware). Step 1 counts the IP address and the
  route's own limits, before the token check, so requests with a bad token are limited too.
  Step 2, on a `signed_in` route, runs the token check (`signed_in`, whose answer the route then
  reuses) and counts the user. A request for no route (404, 405) is not counted; a request over
  a limit is.
- `rate_limit(limit=, window_seconds=, key=)` is a dependency a route adds for its own limit;
  `key` makes the key from the request (a module puts its name in it). It runs before the body
  is read.
- `await count_limit(request, key=, limit=, window_seconds=)` is for a module's service that
  knows its key only later, such as the email of a sign-in (in the body) or a failed invitation
  code. It counts one request for the key (own prefix `count:`), with the same windows, `429`,
  and `503`, and it does not depend on `RATE_LIMIT_PER_MINUTE`. Call it where the attempt is
  known (for a failed attempt only, on that branch).
- Both reject `limit < 1` or `window_seconds < 1` (`ValueError`): a window of 0 would delete the
  key and never limit.
- A window is fixed: it starts with the key's first request and lasts `window_seconds`. All the
  keys of one step are counted in one Lua script (one Redis round trip, `EVALSHA`); the count
  and the expiry are set together, so a key never lives without a TTL.
- Over a limit: `429 rate.limited` with `Retry-After`, the whole seconds left in the window,
  rounded up and never 0; over more than one limit counted together, the longest wait. Redis not
  answering: `503 server.unavailable`, and the handler does not run.
- The IP address is the last entry of `X-Forwarded-For` over all its header lines (the one the
  hosting proxy adds), or the connection's address when the header is missing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from redis.exceptions import NoScriptError, RedisError

from app.platform.access import HEALTH_PATH, route_needs_signed_in, signed_in
from app.platform.errors import RATE_LIMITED, SERVER_UNAVAILABLE, AppError

CEILING_WINDOW_SECONDS = 60
FORWARDED_HEADER = "x-forwarded-for"
KEY_PREFIX = "rate:"
RETRY_AFTER_HEADER = "Retry-After"
# Private to the platform: the ids of the `rate_limit` checks that were counted in this request.
_COUNTED_KEY = "_tripmate_platform_rate_limits_counted"

# For each key: INCR, and set the expiry when the key has none (it is new), in one step, so no
# key lacks a TTL. KEYS are the keys, ARGV their windows in milliseconds. Returns the count and
# the milliseconds left of each key, one after another.
COUNT_SCRIPT = """
local result = {}
for i = 1, #KEYS do
  local count = redis.call('INCR', KEYS[i])
  local left = redis.call('PTTL', KEYS[i])
  if left < 0 then
    redis.call('PEXPIRE', KEYS[i], ARGV[i])
    left = tonumber(ARGV[i])
  end
  result[#result + 1] = count
  result[#result + 1] = left
end
return result
"""
COUNT_SCRIPT_SHA = hashlib.sha1(COUNT_SCRIPT.encode(), usedforsecurity=False).hexdigest()

Count = tuple[str, int, int]  # key, limit, window in seconds


def client_ip(forwarded: str | None, connection: str | None) -> str:
    """The last entry of `X-Forwarded-For` (all its lines joined), else the connection's address."""
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return connection or "unknown"


def retry_after_seconds(milliseconds_left: int) -> int:
    """Whole seconds left, rounded up, at least 1."""
    return max(1, -(-milliseconds_left // 1000))


def _checked(limit: int, window_seconds: int) -> None:
    if limit < 1 or window_seconds < 1:
        raise ValueError("a rate limit needs a limit and a window of at least 1")


async def _hit(redis: Any, counts: list[Count]) -> list[int]:
    """The count and the milliseconds left of each key, from one round trip."""
    keys = [KEY_PREFIX + key for key, _, _ in counts]
    windows = [window * 1000 for _, _, window in counts]
    try:
        try:
            flat = await redis.evalsha(COUNT_SCRIPT_SHA, len(keys), *keys, *windows)
        except NoScriptError:  # Redis has not loaded the script yet (or was restarted)
            flat = await redis.eval(COUNT_SCRIPT, len(keys), *keys, *windows)
    except (RedisError, OSError, TimeoutError):
        raise AppError(503, SERVER_UNAVAILABLE) from None
    return [int(value) for value in flat]


def _limited(seconds: int) -> AppError:
    return AppError(429, RATE_LIMITED, headers={RETRY_AFTER_HEADER: str(seconds)})


async def enforce(redis: Any, counts: list[Count]) -> None:
    """Count each `(key, limit, window_seconds)`; raise `429` when any is over its limit.

    Every key is counted, so a request over one limit still counts for the others. When more
    than one limit is over, `Retry-After` is the longest wait (when every limit has let go).
    """
    if not counts:
        return
    flat = await _hit(redis, counts)
    over = [
        retry_after_seconds(flat[2 * n + 1])
        for n, (_, limit, _) in enumerate(counts)
        if flat[2 * n] > limit
    ]
    if over:
        raise _limited(max(over))


async def count_limit(request: Request, *, key: str, limit: int, window_seconds: int) -> None:
    """Count one request for `key` now (see the module's docstring); `429` or `503` raise."""
    _checked(limit, window_seconds)
    await enforce(request.app.state.resources.redis, [(f"count:{key}", limit, window_seconds)])


@dataclass(frozen=True)
class RouteLimit:
    """A route's own limit: `limit` requests per `window_seconds` for the key of the request."""

    limit: int
    window_seconds: int
    key: Callable[[Request], str]

    def count(self, request: Request) -> Count:
        return f"route:{self.key(request)}", self.limit, self.window_seconds


def _counted(request: Request) -> set[int]:
    counted: set[int] = request.scope.setdefault("state", {}).setdefault(_COUNTED_KEY, set())
    return counted


def rate_limit(
    *, limit: int, window_seconds: int, key: Callable[[Request], str]
) -> Callable[[Request], Awaitable[None]]:
    """A dependency: a route's own limit, `limit` requests per `window_seconds` for `key(request)`."""
    _checked(limit, window_seconds)
    own = RouteLimit(limit, window_seconds, key)

    async def check(request: Request) -> None:
        if id(own) in _counted(request):
            return  # the general ceiling counted this limit together with the IP address
        await enforce(request.app.state.resources.redis, [own.count(request)])

    check.route_limit = own  # type: ignore[attr-defined]
    return check


def _own_limits(dependant: Dependant) -> list[RouteLimit]:
    found: list[RouteLimit] = []
    for dependency in dependant.dependencies:
        own = getattr(dependency.call, "route_limit", None)
        if isinstance(own, RouteLimit):
            found.append(own)
        found.extend(_own_limits(dependency))
    return found


async def general_ceiling(request: Request) -> None:
    """The general ceiling: a dependency of every route (see `install_rate_limits`).

    Step 1, before the token check: the IP address (unless the ceiling is off or this is the
    health check) and the route's own limits, counted together, so a request over several is
    answered once, with the longest wait. Each limit counted is marked, so its own check skips
    it. Step 2, on a `signed_in` route: the token check, then the user.
    """
    ceiling: int = request.app.state.settings.rate_limit_per_minute
    counting = ceiling != 0 and request.url.path != HEALTH_PATH
    route = request.scope.get("route")
    own = _own_limits(route.dependant) if isinstance(route, APIRoute) else []
    counts: list[Count] = []
    if counting:
        connection = request.client.host if request.client else None
        forwarded = ", ".join(request.headers.getlist(FORWARDED_HEADER))
        counts.append((f"ip:{client_ip(forwarded, connection)}", ceiling, CEILING_WINDOW_SECONDS))
    counts.extend(limit.count(request) for limit in own)
    _counted(request).update(id(limit) for limit in own)
    redis = request.app.state.resources.redis
    await enforce(redis, counts)
    if counting and route_needs_signed_in(request):
        caller = await signed_in(request)  # the route reuses this answer
        await enforce(redis, [(f"user:{caller.user_id}", ceiling, CEILING_WINDOW_SECONDS)])


def install_rate_limits(app: FastAPI) -> None:
    """Make the general ceiling a dependency of every route. Call it before routes are added."""
    app.router.dependencies.append(Depends(general_ceiling))


# -- The contract --------------------------------------------------------------------------


def _error_response(description: str, headers: dict[str, Any] | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
    }
    if headers:
        response["headers"] = headers
    return response


def install_rate_limits_contract(app: FastAPI) -> None:
    """Declare in the contract that every operation but the health check may answer `429`
    (with a `Retry-After` header) and `503`, both as `ErrorResponse`.

    Install it after `install_openapi`, whose `ErrorResponse` it refers to.
    """
    documented = app.openapi

    def openapi() -> dict[str, Any]:
        schema = documented()
        for path, item in schema["paths"].items():
            if path == HEALTH_PATH:
                continue
            for operation in item.values():
                responses = operation["responses"]
                responses.setdefault(
                    "429",
                    _error_response(
                        "Too many requests",
                        {
                            RETRY_AFTER_HEADER: {
                                "description": "Whole seconds until the limit's window ends",
                                "schema": {"type": "integer", "minimum": 1},
                            }
                        },
                    ),
                )
                responses.setdefault("503", _error_response("A service the request needs is down"))
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
