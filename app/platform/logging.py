"""Logs, traces, metrics, and error reporting (EPIC-11 spec, section 2.6).

- Logs: every request writes one JSON line (`event` is `request`) on standard output, once its
  response has been sent, with the request ID, the signed-in user ID, the trip ID, the method,
  the path without its query string, the status, and the time. It holds no email, name, token,
  header, or body: the line is built from those fields only, never from the request.
- Traces and metrics: OpenTelemetry for the API, SQLAlchemy, Redis, and outgoing HTTP clients,
  pushed to the collector of `OTEL_EXPORTER_OTLP_ENDPOINT` (no metrics route).
- Errors: an unexpected exception goes to Sentry with the release tag and a `request_id` tag.

Each `create_app` call makes its own providers and Sentry client and hands them to the app, so
several apps in one process (the tests) never share a signal and OpenTelemetry's global
providers are never set. Nothing here waits for Sentry or the collector while a request runs:
spans, metrics, and events are sent from background threads, and `Observability.shutdown`
(at the end of the app's lifespan) flushes them with a short timeout, off the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import sentry_sdk
import structlog
from fastapi import FastAPI, Request
from opentelemetry import metrics as otel_metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from redis.asyncio import Redis
from sentry_sdk.transport import Transport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.platform.access import checked_caller
from app.platform.config import Settings, parse_otlp_headers
from app.platform.errors import AppError

if TYPE_CHECKING:
    from sentry_sdk._types import DataCollectionUserOptions

SERVICE_NAME = "tripmate-backend"

# Seconds a collector or Sentry gets for one send. A request never waits for either; this
# bounds the background send and the flush when the app stops.
EXPORT_TIMEOUT_SECONDS = 3.0
SENTRY_SHUTDOWN_TIMEOUT_SECONDS = 2.0
# The whole shut-down flush (spans, metrics, Sentry) ends within this many seconds.
SHUTDOWN_TIMEOUT_SECONDS = 10.0
# The same push interval as Grafana Cloud's default scrape.
METRIC_EXPORT_INTERVAL_MILLIS = 60_000
# Seconds one reading of the outbox backlog may take.
OUTBOX_QUERY_TIMEOUT_SECONDS = 2.0

REQUEST_EVENT = "request"

# What Sentry may collect: no user, cookie, body, query string, or local variable.
_NO_REQUEST_DATA: DataCollectionUserOptions = {
    "user_info": False,
    "cookies": {"mode": "off"},
    "http_bodies": [],
    "url_query_params": {"mode": "off"},
    "stack_frame_variables": False,
}

# Span attributes that hold a URL or a path with its query string (old and new semantic
# conventions); the query part is cut off before a span leaves the process.
_URL_ATTRIBUTES = ("http.url", "url.full", "http.target", "url.path", "http.path")
_QUERY_ATTRIBUTES = ("url.query", "http.query")
# Libraries whose own INFO line logs a full request URL, query string included.
_QUIET_LOGGERS = ("httpx", "httpcore")

_HANDLER_MARK = "_tripmate_stdout_json"
_configure_lock = threading.Lock()


# -- Log lines ------------------------------------------------------------------------------


class _StdoutLogger:
    """A structlog logger that writes to whatever `sys.stdout` is when it writes."""

    def msg(self, message: str) -> None:
        sys.stdout.write(message + "\n")
        sys.stdout.flush()

    log = debug = info = warning = warn = error = critical = fatal = failure = exception = msg


class _StdoutLoggerFactory:
    def __call__(self, *args: Any) -> _StdoutLogger:
        return _StdoutLogger()


class _StdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """A standard-library handler on the current `sys.stdout`."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self) -> Any:
        return sys.stdout

    @stream.setter
    def stream(self, value: Any) -> None:  # the base class assigns its stream once
        return


def _shared_processors() -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]


