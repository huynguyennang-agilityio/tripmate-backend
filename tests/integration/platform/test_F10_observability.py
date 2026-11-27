"""F10 Error reporting, traces, and metrics: cases F10-3 to F10-6 (epic-11 spec, version 3.3,
sections 2.6 and 5.1).

The app runs with the real PostgreSQL and Redis of TEST_DATABASE_URL and TEST_REDIS_URL, the
database at the head migration. Sentry, the span exporter, and the metric reader are in-memory
objects passed to `create_app`, or (F10-5) local sockets that accept and never answer; nothing
reaches a real network (tests/INTERFACE.md, section "F10: Observability").
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import itertools
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text

from tests.support import (
    client_for,
    database_engine,
    free_port,
    real_database_url,
    real_redis_url,
    run_alembic,
)

HEALTH_PATH = "/v1/health"
RELEASE = f"f10-release-{uuid.uuid4().hex[:8]}"

# A request that answers within this many seconds has not waited on Sentry or the collector.
ANSWER_WITHIN = 5.0
# How long a test waits for a metric value or a connection to show up.
METRIC_WAIT = 10.0
CONNECTION_WAIT = 15.0
# The app's lifespan ends within this many seconds, even when nothing answers.
SHUTDOWN_WITHIN = 15.0
# During shut-down the event loop never stops for this long.
LOOP_GAP_LIMIT = 1.0

API_SOURCE = """\
from fastapi import APIRouter, Depends

from app.platform.access import public

router = APIRouter()


@router.get("/{segment}/boom", dependencies=[Depends(public)])
async def boom() -> dict[str, str]:
    raise RuntimeError("{message}")
