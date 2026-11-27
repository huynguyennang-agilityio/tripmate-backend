"""F6 Rate limits: cases F6-1 to F6-5 (epic-11 spec, sections 2.2 "Rate limits" and 2.3).

The app runs in process against the real Redis of the test run (`TEST_REDIS_URL`); PostgreSQL
stays on a closed port, as no route here uses it. The general ceiling is made small through
`RATE_LIMIT_PER_MINUTE`, and the route with its own limit uses a short window, so no test waits
a whole minute. Each caller has its own made-up IP address in `X-Forwarded-For`, so no two tests
share a counter. The interface is in tests/INTERFACE.md, section "F6: Rate limits".
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from tests.api.platform.f6_support import (
    CEILING_SETTING,
    CEILING_WINDOW_SECONDS,
    PUBLIC_KEYS_SETTING,
    assert_limited,
    assert_passes,
    headers,
    new_ip,
    new_private_key,
    public_pem,
    seconds_left_bounds,
    token_for,
    write_probe,
)
from tests.support import build_app, client_for, real_redis_url


@pytest.fixture
def limits(set_settings, monkeypatch: pytest.MonkeyPatch):
    """Settings with the real Redis and the general ceiling given; returns a signing key."""

    def _set(ceiling: int | None = None):
        set_settings(REDIS_URL=real_redis_url())
        if ceiling is None:
            monkeypatch.delenv(CEILING_SETTING, raising=False)
        else:
            monkeypatch.setenv(CEILING_SETTING, str(ceiling))
        private_key = new_private_key()
        monkeypatch.setenv(PUBLIC_KEYS_SETTING, public_pem(private_key))
        return private_key

    return _set


# -- F6-1 ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F6_1_requests_up_to_the_limit_each_pass(limits, module_folder) -> None:
    limits(ceiling=3)
    probe = write_probe(module_folder, route_limit=2, route_window=10)
    ceiling_ip, route_ip = new_ip(), new_ip()

    async with client_for(build_app()) as client:
        up_to_ceiling = [
            await client.get(probe.open, headers=headers(ceiling_ip)) for _ in range(3)
        ]
        up_to_route_limit = [
            await client.get(probe.limited, headers=headers(route_ip, probe_key="a"))
            for _ in range(2)
        ]

    for n, response in enumerate(up_to_ceiling, start=1):
        assert_passes(response, f"request {n} of a ceiling of 3")
    for n, response in enumerate(up_to_route_limit, start=1):
        assert_passes(response, f"request {n} of a route limit of 2")
    assert probe.calls() == ["open"] * 3 + ["limited"] * 2


# -- F6-2 ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F6_2_one_request_over_the_limit_is_429_with_retry_after_the_seconds_left(
    limits, module_folder
) -> None:
    limits(ceiling=2)
    route_window = 10
    probe = write_probe(module_folder, route_limit=2, route_window=route_window)
    ceiling_ip = new_ip()

    async with client_for(build_app()) as client:
        # The general ceiling: 2 a minute for this IP address.
        first_sent = time.monotonic()
        first = await client.get(probe.open, headers=headers(ceiling_ip))
        first_answered = time.monotonic()
        second = await client.get(probe.open, headers=headers(ceiling_ip))
        await asyncio.sleep(1.3)
        over_sent = time.monotonic()
        over = await client.get(probe.open, headers=headers(ceiling_ip))
        over_answered = time.monotonic()
        ceiling_times = (first_sent, first_answered, over_sent, over_answered)

        # The route's own limit: 2 in 10 seconds for this key. Each request comes from a new
        # IP address, so the third one is over the route's limit only, not the ceiling of 2.
        r_first_sent = time.monotonic()
        r_first = await client.get(probe.limited, headers=headers(new_ip(), probe_key="a"))
        r_first_answered = time.monotonic()
        r_second = await client.get(probe.limited, headers=headers(new_ip(), probe_key="a"))
        await asyncio.sleep(1.3)
        r_over_sent = time.monotonic()
        r_over = await client.get(probe.limited, headers=headers(new_ip(), probe_key="a"))
        r_over_answered = time.monotonic()
        route_times = (r_first_sent, r_first_answered, r_over_sent, r_over_answered)

    assert_passes(first, "ceiling, request 1")
    assert_passes(second, "ceiling, request 2")
    retry_after = assert_limited(over, "ceiling, request 3")
    lowest, highest = seconds_left_bounds(CEILING_WINDOW_SECONDS, *ceiling_times)
    assert lowest <= retry_after <= highest, (
        f"ceiling: Retry-After {retry_after}, the seconds left rounded up are {lowest}..{highest}"
    )

    assert_passes(r_first, "route limit, request 1")
    assert_passes(r_second, "route limit, request 2")
    r_retry_after = assert_limited(r_over, "route limit, request 3")
    r_lowest, r_highest = seconds_left_bounds(route_window, *route_times)
    assert r_lowest <= r_retry_after <= r_highest, (
        f"route limit: Retry-After {r_retry_after}, "
        f"the seconds left rounded up are {r_lowest}..{r_highest}"
    )

    assert probe.calls() == ["open", "open", "limited", "limited"], (
        "a handler ran for a request over the limit"
    )


# -- F6-3 ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F6_3_after_the_window_has_passed_the_next_request_passes(
    limits, module_folder
) -> None:
    limits()
    route_window = 2
    probe = write_probe(module_folder, route_limit=2, route_window=route_window)
    ip = new_ip()

    async with client_for(build_app()) as client:
        window_opened = time.monotonic()
        first = await client.get(probe.limited, headers=headers(ip, probe_key="a"))
        second = await client.get(probe.limited, headers=headers(ip, probe_key="a"))
        over = await client.get(probe.limited, headers=headers(ip, probe_key="a"))
        # The window started at the first request and lasts 2 seconds.
        await asyncio.sleep(max(0.0, window_opened + route_window + 0.5 - time.monotonic()))
        after_window = await client.get(probe.limited, headers=headers(ip, probe_key="a"))

    assert_passes(first, "request 1")
    assert_passes(second, "request 2")
    assert_limited(over, "request 3, inside the window")
    assert_passes(after_window, "the first request after the window")


# -- F6-4 ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F6_4_two_callers_with_different_keys_are_each_counted_on_their_own(
    limits, module_folder
) -> None:
    signing_key = limits(ceiling=2)
    probe = write_probe(module_folder)
    ip_a, ip_b = new_ip(), new_ip()
    forged = new_ip()
    user_1, user_2 = str(uuid.uuid4()), str(uuid.uuid4())
    token_1, token_2 = token_for(signing_key, user_1), token_for(signing_key, user_2)
    ip_1a, ip_1b, ip_shared, ip_2 = new_ip(), new_ip(), new_ip(), new_ip()

    async with client_for(build_app()) as client:
        # Two IP addresses on a public route. The IP address is the last X-Forwarded-For
        # entry: the entries before it change, or are the same for both, and do not count.
        a = [
            await client.get(probe.open, headers=headers(ip_a, forwarded_before=new_ip()))
            for _ in range(3)
        ]
        b = [
            await client.get(probe.open, headers=headers(ip_b, forwarded_before=forged))
            for _ in range(2)
        ]

        # Two users on a signed_in route, each request from a new IP address, so only the
        # user's count can reach the ceiling.
        user_1_first = await client.get(probe.me, headers=headers(ip_1a, token=token_1))
        user_1_second = await client.get(probe.me, headers=headers(ip_1b, token=token_1))
        user_1_over = await client.get(probe.me, headers=headers(ip_shared, token=token_1))
        user_2_same_ip = await client.get(probe.me, headers=headers(ip_shared, token=token_2))
        user_2_other_ip = await client.get(probe.me, headers=headers(ip_2, token=token_2))

    assert_passes(a[0], "IP A, request 1")
    assert_passes(a[1], "IP A, request 2")
    assert_limited(a[2], "IP A, request 3 (earlier X-Forwarded-For entries differ)")
    assert_passes(b[0], "IP B, request 1, after IP A reached the ceiling")
    assert_passes(b[1], "IP B, request 2")

    assert_passes(user_1_first, "user 1, request 1")
    assert_passes(user_1_second, "user 1, request 2, from another IP address")
    assert_limited(user_1_over, "user 1, request 3, from a third IP address")
    assert_passes(user_2_same_ip, "user 2, from the IP address user 1 was stopped on")
    assert_passes(user_2_other_ip, "user 2, request 2")


# -- F6-5 ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F6_5_a_route_with_its_own_limit_and_key_gets_it_as_well_as_the_general_ceiling(
    limits, module_folder
) -> None:
    limits(ceiling=4)
    probe = write_probe(module_folder, route_limit=2, route_window=10)
    route_ip, ceiling_ip = new_ip(), new_ip()

    async with client_for(build_app()) as client:
        # The route's own limit (2 for key "a") stops the third request, while this IP
        # address is still under the ceiling of 4; another key goes on.
        same_key = [
            await client.get(probe.limited, headers=headers(route_ip, probe_key="a"))
            for _ in range(3)
        ]
        other_key = await client.get(probe.limited, headers=headers(route_ip, probe_key="b"))

        # The general ceiling (4 for this IP address) stops the fifth request, although
        # every request has a new key that is under the route's own limit.
        new_keys = [
            await client.get(probe.limited, headers=headers(ceiling_ip, probe_key=f"k{n}"))
            for n in range(5)
        ]

    assert_passes(same_key[0], "key a, request 1")
    assert_passes(same_key[1], "key a, request 2")
    assert_limited(same_key[2], "key a, request 3: over the route's own limit")
    assert_passes(other_key, "key b, request 1, same IP address")

    for n in range(4):
        assert_passes(new_keys[n], f"new key, request {n + 1} of the ceiling of 4")
    assert_limited(new_keys[4], "new key, request 5: over the general ceiling")


# -- Over more than one limit (spec 2.2 and 5.1, version 2.8, no case of its own) ----------


@pytest.mark.asyncio
async def test_F6_extra_over_two_limits_gets_the_longest_wait(limits, module_folder) -> None:
    limits(ceiling=2)
    route_window = 10
    probe = write_probe(module_folder, route_limit=2, route_window=route_window)
    ip = new_ip()

    async with client_for(build_app()) as client:
        # One IP address and one key: the third request is over the ceiling of 2 a minute
        # and over the route's own limit of 2 in 10 seconds; both windows began at the first.
        first_sent = time.monotonic()
        first = await client.get(probe.limited, headers=headers(ip, probe_key="a"))
        first_answered = time.monotonic()
        second = await client.get(probe.limited, headers=headers(ip, probe_key="a"))
        await asyncio.sleep(1.3)
        over_sent = time.monotonic()
        over = await client.get(probe.limited, headers=headers(ip, probe_key="a"))
        over_answered = time.monotonic()
        times = (first_sent, first_answered, over_sent, over_answered)

    assert_passes(first, "request 1")
    assert_passes(second, "request 2")
    retry_after = assert_limited(over, "request 3, over the ceiling and the route's limit")
    lowest, highest = seconds_left_bounds(CEILING_WINDOW_SECONDS, *times)
    r_lowest, r_highest = seconds_left_bounds(route_window, *times)
    assert lowest <= retry_after <= highest, (
        f"Retry-After {retry_after}: the longest wait, the ceiling's seconds left rounded up, "
        f"is {lowest}..{highest} (the route limit's shorter wait is {r_lowest}..{r_highest})"
    )
    assert probe.calls() == ["limited", "limited"], "a handler ran for a request over the limit"


# -- RATE_LIMIT_PER_MINUTE=0 (spec 2.2 and 5.1, no case of its own) ------------------------


@pytest.mark.asyncio
async def test_F6_extra_ceiling_0_turns_the_general_ceiling_off_but_not_a_routes_own_limit(
    limits, module_folder
) -> None:
    limits(ceiling=0)
    probe = write_probe(module_folder, route_limit=2, route_window=10)
    ip = new_ip()
    # One more than the default ceiling of 300, from one IP address.
    over_default = 301

    async with client_for(build_app()) as client:
        open_route = [
            await client.get(probe.open, headers=headers(ip)) for _ in range(over_default)
        ]
        own_limit = [
            await client.get(probe.limited, headers=headers(new_ip(), probe_key="a"))
            for _ in range(3)
        ]

    for n, response in enumerate(open_route, start=1):
        assert_passes(response, f"ceiling 0, request {n} from one IP address")
    assert_passes(own_limit[0], "ceiling 0, route limit 2, request 1")
    assert_passes(own_limit[1], "ceiling 0, route limit 2, request 2")
    assert_limited(own_limit[2], "ceiling 0, route limit 2, request 3: the route's limit applies")