def configure_logging() -> None:
    """JSON lines on standard output for structlog and for the standard library's loggers.

    Safe to call again: it replaces its own handler and never adds a second one.
    """
    with _configure_lock:
        structlog.configure(
            processors=[
                *_shared_processors(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            logger_factory=_StdoutLoggerFactory(),
            cache_logger_on_first_use=False,
        )
        handler = _StdoutHandler()
        setattr(handler, _HANDLER_MARK, True)
        handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                foreign_pre_chain=_shared_processors(),
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.format_exc_info,
                    structlog.processors.JSONRenderer(),
                ],
            )
        )
        root = logging.getLogger()
        for existing in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
            root.removeHandler(existing)
        root.addHandler(handler)
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)
        for name in _QUIET_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)


# -- Span scrubbing ---------------------------------------------------------------------------


def _without_query(value: Any) -> Any:
    if not isinstance(value, str) or ("?" not in value and "#" not in value):
        return value
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _scrub_attributes(attributes: Any) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in dict(attributes or {}).items():
        if key in _QUERY_ATTRIBUTES:
            continue
        clean[key] = _without_query(value) if key in _URL_ATTRIBUTES else value
    return clean


class _ScrubbingExporter(SpanExporter):
    """Hands spans to `inner` with no query string in any URL attribute.

    It works on the finished span, so it covers an attribute set when the span starts and one
    set when it ends, whichever instrumentation sets it.
    """

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._inner.export([self._scrubbed(span) for span in spans])

    @staticmethod
    def _scrubbed(span: ReadableSpan) -> ReadableSpan:
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=_scrub_attributes(span.attributes),
            events=span.events,
            links=span.links,
            kind=span.kind,
            instrumentation_scope=span.instrumentation_scope,
            status=span.status,
            start_time=span.start_time,
            end_time=span.end_time,
        )

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


# -- Telemetry and error reporting ----------------------------------------------------------