"""


def _output(result: Any) -> str:
    return (
        f"exit code {result.returncode}\n--- stdout\n{result.stdout}\n--- stderr\n{result.stderr}"
    )


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> Iterator[None]:
    """The database of TEST_DATABASE_URL at the head migration (tests/INTERFACE.md, F3)."""
    result = run_alembic(real_database_url(), "upgrade", "head")
    assert result.returncode == 0, _output(result)
    yield


def _set_real_services(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    """The app's settings with the real services and none of the F10 settings unless given."""
    values = {
        "DATABASE_URL": real_database_url(),
        "REDIS_URL": real_redis_url(),
        "RELEASE_VERSION": RELEASE,
        "PORT": str(free_port()),
        "RATE_LIMIT_PER_MINUTE": "0",
    }
    values.update(extra)
    for name in ("SENTRY_DSN", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_HEADERS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _fake_dsn(port: int) -> str:
    return f"http://{uuid.uuid4().hex}@127.0.0.1:{port}/1"


def _error_module(module_folder: Any) -> tuple[str, str]:
    """A module whose `public` route raises a RuntimeError; returns (path, message)."""
    segment = f"f10seg{uuid.uuid4().hex[:8]}"
    message = f"f10 unexpected {uuid.uuid4().hex}"
    module_folder(
        {"__init__.py": "", "api.py": API_SOURCE.format(segment=segment, message=message)}
    )
    return f"/v1/{segment}/boom", message


def _device() -> dict[str, str]:
    return {"X-Device-Id": str(uuid.uuid4())}


def _create_app(**injected: Any) -> Any:
    # Imported here, so each test fails on its own while the code does not exist yet.
    from app.main import create_app

    return create_app(**injected)


def _memory_transport() -> Any:
    """A sentry_sdk Transport that keeps every envelope in memory, in `.envelopes`."""
    from sentry_sdk.transport import Transport

    class MemoryTransport(Transport):
        def __init__(self) -> None:
            super().__init__()
            self.envelopes: list[Any] = []

        def capture_envelope(self, envelope: Any) -> None:
            self.envelopes.append(envelope)

    return MemoryTransport()


def _events(transport: Any) -> list[dict[str, Any]]:
    events = []
    for envelope in transport.envelopes:
        event = envelope.get_event()
        if event is not None:
            events.append(event)
    return events


def _exception_values(event: dict[str, Any]) -> list[str]:
    values = (event.get("exception") or {}).get("values") or []
    return [str(value.get("value", "")) for value in values]


# --- metrics -----------------------------------------------------------------------------


def _metrics_by_name(data: Any) -> dict[str, Any]:
    found: dict[str, Any] = {}
    if data is None:
        return found
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                found[metric.name] = metric
    return found


async def _read_metrics(reader: Any) -> dict[str, Any]:
    return _metrics_by_name(await asyncio.to_thread(reader.get_metrics_data))


def _last_value(metric: Any) -> Any:
    points = list(metric.data.data_points)
    assert points, f"{metric.name} has no data point"
    return points[-1].value


async def _wait_for_value(reader: Any, name: str, expected: Any) -> dict[str, Any]:
    """Read the metrics until `name` reads `expected`, up to METRIC_WAIT seconds."""
    deadline = time.monotonic() + METRIC_WAIT
    metrics: dict[str, Any] = {}
    while True:
        metrics = await _read_metrics(reader)
        if name in metrics and _last_value(metrics[name]) == expected:
            return metrics
        if time.monotonic() > deadline:
            seen = _last_value(metrics[name]) if name in metrics else "missing"
            pytest.fail(f"{name} did not read {expected!r} within {METRIC_WAIT} s: {seen}")
        await asyncio.sleep(0.2)


def _duration_points(metric: Any) -> list[Any]:
    return list(metric.data.data_points)


def _point_for(points: list[Any], route: str, status: int) -> Any:
    for point in points:
        attributes = dict(point.attributes or {})
        if attributes.get("http.route") == route and attributes.get(
            "http.response.status_code"
        ) in (status, str(status)):
            return point
    described = [dict(point.attributes or {}) for point in points]
    pytest.fail(f"no http.server.request.duration point for {route} {status}: {described}")


# --- a collector that does not answer ----------------------------------------------------


class SilentListener:
    """A local TCP socket that accepts connections and never reads or answers."""

    def __init__(self) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(64)
        self.socket.settimeout(0.2)
        self.port = int(self.socket.getsockname()[1])
        self.connections: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self.socket.accept()
            except (TimeoutError, OSError):
                continue
            self.connections.append(connection)  # held open, never read

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        for connection in self.connections:
            connection.close()
        self.socket.close()


@pytest.fixture
def silent_listeners() -> Iterator[tuple[SilentListener, SilentListener]]:
    sentry, collector = SilentListener(), SilentListener()
    try:
        yield sentry, collector
    finally:
        sentry.close()
        collector.close()


# --- the cases ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F10_3_unexpected_error_reaches_error_reporting_with_release_and_request_id(
    monkeypatch, module_folder
) -> None:
    _set_real_services(monkeypatch, SENTRY_DSN=_fake_dsn(free_port()))
    path, message = _error_module(module_folder)
    transport = _memory_transport()
    app = _create_app(sentry_transport=transport)

    async with client_for(app, raise_app_exceptions=False) as client:
        response = await client.get(path, headers=_device())

    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "server.error"
    request_id = response.headers["X-Request-Id"]
    events = [e for e in _events(transport) if any(message in v for v in _exception_values(e))]
    assert events, f"no error event holds the exception; events: {_events(transport)}"
    for event in events:
        assert event.get("release") == RELEASE, event.get("release")
        assert (event.get("tags") or {}).get("request_id") == request_id, event.get("tags")


@pytest.mark.asyncio
async def test_F10_4_request_reading_database_is_one_trace_with_request_and_query_spans(
    monkeypatch,
) -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import SpanKind

    _set_real_services(monkeypatch)
    exporter = InMemorySpanExporter()
    app = _create_app(span_exporter=exporter)

    async with client_for(app) as client:
        response = await client.get(HEALTH_PATH)

    assert response.status_code == 200, response.text
    spans = list(exporter.get_finished_spans())
    described = [(span.name, span.kind, dict(span.attributes or {})) for span in spans]
    server = [span for span in spans if span.kind == SpanKind.SERVER]
    assert len(server) == 1, f"expected one SERVER span: {described}"
    trace_id = server[0].context.trace_id
    queries = [
        span
        for span in spans
        if "postgresql"
        in {(span.attributes or {}).get("db.system"), (span.attributes or {}).get("db.system.name")}
    ]
    assert queries, f"no PostgreSQL query span: {described}"
    assert all(span.context.trace_id == trace_id for span in spans), (
        f"the spans are not one trace: {described}"
    )


