"""Rate limits counted in Redis (EPIC-11 spec, section 2.2, row "Rate limits"; section 5.1).

- The general ceiling (`RATE_LIMIT_PER_MINUTE`, default 300, `0` turns it off) counts every
  route except the health check, for the IP address and, on a `signed_in` route, also for the
  user (the token's `sub`). `install_rate_limits` adds it to the app as a dependency of every
  route, so it runs after the device ID check (a middleware) and after the token check (it asks
  `signed_in`, whose answer the route then reuses), and counts the route's own limits in the
  same step. A request for no route (404, 405) is not counted; a request over a limit is.
- `rate_limit(limit=, window_seconds=, key=)` is a dependency a route adds for its own limit;
  `key` makes the key from the request (a module puts its name in it).
- A window is fixed: it starts with the key's first request and lasts `window_seconds`. The
  count and the expiry are set in one Lua script, so a key never lives without a TTL.
- Over the limit: `429 rate.limited` with `Retry-After`, the whole seconds left in the window,
  rounded up and never 0. Redis not answering: `503 server.unavailable`, and the handler does
  not run.
- The IP address is the last entry of `X-Forwarded-For` (the one the hosting proxy adds), or the
  connection's address when the header is missing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from redis.exceptions import RedisError

from app.platform.access import HEALTH_PATH, route_needs_signed_in, signed_in
from app.platform.errors import RATE_LIMITED, SERVER_UNAVAILABLE, AppError

CEILING_WINDOW_SECONDS = 60
FORWARDED_HEADER = "x-forwarded-for"
KEY_PREFIX = "rate:"
RETRY_AFTER_HEADER = "Retry-After"
_COUNTED_KEY = "rate_limits_counted"

# INCR, and set the expiry when the key has none (it is new): one step, so no key lacks a TTL.
# Returns the count and the milliseconds left.
COUNT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local left = redis.call('PTTL', KEYS[1])
if left < 0 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
  left = tonumber(ARGV[1])
end
return {count, left}
"""


def client_ip(forwarded: str | None, connection: str | None) -> str:
    """The last entry of `X-Forwarded-For`, else the connection's address."""
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return connection or "unknown"


def retry_after_seconds(milliseconds_left: int) -> int:
    """Whole seconds left, rounded up, at least 1."""
    return max(1, -(-milliseconds_left // 1000))


async def seconds_to_wait(redis: Any, key: str, limit: int, window_seconds: int) -> int | None:
    """Count one request for `key`; `None` while within `limit`, else the `Retry-After` seconds."""
    try:
        count, milliseconds_left = await redis.eval(
            COUNT_SCRIPT, 1, KEY_PREFIX + key, window_seconds * 1000
        )
    except (RedisError, OSError, TimeoutError):
        raise AppError(503, SERVER_UNAVAILABLE) from None
    if count <= limit:
        return None
    return retry_after_seconds(int(milliseconds_left))


def _limited(seconds: int) -> AppError:
    return AppError(429, RATE_LIMITED, headers={RETRY_AFTER_HEADER: str(seconds)})


async def enforce(redis: Any, counts: list[tuple[str, int, int]]) -> None:
    """Count each `(key, limit, window_seconds)`; raise `429` when any is over its limit.

    Every key is counted, so a request over one limit still counts for the others. When more
    than one limit is over, `Retry-After` is the shortest wait (the first limit to let go).
    """
    waits = [await seconds_to_wait(redis, key, limit, window) for key, limit, window in counts]
    over = [wait for wait in waits if wait is not None]
    if over:
        raise _limited(min(over))


@dataclass(frozen=True)
class RouteLimit:
    """A route's own limit: `limit` requests per `window_seconds` for the key of the request."""

    limit: int
    window_seconds: int
    key: Callable[[Request], str]

    def count(self, request: Request) -> tuple[str, int, int]:
        return f"route:{self.key(request)}", self.limit, self.window_seconds


def rate_limit(
    *, limit: int, window_seconds: int, key: Callable[[Request], str]
) -> Callable[[Request], Awaitable[None]]:
    """A dependency: a route's own limit, `limit` requests per `window_seconds` for `key(request)`."""
    own = RouteLimit(limit, window_seconds, key)

    async def check(request: Request) -> None:
        if request.scope.setdefault("state", {}).get(_COUNTED_KEY):
            return  # the general ceiling counted it together with its own keys
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

    It also counts the route's own limits in the same step, so that a request over both is
    answered once, with the shortest wait; the route's own check then finds it counted.
    """
    ceiling: int = request.app.state.settings.rate_limit_per_minute
    if ceiling == 0 or request.url.path == HEALTH_PATH:
        return
    connection = request.client.host if request.client else None
    ip = client_ip(request.headers.get(FORWARDED_HEADER), connection)
    counts = [(f"ip:{ip}", ceiling, CEILING_WINDOW_SECONDS)]
    if route_needs_signed_in(request):
        caller = await signed_in(request)  # the token check comes first; the route reuses it
        counts.append((f"user:{caller.user_id}", ceiling, CEILING_WINDOW_SECONDS))
    route = request.scope.get("route")
    if isinstance(route, APIRoute):
        counts.extend(own.count(request) for own in _own_limits(route.dependant))
    request.scope.setdefault("state", {})[_COUNTED_KEY] = True
    await enforce(request.app.state.resources.redis, counts)


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