class Observability:
    """The providers and the Sentry client of one app."""

    def __init__(
        self,
        settings: Settings,
        *,
        sentry_transport: Transport | None = None,
        span_exporter: SpanExporter | None = None,
        metric_reader: MetricReader | None = None,
    ) -> None:
        self.release = settings.release_version
        # What the gauges read. F9 feeds the queue values through `set_queue_state`.
        self._queue_depth = 0
        self._job_age_seconds = 0.0
        self._engine: AsyncEngine | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        endpoint = settings.otel_exporter_otlp_endpoint
        headers: dict[str, str] = {}
        if endpoint is not None and settings.otel_exporter_otlp_headers is not None:
            headers = parse_otlp_headers(settings.otel_exporter_otlp_headers.get_secret_value())
        base = endpoint.rstrip("/") if endpoint is not None else None
        resource = Resource.create(
            {"service.name": SERVICE_NAME, "service.version": settings.release_version}
        )

        self.tracer_provider: TracerProvider | None = None
        if span_exporter is not None:
            self.tracer_provider = TracerProvider(resource=resource)
            self.tracer_provider.add_span_processor(
                SimpleSpanProcessor(_ScrubbingExporter(span_exporter))
            )
        elif base is not None:
            self.tracer_provider = TracerProvider(resource=resource)
            self.tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    _ScrubbingExporter(
                        OTLPSpanExporter(
                            endpoint=f"{base}/v1/traces",
                            headers=headers,
                            timeout=int(EXPORT_TIMEOUT_SECONDS),
                        )
                    ),
                    export_timeout_millis=int(EXPORT_TIMEOUT_SECONDS * 1000),
                )
            )

        reader: MetricReader | None = metric_reader
        if reader is None and base is not None:
            reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=f"{base}/v1/metrics",
                    headers=headers,
                    timeout=int(EXPORT_TIMEOUT_SECONDS),
                ),
                export_interval_millis=METRIC_EXPORT_INTERVAL_MILLIS,
                export_timeout_millis=int(EXPORT_TIMEOUT_SECONDS * 1000),
            )
        self.meter_provider: MeterProvider | None = None
        self._request_duration: otel_metrics.Histogram | None = None
        if reader is not None:
            self.meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
            self._create_instruments(self.meter_provider)

        self._sentry: sentry_sdk.Client | None = None
        if settings.sentry_dsn is not None:
            self._sentry = sentry_sdk.Client(
                dsn=settings.sentry_dsn.get_secret_value(),
                release=settings.release_version,
                transport=sentry_transport,
                # Events are captured by hand, with no request data and no local variables:
                # both can hold an email, a name, or a token.
                default_integrations=False,
                integrations=[],
                data_collection=_NO_REQUEST_DATA,
                shutdown_timeout=SENTRY_SHUTDOWN_TIMEOUT_SECONDS,
            )

    # -- metrics --

    def _create_instruments(self, provider: MeterProvider) -> None:
        meter = provider.get_meter(SERVICE_NAME, self.release)
        self._request_duration = meter.create_histogram(
            "http.server.request.duration",
            unit="s",
            description="Time to answer an HTTP request",
        )
        meter.create_observable_gauge(
            "tripmate.queue.depth",
            callbacks=[self._observe_queue_depth],
            unit="{job}",
            description="Jobs waiting in the queue",
        )
        meter.create_observable_gauge(
            "tripmate.job.age",
            callbacks=[self._observe_job_age],
            unit="s",
            description="Age of the oldest waiting job",
        )
        meter.create_observable_gauge(
            "tripmate.outbox.waiting",
            callbacks=[self._observe_outbox_waiting],
            unit="{event}",
            description="Outbox rows not published yet",
        )

    def set_queue_state(self, depth: int, oldest_job_age_seconds: float) -> None:
        """For the queue (card F9): the jobs waiting and the age of the oldest."""
        self._queue_depth = depth
        self._job_age_seconds = oldest_job_age_seconds

    def _observe_queue_depth(self, options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(self._queue_depth)]

    def _observe_job_age(self, options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(self._job_age_seconds)]

    def _observe_outbox_waiting(self, options: CallbackOptions) -> Iterable[Observation]:
        """Rows of `outbox_events` not published yet; read on the app's loop, not here."""
        loop, engine = self._loop, self._engine
        if loop is None or engine is None or loop.is_closed():
            return []
        try:
            if asyncio.get_running_loop() is loop:  # waiting here would block the loop
                return []
        except RuntimeError:
            pass
        try:
            future = asyncio.run_coroutine_threadsafe(self._count_outbox_waiting(engine), loop)
            return [Observation(future.result(timeout=OUTBOX_QUERY_TIMEOUT_SECONDS + 1))]
        except Exception:
            # PostgreSQL does not answer: no reading this time, and no failure.
            return []

    @staticmethod
    async def _count_outbox_waiting(engine: AsyncEngine) -> int:
        # The reading is not a request: it gets no span of its own.
        with suppress_instrumentation():
            async with asyncio.timeout(OUTBOX_QUERY_TIMEOUT_SECONDS), engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT count(*) FROM outbox_events WHERE published_at IS NULL")
                )
                return int(result.scalar_one())

    def record_request(
        self, *, method: str, route: str | None, status: int, duration_seconds: float
    ) -> None:
        if self._request_duration is None:
            return
        attributes: dict[str, str | int] = {
            "http.request.method": method,
            "http.response.status_code": status,
        }
        if route is not None:
            attributes["http.route"] = route
        self._request_duration.record(duration_seconds, attributes)

    # -- traces --

    def instrument_app(self, app: FastAPI) -> None:
        """Trace the app's requests. Call before the request ID middleware is added."""
        if self.tracer_provider is not None:
            FastAPIInstrumentor.instrument_app(
                app, tracer_provider=self.tracer_provider, exclude_spans=["receive", "send"]
            )

    def start(self, engine: AsyncEngine, redis: Redis) -> None:
        """At the app's start-up, with the engine and the Redis client it made."""
        self._engine = engine
        self._loop = asyncio.get_running_loop()
        if self.tracer_provider is not None:
            tracer = self.tracer_provider.get_tracer(SERVICE_NAME, self.release)
            # Listeners on this engine only (the instrumentor's own entry point patches
            # SQLAlchemy for the whole process).
            EngineTracer(  # type: ignore[no-untyped-call]
                tracer, engine.sync_engine, _connection_usage(self.meter_provider)
            )
            RedisInstrumentor.instrument_client(redis, tracer_provider=self.tracer_provider)

    def instrument_http_client(self, client: httpx.AsyncClient) -> httpx.AsyncClient:
        """Trace the outgoing calls of an `httpx` client the backend makes; returns it."""
        if self.tracer_provider is not None:
            HTTPXClientInstrumentor.instrument_client(client, tracer_provider=self.tracer_provider)
        return client

    # -- errors --

    def capture_exception(self, error: BaseException, request_id: str | None) -> None:
        """Send an unexpected exception to Sentry, tagged with the request ID."""
        if self._sentry is None:
            return
        try:
            with sentry_sdk.new_scope() as scope:
                scope.set_client(self._sentry)
                if request_id is not None:
                    scope.set_tag("request_id", request_id)
                scope.capture_exception(error)
        except Exception:
            logging.getLogger(__name__).warning("Error reporting failed")

    # -- shutdown --

    async def shutdown(self) -> None:
        """Flush spans, metrics, and events, without blocking the event loop.

        The three steps run at once, each in its own daemon thread, and the wait for all of
        them ends after `SHUTDOWN_TIMEOUT_SECONDS`: a collector or Sentry that never answers
        costs that long at most, and never keeps the process from exiting.
        """
        self._engine = None
        loop = asyncio.get_running_loop()
        done = [loop.create_future() for _ in self._shutdown_steps()]
        for step, finished in zip(self._shutdown_steps(), done, strict=True):
            threading.Thread(
                target=self._run_step, args=(step, loop, finished), daemon=True
            ).start()
        try:
            await asyncio.wait(done, timeout=SHUTDOWN_TIMEOUT_SECONDS)
        finally:
            for finished in done:
                finished.cancel()
            self._loop = None

    def _shutdown_steps(self) -> tuple[Callable[[], None], ...]:
        return (
            self._shutdown_tracer_provider,
            self._shutdown_meter_provider,
            self._close_sentry,
        )

    @staticmethod
    def _run_step(
        step: Callable[[], None], loop: asyncio.AbstractEventLoop, finished: asyncio.Future[None]
    ) -> None:
        try:
            step()
        except Exception:
            logging.getLogger(__name__).warning("Telemetry shutdown step failed")
        with contextlib.suppress(RuntimeError):  # the loop closed while the step ran

            def mark() -> None:
                if not finished.done():
                    finished.set_result(None)

            loop.call_soon_threadsafe(mark)

    def _shutdown_tracer_provider(self) -> None:
        if self.tracer_provider is not None:
            self.tracer_provider.shutdown()

    def _shutdown_meter_provider(self) -> None:
        if self.meter_provider is not None:
            self.meter_provider.shutdown(timeout_millis=int(EXPORT_TIMEOUT_SECONDS * 1000))

    def _close_sentry(self) -> None:
        if self._sentry is not None:
            self._sentry.close(timeout=SENTRY_SHUTDOWN_TIMEOUT_SECONDS)