@pytest.mark.asyncio
async def test_F10_5_request_succeeds_when_error_reporting_and_collector_do_not_answer(
    monkeypatch, module_folder, silent_listeners
) -> None:
    sentry, collector = silent_listeners
    _set_real_services(
        monkeypatch,
        SENTRY_DSN=_fake_dsn(sentry.port),
        OTEL_EXPORTER_OTLP_ENDPOINT=f"http://127.0.0.1:{collector.port}",
        OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic%20bWFkZS11cDptYWRlLXVw",
    )
    path, _ = _error_module(module_folder)
    app = _create_app()

    async with client_for(app, raise_app_exceptions=False) as client:
        started = time.monotonic()
        failed = await asyncio.wait_for(client.get(path, headers=_device()), ANSWER_WITHIN)
        assert time.monotonic() - started < ANSWER_WITHIN
        started = time.monotonic()
        health = await asyncio.wait_for(client.get(HEALTH_PATH), ANSWER_WITHIN)
        assert time.monotonic() - started < ANSWER_WITHIN

    assert failed.status_code == 500, failed.text
    assert failed.json()["error"]["code"] == "server.error"
    assert health.status_code == 200, health.text

    deadline = time.monotonic() + CONNECTION_WAIT
    for _ in range(int(CONNECTION_WAIT / 0.2)):
        if (sentry.connections and collector.connections) or time.monotonic() > deadline:
            break
        await asyncio.sleep(0.2)
    assert sentry.connections, "error reporting was never asked"
    assert collector.connections, "the trace collector was never asked"


@pytest.mark.asyncio
async def test_F10_6_metrics_carry_requests_errors_latency_queue_job_age_and_outbox_waiting(
    monkeypatch, module_folder
) -> None:
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    _set_real_services(monkeypatch)
    path, _ = _error_module(module_folder)
    async with database_engine(real_database_url()) as engine, engine.begin() as connection:
        for published in (False, False, True):
            await connection.execute(
                text(
                    "INSERT INTO outbox_events (trip_id, trip_seq, type, payload, published_at) "
                    "VALUES (:trip_id, 1, 'f10.probe', CAST('{}' AS jsonb), "
                    + ("clock_timestamp()" if published else "NULL")
                    + ")"
                ),
                {"trip_id": uuid.uuid4()},
            )
    reader = InMemoryMetricReader()
    app = _create_app(metric_reader=reader)

    async with client_for(app, raise_app_exceptions=False) as client:
        health = await client.get(HEALTH_PATH)
        failed = await client.get(path, headers=_device())
        assert health.status_code == 200, health.text
        assert failed.status_code == 500, failed.text
        metrics = await _wait_for_value(reader, "tripmate.outbox.waiting", 2)

    for name in ("http.server.request.duration", "tripmate.queue.depth", "tripmate.job.age"):
        assert name in metrics, f"{name} is missing; found {sorted(metrics)}"
    points = _duration_points(metrics["http.server.request.duration"])
    for route, status in ((HEALTH_PATH, 200), (path, 500)):
        point = _point_for(points, route, status)
        assert point.count >= 1, (route, status, point.count)
        assert point.sum > 0, (route, status, point.sum)
    assert _last_value(metrics["tripmate.queue.depth"]) == 0
    assert _last_value(metrics["tripmate.job.age"]) == 0
    assert _last_value(metrics["tripmate.outbox.waiting"]) == 2


# --- data protection in spans and outgoing calls (F10-2) ----------------------------------


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return  # the request line holds the query; it must not reach the captured output


