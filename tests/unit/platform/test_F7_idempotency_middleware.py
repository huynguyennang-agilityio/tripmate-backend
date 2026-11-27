"""F7 round 1 (code agent's own tests): how the idempotency middleware ends a claim, with fakes for
PostgreSQL (R1-3, R1-5, R1-6, R1-9), the order of `idempotent` among a route's dependencies
(R1-4), and the documented `409` (R1-10).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Depends, FastAPI

from app.platform.access import public
from app.platform.db import SessionHandoff
from app.platform.errors import install_openapi
from app.platform.idempotency import (
    _CLAIM_KEY,
    IDEMPOTENCY_409_TEXT,
    MAX_STORED_RESPONSE_BYTES,
    Claim,
    IdempotencyMiddleware,
    check_idempotent_order,
    idempotent,
    install_idempotency_contract,
    misplaced_idempotent,
)
from app.platform.rate_limits import rate_limit


class FakeSession:
    """A session that records its statements; `stored` is what the re-read of a row finds."""

    def __init__(
        self,
        log: list[str],
        name: str,
        *,
        rowcount: int = 1,
        stored: bool = False,
        commit_error: Exception | None = None,
    ) -> None:
        self.log, self.name = log, name
        self.rowcount, self.stored, self.commit_error = rowcount, stored, commit_error
        self.info: dict[str, Any] = {}

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> FakeSession:
        return self

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        kind = "store" if sql.startswith("UPDATE") else sql.split()[0].lower()
        if sql.startswith("SELECT"):
            kind = "reread"
        self.log.append(f"{self.name}:{kind}")
        return SimpleNamespace(rowcount=self.rowcount, first=lambda: (1,) if self.stored else None)

    async def commit(self) -> None:
        self.log.append(f"{self.name}:commit")
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.log.append(f"{self.name}:rollback")

    async def close(self) -> None:
        self.log.append(f"{self.name}:close")


async def _run(
    app: Any,
    *,
    handler_session: FakeSession | None,
    other: FakeSession,
) -> tuple[list[dict[str, Any]], list[str]]:
    handoff = SessionHandoff()
    handoff.session = handler_session  # type: ignore[assignment]
    claim = Claim(1, "created", handoff)
    scope = {
        "type": "http",
        "method": "POST",
        "state": {_CLAIM_KEY: claim, "request_id": "R"},
        "app": SimpleNamespace(state=SimpleNamespace(session_factory=lambda: other)),
    }
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    await IdempotencyMiddleware(app)(scope, receive, send)  # type: ignore[arg-type]
    return sent, other.log


def _start(status: int) -> dict[str, Any]:
    return {"type": "http.response.start", "status": status, "headers": []}


def _body(body: bytes, more: bool = False) -> dict[str, Any]:
    return {"type": "http.response.body", "body": body, "more_body": more}


async def test_F7_R1_3_app_that_returns_without_the_final_body_releases_the_claim() -> None:
    log: list[str] = []
    handler = FakeSession(log, "handler")

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send(_start(201))
        await send(_body(b"part", more=True))  # a client that disconnected: no final message

    sent, _ = await _run(app, handler_session=handler, other=FakeSession(log, "other"))

    assert sent == [], "a held response was sent"
    assert "handler:rollback" in log and "handler:close" in log, log
    assert "other:delete" in log, log


async def test_F7_R1_9_message_without_a_body_passes_through_after_the_start_and_commits() -> None:
    log: list[str] = []
    handler = FakeSession(log, "handler")
    pathsend = {"type": "http.response.pathsend", "path": "/x"}

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send(_start(200))
        await send(pathsend)

    sent, _ = await _run(app, handler_session=handler, other=FakeSession(log, "other"))

    assert sent == [_start(200), pathsend]
    assert "handler:commit" in log and "other:delete" in log, log
    assert "other:store" not in log and "handler:store" not in log, log


async def test_F7_R1_9_response_over_the_limit_is_passed_through_unstored_and_committed() -> None:
    log: list[str] = []
    handler = FakeSession(log, "handler")
    big = b"x" * (MAX_STORED_RESPONSE_BYTES + 1)

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send(_start(201))
        await send(_body(big, more=True))
        await send(_body(b"tail"))

    sent, _ = await _run(app, handler_session=handler, other=FakeSession(log, "other"))

    assert [m["type"] for m in sent] == ["http.response.start"] + ["http.response.body"] * 2
    assert sent[1]["body"] == big and sent[2]["body"] == b"tail"
    assert "handler:commit" in log and "other:delete" in log, log
    assert "handler:store" not in log, log


async def test_F7_R1_9_response_at_the_limit_is_stored() -> None:
    log: list[str] = []
    handler = FakeSession(log, "handler")

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send(_start(201))
        await send(_body(b"x" * MAX_STORED_RESPONSE_BYTES))

    sent, _ = await _run(app, handler_session=handler, other=FakeSession(log, "other"))

    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert log == ["handler:store", "handler:commit", "handler:close"], log


async def test_F7_R1_6_taken_over_claim_of_a_handler_with_no_session_answers_409() -> None:
    for status in (201, 422):
        log: list[str] = []

        async def app(scope: Any, receive: Any, send: Any, status: int = status) -> None:
            await send(_start(status))
            await send(_body(b"{}"))

        sent, _ = await _run(app, handler_session=None, other=FakeSession(log, "other", rowcount=0))

        assert sent[0]["status"] == 409, (status, sent)
        assert b"request.idempotency_in_progress" in sent[1]["body"]


async def test_F7_R1_6_taken_over_claim_of_a_handler_with_a_session_rolls_back_and_answers_409() -> (
    None
):
    log: list[str] = []
    handler = FakeSession(log, "handler", rowcount=0)

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send(_start(201))
        await send(_body(b"{}"))

    sent, _ = await _run(app, handler_session=handler, other=FakeSession(log, "other"))

    assert sent[0]["status"] == 409
    assert "handler:rollback" in log and "handler:commit" not in log, log


async def test_F7_R1_5_commit_error_keeps_the_row_when_the_response_was_stored() -> None:
    log: list[str] = []
    handler = FakeSession(log, "handler", commit_error=ConnectionError("lost"))

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send(_start(201))
        await send(_body(b"{}"))

    sent, _ = await _run(app, handler_session=handler, other=FakeSession(log, "other", stored=True))

    assert [m["status"] for m in sent if "status" in m] == [201], sent
    assert "other:reread" in log and "other:delete" not in log, log


async def test_F7_R1_5_commit_error_deletes_the_row_when_nothing_was_stored() -> None:
    log: list[str] = []
    handler = FakeSession(log, "handler", commit_error=ConnectionError("lost"))

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send(_start(201))
        await send(_body(b"{}"))

    sent, _ = await _run(
        app, handler_session=handler, other=FakeSession(log, "other", stored=False)
    )

    assert [m["status"] for m in sent if "status" in m] == [500], sent
    assert "other:reread" in log and "other:delete" in log, log


# -- R1-4: the order of the declaration ------------------------------------------------------


async def _guard() -> None:
    """A dependency that may refuse (a membership guard)."""


def _app_with(*dependencies: Any) -> FastAPI:
    app = FastAPI()
    app.post("/x", dependencies=[Depends(d) for d in dependencies])(lambda: None)
    return app


def test_F7_R1_4_a_guard_after_idempotent_is_named_and_fails_the_start() -> None:
    app = _app_with(public, idempotent, _guard)

    assert misplaced_idempotent(app) == ["POST /x"]
    with pytest.raises(RuntimeError, match="POST /x"):
        check_idempotent_order(app)


def test_F7_R1_4_guards_before_idempotent_and_own_rate_limits_after_it_are_allowed() -> None:
    limit = rate_limit(limit=1, window_seconds=1, key=lambda request: "k")
    for app in (_app_with(public, _guard, idempotent), _app_with(public, idempotent, limit)):
        assert misplaced_idempotent(app) == []
        check_idempotent_order(app)


def test_F7_R1_4_a_route_that_does_not_declare_idempotent_is_not_checked() -> None:
    assert misplaced_idempotent(_app_with(public, _guard)) == []


# -- R1-10: the documented 409 ---------------------------------------------------------------


def test_F7_R1_10_a_409_the_route_documents_is_kept_and_the_idempotency_text_added() -> None:
    app = FastAPI()
    install_openapi(app)
    install_idempotency_contract(app)
    example = {"error": {"code": "trip.exists"}}
    app.post(
        "/x",
        dependencies=[Depends(idempotent)],
        responses={
            409: {
                "description": "The trip exists",
                "content": {"application/json": {"example": example}},
            }
        },
    )(lambda: None)

    response = app.openapi()["paths"]["/x"]["post"]["responses"]["409"]

    assert response["description"].startswith("The trip exists")
    assert IDEMPOTENCY_409_TEXT in response["description"]
    assert response["content"]["application/json"]["example"] == example
    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