def _connection_usage(provider: MeterProvider | None) -> otel_metrics.UpDownCounter:
    meter = (provider or otel_metrics.NoOpMeterProvider()).get_meter(SERVICE_NAME)
    return meter.create_up_down_counter("db.client.connections.usage", unit="connections")


# -- Middleware -----------------------------------------------------------------------------


def _request_id_of(scope: Scope) -> str | None:
    request_id = scope.get("state", {}).get("request_id")
    return request_id if isinstance(request_id, str) else None


def _route_template(scope: Scope) -> str | None:
    """The route's template (`/v1/trips/{trip_id}`), or `None` when no route matched.

    A router included under a prefix reports only its own part of the path, so the template
    is the request path with each path parameter's value put back as `{name}`: the label
    stays one per route, never one per ID.
    """
    route = scope.get("route")
    if route is None:
        return None
    template = getattr(route, "path", None)
    # The matched route's own path is the full template unless the route sits in a mounted app
    # (it then holds only its part); the segment count tells the two apart.
    if (
        isinstance(template, str)
        and template.startswith("/")
        and template.count("/") == str(scope["path"]).count("/")
    ):
        return template
    named = {str(value): name for name, value in scope.get("path_params", {}).items()}
    segments = str(scope["path"]).split("/")
    return "/".join("{" + named[s] + "}" if s in named else s for s in segments)


