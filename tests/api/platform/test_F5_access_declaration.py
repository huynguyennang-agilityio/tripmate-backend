"""F5 The access declaration test (epic-11 spec, section 2.2, row "Access declaration").

Every route the app serves declares `public`, `signed_in`, or a capability; a route with none
fails this test, which names it. Case F5-1 (tests/api/platform/test_F5_access.py) runs this
test with a temporary module that has an undeclared route. The interface is in
tests/INTERFACE.md, section "F5: Access".
"""

from __future__ import annotations

from tests.support import build_app


def test_F5_every_route_declares_its_access(set_settings) -> None:
    set_settings()
    from app.platform.access import undeclared_routes

    undeclared = undeclared_routes(build_app())

    assert undeclared == [], f"routes with no access declaration: {undeclared}"