@contextlib.contextmanager
def _local_http_server() -> Iterator[int]:
    """A local HTTP server that answers every GET with 200; yields its port."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()


def _pairs(attributes: Any) -> str:
    return " ".join(f"{key}={value}" for key, value in dict(attributes or {}).items())


def _span_text(span: Any) -> str:
    """All a span holds as text: its name, attributes, status description, events (name and
    attributes, an `exception` event's message and stack trace included), and links' attributes.
    """
    parts = [f"name={span.name}", _pairs(span.attributes)]
    status = getattr(span, "status", None)
    parts.append(f"status={getattr(status, 'description', None) or ''}")
    for event in getattr(span, "events", None) or ():
        parts.append(f"event={event.name} {_pairs(event.attributes)}")
    for link in getattr(span, "links", None) or ():
        parts.append(f"link {_pairs(link.attributes)}")
    return " | ".join(parts)


@pytest.mark.asyncio
async def test_F10_2_query_string_never_reaches_a_span_or_a_log_line(monkeypatch, capfd) -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import SpanKind

    _set_real_services(monkeypatch)
    token = f"QSECRET{uuid.uuid4().hex}"
    email = f"q{uuid.uuid4().hex[:8]}@example.com"
    exporter = InMemorySpanExporter()
    app = _create_app(span_exporter=exporter)
    capfd.readouterr()

    async with client_for(app) as client:
        response = await client.get(HEALTH_PATH, params={"token": token, "email": email})

    captured = capfd.readouterr()
    assert response.status_code == 200, response.text
    spans = list(exporter.get_finished_spans())
    assert [span for span in spans if span.kind == SpanKind.SERVER], "no SERVER span"
    for span in spans:
        held = _span_text(span)
        assert token not in held, f"the token is in span {span.name}: {held}"
        assert email not in held, f"the email is in span {span.name}: {held}"
    for value in (token, email):
        assert value not in captured.out + captured.err, "a query value was logged"


@pytest.mark.asyncio
async def test_F10_2_outgoing_http_call_url_query_never_reaches_a_span_or_a_log_line(
    monkeypatch, capfd, caplog
) -> None:
    import httpx
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import SpanKind

    _set_real_services(monkeypatch)
    secret = f"SECRETKEY{uuid.uuid4().hex}"
    exporter = InMemorySpanExporter()
    app = _create_app(span_exporter=exporter)
    capfd.readouterr()

    with _local_http_server() as port:
        async with client_for(app), httpx.AsyncClient() as raw_client:
            outgoing = app.state.observability.instrument_http_client(raw_client)
            answer = await outgoing.get(f"http://127.0.0.1:{port}/f10-ping?key={secret}")

    captured = capfd.readouterr()
    assert answer.status_code == 200, answer.text
    spans = list(exporter.get_finished_spans())
    clients = [span for span in spans if span.kind == SpanKind.CLIENT]
    described = [(span.name, span.kind, _span_text(span)) for span in spans]
    assert clients, f"the outgoing call made no CLIENT span: {described}"
    for span in spans:
        held = _span_text(span)
        assert secret not in held, f"the URL's query is in span {span.name}: {held}"
    assert secret not in captured.out + captured.err, "the URL's query was logged"
    httpx_lines = [r.getMessage() for r in caplog.records if r.name.startswith("httpx")]
    assert not [line for line in httpx_lines if secret in line], httpx_lines


# --- shut-down with nothing answering (F10-5) --------------------------------------------


@pytest.mark.asyncio
async def test_F10_5_shutdown_is_bounded_and_does_not_block_the_event_loop(
    monkeypatch, module_folder, silent_listeners
) -> None:
    sentry, collector = silent_listeners
    _set_real_services(
        monkeypatch,
        SENTRY_DSN=_fake_dsn(sentry.port),
        OTEL_EXPORTER_OTLP_ENDPOINT=f"http://127.0.0.1:{collector.port}",
        OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic%20bWFkZS11cDptYWRlLXVw",
    )
    path, _ = _error_module(module_folder)
    app = _create_app()
    ticks: list[float] = []

    async def tick() -> None:
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    running = client_for(app, raise_app_exceptions=False)
    client = await running.__aenter__()
    try:
        failed = await asyncio.wait_for(client.get(path, headers=_device()), ANSWER_WITHIN)
        health = await asyncio.wait_for(client.get(HEALTH_PATH), ANSWER_WITHIN)
    except BaseException:
        await running.__aexit__(None, None, None)
        raise
    assert failed.status_code == 500, failed.text
    assert health.status_code == 200, health.text

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0.1)
    started = time.monotonic()
    try:
        await asyncio.wait_for(running.__aexit__(None, None, None), SHUTDOWN_WITHIN + 5)
    finally:
        ended = time.monotonic()
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker

    assert ended - started < SHUTDOWN_WITHIN, f"shut-down took {ended - started:.1f} s"
    during = [started, *[t for t in ticks if started <= t <= ended], ended]
    longest = max(later - earlier for earlier, later in itertools.pairwise(during))
    assert longest < LOOP_GAP_LIMIT, f"the event loop stopped for {longest:.1f} s in shut-down"

    deadline = time.monotonic() + CONNECTION_WAIT
    for _ in range(int(CONNECTION_WAIT / 0.2)):
        if (sentry.connections and collector.connections) or time.monotonic() > deadline:
            break
        await asyncio.sleep(0.2)
    assert sentry.connections, "error reporting was never asked"
    assert collector.connections, "the trace collector was never asked"


# --- data protection in the log line of a database error (F10-2) --------------------------

# A module whose `public` routes fail on the database: `/bound` with a statement whose bound
# values are an email and a token (PostgreSQL quotes the email in its message, SQLAlchemy puts
# both values in the exception text), `/unique` with a unique violation whose DETAIL quotes the
# email (the failing statement has no bound value). Each keeps the name of the exception's class
# in `RAISED` and raises it unchanged. The same values must reach no span either: not its
# attributes, its status description, or its events.
DB_ERROR_API_SOURCE = """\
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.access import public
from app.platform.db import get_session

router = APIRouter()
RAISED: list[str] = []


@router.get("/SEGMENT/bound", dependencies=[Depends(public)])
async def bound(
    session: Annotated[AsyncSession, Depends(get_session)],
    email: Annotated[str, Query()],
    token: Annotated[str, Query()],
) -> dict[str, str]:
    try:
        await session.execute(
            text("SELECT CAST(CAST(:email AS text) AS int), CAST(:token AS text)"),
            {"email": email, "token": token},
        )
    except Exception as error:
        RAISED.append(type(error).__name__)
        raise
    return {"ok": "no"}


@router.get("/SEGMENT/unique", dependencies=[Depends(public)])
async def unique(
    session: Annotated[AsyncSession, Depends(get_session)],
    email: Annotated[str, Query()],
    token: Annotated[str, Query()],
) -> dict[str, str]:
    await session.execute(
        text("CREATE TEMP TABLE TABLE_NAME (email text PRIMARY KEY, token text) ON COMMIT DROP")
    )
    await session.execute(
        text("INSERT INTO TABLE_NAME (email, token) VALUES (:email, :token)"),
        {"email": email, "token": token},
    )
    try:
        await session.execute(text("INSERT INTO TABLE_NAME SELECT email, token FROM TABLE_NAME"))
    except Exception as error:
        RAISED.append(type(error).__name__)
        raise
    return {"ok": "no"}
"""


def _db_error_module(module_folder: Any) -> tuple[str, Any]:
    """The module of DB_ERROR_API_SOURCE; returns (path prefix, its loaded api module getter)."""
    import importlib

    segment = f"f10db{uuid.uuid4().hex[:8]}"
    source = DB_ERROR_API_SOURCE.replace("SEGMENT", segment).replace(
        "TABLE_NAME", f"f10_unique_{uuid.uuid4().hex[:8]}"
    )
    name = module_folder({"__init__.py": "", "api.py": source})
    return f"/v1/{segment}", lambda: importlib.import_module(f"app.{name}.api")


def _json_lines(output: str) -> list[dict[str, Any]]:
    import json

    lines: list[dict[str, Any]] = []
    for raw in output.splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if isinstance(value, dict):
            lines.append(value)
    return lines


async def _assert_database_error_is_logged_without_values(
    monkeypatch: pytest.MonkeyPatch, module_folder: Any, capfd: Any, caplog: Any, route: str
) -> None:
    import json

    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import SpanKind

    _set_real_services(monkeypatch)
    prefix, api = _db_error_module(module_folder)
    email = f"e{uuid.uuid4().hex[:10]}@example.com"
    token = f"DBTOKEN{uuid.uuid4().hex}"
    exporter = InMemorySpanExporter()
    app = _create_app(span_exporter=exporter)
    capfd.readouterr()

    async with client_for(app, raise_app_exceptions=False) as client:
        response = await client.get(
            f"{prefix}/{route}", params={"email": email, "token": token}, headers=_device()
        )

    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "server.error"
    raised = api().RAISED
    assert raised, "the route did not fail on the database"
    request_id = response.headers["X-Request-Id"]

    for value in (email, token):
        assert value not in output, f"a bound or quoted value was logged:\n{output}"
        assert value not in caplog.text, f"a log record holds a value:\n{caplog.text}"
        for record in caplog.records:
            assert value not in record.getMessage(), record.getMessage()
            assert value not in repr(record.args), repr(record.args)
            assert value not in repr(record.__dict__), repr(record.__dict__)

    error_lines = [
        line
        for line in _json_lines(output)
        if line.get("request_id") == request_id and line.get("event") != "request"
    ]
    assert error_lines, f"no JSON error line carries the request ID {request_id}:\n{output}"
    assert any(raised[0] in json.dumps(line) for line in error_lines), (
        f"no error line of the request names the error type {raised[0]}: {error_lines}"
    )

    # The spans are read after the lifespan has ended (shut-down flushes them). The failing
    # query's span may carry the database's message in its status description or in the
    # attributes of its `exception` event; neither may hold the values.
    spans = list(exporter.get_finished_spans())
    described = [(span.name, span.kind, _span_text(span)) for span in spans]
    assert [span for span in spans if span.kind == SpanKind.SERVER], f"no SERVER span: {described}"
    assert [
        span
        for span in spans
        if "postgresql"
        in {(span.attributes or {}).get("db.system"), (span.attributes or {}).get("db.system.name")}
    ], f"no PostgreSQL query span: {described}"
    for span in spans:
        held = _span_text(span)
        for name, value in (("email", email), ("token", token)):
            assert value not in held, f"the {name} is in span {span.name}: {held}"


@pytest.mark.asyncio
async def test_F10_2_database_error_reaches_no_log_line_or_span_with_the_bound_email_or_token(
    monkeypatch, module_folder, capfd, caplog
) -> None:
    await _assert_database_error_is_logged_without_values(
        monkeypatch, module_folder, capfd, caplog, "bound"
    )


@pytest.mark.asyncio
async def test_F10_2_database_error_reaches_no_log_line_or_span_with_a_value_the_database_quotes(
    monkeypatch, module_folder, capfd, caplog
) -> None:
    await _assert_database_error_is_logged_without_values(
        monkeypatch, module_folder, capfd, caplog, "unique"
    )


# --- the route template on spans and metrics (F10-2, F10-6) --------------------------------

ITEM_API_SOURCE = """\
from fastapi import APIRouter, Depends

from app.platform.access import public

router = APIRouter()


@router.get("/SEGMENT/items/{item_id}", dependencies=[Depends(public)])
async def item(item_id: str) -> dict[str, str]:
    return {"seen": "yes"}
"""


@pytest.mark.asyncio
async def test_F10_2_route_with_a_path_parameter_is_named_by_its_template(
    monkeypatch, module_folder
) -> None:
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import SpanKind

    _set_real_services(monkeypatch)
    segment = f"f10item{uuid.uuid4().hex[:8]}"
    module_folder({"__init__.py": "", "api.py": ITEM_API_SOURCE.replace("SEGMENT", segment)})
    template = f"/v1/{segment}/items/{{item_id}}"
    item_id = f"ITEM{uuid.uuid4().hex}"
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    app = _create_app(span_exporter=exporter, metric_reader=reader)

    async with client_for(app) as client:
        response = await client.get(f"/v1/{segment}/items/{item_id}", headers=_device())
        assert response.status_code == 200, response.text
        deadline = time.monotonic() + METRIC_WAIT
        while True:
            metrics = await _read_metrics(reader)
            if "http.server.request.duration" in metrics or time.monotonic() > deadline:
                break
            await asyncio.sleep(0.2)

    assert "http.server.request.duration" in metrics, f"found {sorted(metrics)}"
    points = _duration_points(metrics["http.server.request.duration"])
    point = _point_for(points, template, 200)
    assert point.count >= 1, point.count
    for each in points:
        route = str(dict(each.attributes or {}).get("http.route", ""))
        assert item_id not in route, f"the path value is in the metric's http.route: {route}"

    spans = list(exporter.get_finished_spans())
    described = [(span.name, span.kind, dict(span.attributes or {})) for span in spans]
    server = [span for span in spans if span.kind == SpanKind.SERVER]
    assert len(server) == 1, f"expected one SERVER span: {described}"
    route = (server[0].attributes or {}).get("http.route")
    assert route == template, f"the SERVER span's http.route is {route!r}: {described}"
    for span in spans:
        value = str((span.attributes or {}).get("http.route", ""))
        assert item_id not in value, f"the path value is in http.route of {span.name}: {value}"


# --- an error after the response has started (F10-2) --------------------------------------

# A module whose `public` routes fail after the response has started: `/stream` answers with a
# StreamingResponse whose generator raises after its first chunk, `/background` answers 200 and
# adds a background task that raises. Both raise a ValueError whose message holds the module's
# random EMAIL and TOKEN, values the request never carries. Each notes in `REACHED` that it got
# to the raise.
AFTER_START_API_SOURCE = """\
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse

from app.platform.access import public

router = APIRouter()
EMAIL = "EMAIL_VALUE"
TOKEN = "TOKEN_VALUE"
REACHED: list[str] = []


async def _chunks() -> AsyncIterator[bytes]:
    yield b"first chunk\\n"
    REACHED.append("stream")
    raise ValueError(f"bad user {EMAIL} {TOKEN}")


async def _task() -> None:
    REACHED.append("background")
    raise ValueError(f"bad user {EMAIL} {TOKEN}")


@router.get("/SEGMENT/stream", dependencies=[Depends(public)])
async def stream() -> StreamingResponse:
    return StreamingResponse(_chunks(), media_type="text/plain")


@router.get("/SEGMENT/background", dependencies=[Depends(public)])
async def background(tasks: BackgroundTasks) -> dict[str, str]:
    tasks.add_task(_task)
    return {"ok": "yes"}
"""

AFTER_START_ROUTES = ["stream", "background"]


def _after_start_module(module_folder: Any) -> tuple[str, str, str, Any]:
    """The module of AFTER_START_API_SOURCE; returns (path prefix, email, token, api getter)."""
    import importlib

    segment = f"f10late{uuid.uuid4().hex[:8]}"
    email = f"late{uuid.uuid4().hex[:10]}@example.com"
    token = f"LATETOKEN{uuid.uuid4().hex}"
    source = (
        AFTER_START_API_SOURCE.replace("SEGMENT", segment)
        .replace("EMAIL_VALUE", email)
        .replace("TOKEN_VALUE", token)
    )
    name = module_folder({"__init__.py": "", "api.py": source})
    return f"/v1/{segment}", email, token, lambda: importlib.import_module(f"app.{name}.api")


async def _call_asgi(
    app: Any, path: str, headers: dict[str, str]
) -> tuple[list[dict[str, Any]], BaseException | None]:
    """Call the ASGI app itself, as a server would (uvicorn), for one GET of `path`.

    Returns the messages the app sent and the exception that escaped the app, if any. A server
    logs an escaped exception on its own (uvicorn: "Exception in ASGI application", with the
    traceback and the exception's message, and no request ID), so the app must not let one out.
    """
    sent: list[dict[str, Any]] = []
    disconnected = asyncio.Event()
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")]
        + [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    escaped: BaseException | None = None
    try:
        await asyncio.wait_for(app(scope, receive, send), ANSWER_WITHIN)
    except Exception as error:
        escaped = error
    finally:
        disconnected.set()
    return sent, escaped


def _response_start(sent: list[dict[str, Any]]) -> dict[str, Any]:
    starts = [message for message in sent if message.get("type") == "http.response.start"]
    assert starts, f"the app never started the response: {[m.get('type') for m in sent]}"
    return starts[0]


def _header(start: dict[str, Any], name: str) -> str | None:
    for key, value in start.get("headers") or ():
        if bytes(key).lower() == name.lower().encode():
            return bytes(value).decode()
    return None


def _record_text(record: Any) -> str:
    """All a log record holds as text: message, arguments, fields, and a formatted traceback."""
    import logging
    import traceback

    parts = [record.getMessage(), repr(record.args), repr(record.__dict__)]
    if record.exc_info and record.exc_info[1] is not None:
        parts.append("".join(traceback.format_exception(*record.exc_info)))
        parts.append(logging.Formatter().formatException(record.exc_info))
    if record.exc_text:
        parts.append(str(record.exc_text))
    return "\n".join(parts)


async def _run_after_start(
    monkeypatch: pytest.MonkeyPatch, module_folder: Any, route: str, **injected: Any
) -> tuple[str, str, Any, list[dict[str, Any]], BaseException | None]:
    """Serve one request of `route` with the ASGI app called directly, inside its lifespan."""
    _set_real_services(monkeypatch)
    prefix, email, token, api = _after_start_module(module_folder)
    app = _create_app(**injected)
    async with app.router.lifespan_context(app):
        sent, escaped = await _call_asgi(app, f"{prefix}/{route}", _device())
        # A background task runs before the app's call returns; give the event loop a moment
        # anyway, so a line written just after is captured too.
        await asyncio.sleep(0.2)
    assert route in api().REACHED, f"the {route} route never reached its raise"
    return email, token, api, sent, escaped


@pytest.mark.asyncio
@pytest.mark.parametrize("route", AFTER_START_ROUTES)
async def test_F10_2_error_after_the_response_started_is_logged_with_request_id_and_no_value(
    monkeypatch, module_folder, capfd, caplog, route
) -> None:
    import json

    capfd.readouterr()
    email, token, _, sent, escaped = await _run_after_start(monkeypatch, module_folder, route)
    captured = capfd.readouterr()
    output = captured.out + captured.err

    start = _response_start(sent)
    assert start.get("status") == 200, start.get("status")
    if route == "stream":
        chunks = b"".join(
            bytes(m.get("body", b"")) for m in sent if m.get("type") == "http.response.body"
        )
        assert chunks.startswith(b"first chunk"), chunks
    assert escaped is None, (
        f"{type(escaped).__name__} escaped the ASGI app after the response started; a server "
        "would log it with its message and no request ID"
    )
    request_id = _header(start, "X-Request-Id")
    assert request_id, f"no X-Request-Id on the response: {start.get('headers')}"

    for name, value in (("email", email), ("token", token)):
        assert value not in output, f"the {name} was logged:\n{output}"
        assert value not in caplog.text, f"a log record holds the {name}:\n{caplog.text}"
        for record in caplog.records:
            held = _record_text(record)
            assert value not in held, f"a log record of {record.name} holds the {name}: {held}"

    error_lines = [
        line
        for line in _json_lines(output)
        if line.get("request_id") == request_id and line.get("event") != "request"
    ]
    assert error_lines, f"no JSON error line carries the request ID {request_id}:\n{output}"
    assert any("ValueError" in json.dumps(line) for line in error_lines), (
        f"no error line of the request names the error type ValueError: {error_lines}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", AFTER_START_ROUTES)
async def test_F10_2_error_after_the_response_started_leaves_no_value_in_a_span(
    monkeypatch, module_folder, route
) -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import SpanKind

    exporter = InMemorySpanExporter()
    email, token, _, sent, _ = await _run_after_start(
        monkeypatch, module_folder, route, span_exporter=exporter
    )
    _response_start(sent)

    spans = list(exporter.get_finished_spans())
    described = [(span.name, span.kind, _span_text(span)) for span in spans]
    assert [span for span in spans if span.kind == SpanKind.SERVER], f"no SERVER span: {described}"
    recorded = [
        event
        for span in spans
        for event in getattr(span, "events", None) or ()
        if event.name == "exception"
    ]
    assert recorded, f"no span recorded the error as an `exception` event: {described}"
    for span in spans:
        held = _span_text(span)
        for name, value in (("email", email), ("token", token)):
            assert value not in held, f"the {name} is in span {span.name}: {held}"


# --- OpenTelemetry's header capture setting (F10-2) ---------------------------------------


@pytest.mark.asyncio
async def test_F10_2_header_capture_setting_puts_no_authorization_token_in_a_span(
    monkeypatch, capfd
) -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import SpanKind

    _set_real_services(monkeypatch)
    monkeypatch.setenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST", "Authorization")
    monkeypatch.delenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SANITIZE_FIELDS", raising=False)
    token = f"HDRTOKEN{uuid.uuid4().hex}"
    exporter = InMemorySpanExporter()
    app = _create_app(span_exporter=exporter)
    capfd.readouterr()

    async with client_for(app) as client:
        response = await client.get(HEALTH_PATH, headers={"Authorization": f"Bearer {token}"})

    captured = capfd.readouterr()
    assert response.status_code == 200, response.text
    spans = list(exporter.get_finished_spans())
    described = [(span.name, span.kind, _span_text(span)) for span in spans]
    assert [span for span in spans if span.kind == SpanKind.SERVER], f"no SERVER span: {described}"
    for span in spans:
        held = _span_text(span)
        assert token not in held, f"the Authorization token is in span {span.name}: {held}"
    assert token not in captured.out + captured.err, "the Authorization token was logged"