class ErrorReportMiddleware:
    """Inside the request ID middleware: tags the request's log lines and reports a crash.

    An exception that is not an `AppError` goes to Sentry with the request ID, and then
    continues to the request ID middleware, which answers `500 server.error`.
    """

    def __init__(self, app: ASGIApp, observability: Observability) -> None:
        self.app = app
        self.observability = observability

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _request_id_of(scope)
        tokens = structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            await self.app(scope, receive, send)
        except Exception as error:
            if not isinstance(error, AppError):
                self.observability.capture_exception(error, request_id)
            raise
        finally:
            structlog.contextvars.reset_contextvars(**tokens)


class RequestLogMiddleware:
    """The outermost middleware: one JSON line and one latency point per request."""

    def __init__(self, app: ASGIApp, observability: Observability) -> None:
        self.app = app
        self.observability = observability
        self.logger = structlog.get_logger("tripmate.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500  # what the line says when the app breaks before it answers

        async def send_and_note(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_and_note)
        finally:
            self._write(scope, status, time.perf_counter() - started)

    def _write(self, scope: Scope, status: int, seconds: float) -> None:
        try:
            caller = checked_caller(Request(scope))
            trip_id = scope.get("path_params", {}).get("trip_id")
            self.logger.info(
                REQUEST_EVENT,
                request_id=_request_id_of(scope),
                user_id=str(caller.user_id) if caller is not None else None,
                trip_id=str(trip_id) if trip_id is not None else None,
                method=scope["method"],
                path=scope["path"],  # the path only: the query string is in `query_string`
                status=status,
                duration_ms=round(seconds * 1000, 3),
            )
            self.observability.record_request(
                method=scope["method"],
                route=_route_template(scope),
                status=status,
                duration_seconds=seconds,
            )
        except Exception:
            # Logging and metrics never fail a request.
            logging.getLogger(__name__).warning("The request line could not be written")


def install_observability(
    app: FastAPI,
    settings: Settings,
    *,
    sentry_transport: Transport | None = None,
    span_exporter: SpanExporter | None = None,
    metric_reader: MetricReader | None = None,
) -> Observability:
    """Add the error report and trace middleware to `app`; returns the app's `Observability`.

    The three keywords replace the real transports (the tests pass in-memory ones). Call this
    before `install_request_log`, and both before the request ID middleware is added last.
    """
    configure_logging()
    observability = Observability(
        settings,
        sentry_transport=sentry_transport,
        span_exporter=span_exporter,
        metric_reader=metric_reader,
    )
    app.state.observability = observability
    app.add_middleware(ErrorReportMiddleware, observability=observability)
    observability.instrument_app(app)
    return observability


def install_request_log(app: FastAPI) -> None:
    """Add the request line middleware; it must be the outermost, so add it last."""
    app.add_middleware(RequestLogMiddleware, observability=app.state.observability)


__all__ = [
    "Observability",
    "configure_logging",
    "install_observability",
    "install_request_log",
]
