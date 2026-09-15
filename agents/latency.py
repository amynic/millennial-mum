"""End-to-end latency instrumentation for the decomposed agent path.

Why this exists
---------------
Warm replies run 28-80s with 50-70s to first token, and until now nothing told
us *where* that time goes. Agent Framework's own OpenTelemetry spans cover model
and tool calls, but there was no single correlated view of one turn across the
PWA -> Function proxy -> hosted agent -> orchestrator -> specialist path, and no
time-to-first-token metric at all.

This module adds the missing pieces:

* a **request id** carried end-to-end (PWA body/header -> proxy -> hosted agent)
  and stamped on every span and log line as ``mm.request_id``;
* **per-hop stage timings** (``mm.stage`` spans) so each leg of the turn is
  measured independently;
* **time to first token** and **total duration**, plus a **cold/warm** flag
  derived from whether this process has served a request before.

Privacy
-------
Everything emitted here is content-free by construction: stage names, durations,
counts, and routing labels only. Prompts, replies, family details, and health
content never touch these spans regardless of ``MM_TRACE_SENSITIVE_DATA``. The
one guard is :func:`safe_attributes`, which drops any attribute whose value is
not a short scalar so a caller can't accidentally leak text through ``**attrs``.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

logger = logging.getLogger("millennial_mum.latency")

#: Header used to carry the correlation id between tiers.
#:
#: The ``x-client-`` prefix is deliberate: the Foundry agent-server extracts
#: every request header with that prefix and exposes it to the handler as
#: ``ResponseContext.client_headers``. Any other name is dropped at the platform
#: boundary, which would break correlation at exactly the hop we most need it.
#: The PWA also sends the id in the JSON body (``request_id``) so the browser
#: doesn't widen the CORS preflight surface; the proxy accepts either.
REQUEST_ID_HEADER = "x-client-mm-request-id"

#: Body field equivalent of :data:`REQUEST_ID_HEADER`.
REQUEST_ID_FIELD = "request_id"

_request_id: ContextVar[str | None] = ContextVar("mm_request_id", default=None)

_PROCESS_START = time.monotonic()
_requests_served = 0

#: Attribute values longer than this are dropped rather than truncated — a long
#: value is a sign someone is passing content, which must never be traced.
_MAX_ATTR_LEN = 64


def new_request_id() -> str:
    """Generate a fresh correlation id for one turn."""
    return uuid.uuid4().hex


def set_request_id(request_id: str | None) -> str:
    """Bind ``request_id`` to the current context, generating one if absent."""
    resolved = (request_id or "").strip() or new_request_id()
    _request_id.set(resolved)
    return resolved


def current_request_id() -> str:
    """Return the bound correlation id, binding a new one if none is set."""
    existing = _request_id.get()
    if existing:
        return existing
    return set_request_id(None)


def claim_cold_start() -> bool:
    """Return ``True`` for the first turn this process serves (a cold turn).

    Cold and warm latency differ by tens of seconds, so every measurement has to
    say which it was. The first request served by a freshly started worker pays
    container start, client construction, and credential acquisition; every
    later request in the same process is warm.
    """
    global _requests_served
    _requests_served += 1
    return _requests_served == 1


def process_uptime_ms() -> float:
    """Milliseconds since this module was first imported (process start)."""
    return (time.monotonic() - _PROCESS_START) * 1000


def safe_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    """Keep only short, content-free scalar attributes.

    Guards the telemetry boundary: numbers and booleans pass through, strings
    pass only if short enough to be a label rather than prose, and everything
    else (lists of text, dicts, model objects) is dropped.
    """
    clean: dict[str, Any] = {}
    for key, value in attrs.items():
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            clean[key] = value
        elif isinstance(value, str) and len(value) <= _MAX_ATTR_LEN:
            clean[key] = value
    return clean


def _tracer():
    """Return an OTel tracer, or ``None`` when OpenTelemetry isn't available."""
    try:  # pragma: no cover - depends on optional OTel install
        from opentelemetry import trace

        return trace.get_tracer("millennial_mum.latency")
    except Exception:
        return None


@dataclass
class StageTiming:
    """One measured hop of a turn."""

    name: str
    duration_ms: float
    attributes: dict[str, Any] = field(default_factory=dict)
    failed: bool = False

    def as_dict(self) -> dict[str, Any]:
        record = {"stage": self.name, "duration_ms": round(self.duration_ms, 1)}
        if self.failed:
            record["failed"] = True
        record.update(self.attributes)
        return record


class TurnTimer:
    """Measures one end-to-end turn: per-stage hops, first token, and total.

    Spans are emitted per stage plus one summary span (``mm.turn``) rather than
    wrapping the whole turn in a parent span. Streaming turns yield across
    suspension points, and holding an OTel context open across ``yield`` leaks
    the active span into unrelated tasks. Correlation is done with the
    ``mm.request_id`` attribute instead, which is what the App Insights queries
    join on anyway.
    """

    def __init__(self, component: str, request_id: str | None = None, *, cold: bool | None = None):
        self.component = component
        self.request_id = set_request_id(request_id)
        self.cold = claim_cold_start() if cold is None else cold
        self.stages: list[StageTiming] = []
        self.first_token_ms: float | None = None
        self.total_ms: float | None = None
        self.attributes: dict[str, Any] = {}
        self._start = time.perf_counter()

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000

    @contextmanager
    def stage(self, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        """Time one hop, emitting a span and recording it on the turn.

        Yields a mutable dict so the caller can attach content-free attributes
        discovered while the stage runs (e.g. the domain that was routed to).
        """
        extra: dict[str, Any] = dict(attrs)
        started = time.perf_counter()
        failed = False
        try:
            yield extra
        except Exception:
            failed = True
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            timing = StageTiming(
                name=name,
                duration_ms=duration_ms,
                attributes=safe_attributes(extra),
                failed=failed,
            )
            self.stages.append(timing)
            self._emit_stage_span(timing)

    def mark_first_token(self) -> float:
        """Record time to first streamed token; later calls are ignored."""
        if self.first_token_ms is None:
            self.first_token_ms = self.elapsed_ms
        return self.first_token_ms

    def annotate(self, **attrs: Any) -> None:
        """Attach content-free attributes to the turn summary."""
        self.attributes.update(safe_attributes(attrs))

    def summary(self) -> dict[str, Any]:
        """Content-free record of the turn, suitable for logs and dashboards."""
        record: dict[str, Any] = {
            "request_id": self.request_id,
            "component": self.component,
            "cold": self.cold,
            "total_ms": round(self.total_ms if self.total_ms is not None else self.elapsed_ms, 1),
            "stages": [stage.as_dict() for stage in self.stages],
        }
        if self.first_token_ms is not None:
            record["first_token_ms"] = round(self.first_token_ms, 1)
        record.update(self.attributes)
        return record

    def finish(self, **attrs: Any) -> dict[str, Any]:
        """Close the turn, emit the summary span, and log it."""
        self.annotate(**attrs)
        if self.total_ms is None:
            self.total_ms = self.elapsed_ms
        record = self.summary()
        self._emit_turn_span(record)
        logger.info("mm.latency %s", json.dumps(record, sort_keys=True))
        return record

    def _span_base(self) -> dict[str, Any]:
        return {"mm.request_id": self.request_id, "mm.component": self.component, "mm.cold": self.cold}

    def _emit_stage_span(self, timing: StageTiming) -> None:
        tracer = _tracer()
        if tracer is None:
            return
        try:  # pragma: no cover - depends on live OTel pipeline
            span = tracer.start_span(f"mm.stage.{timing.name}")
            span.set_attributes(
                {
                    **self._span_base(),
                    "mm.stage": timing.name,
                    "mm.duration_ms": round(timing.duration_ms, 1),
                    "mm.stage_failed": timing.failed,
                    **{f"mm.{k}": v for k, v in timing.attributes.items()},
                }
            )
            span.end()
        except Exception:
            logger.debug("Failed to emit stage span for %s", timing.name, exc_info=True)

    def _emit_turn_span(self, record: dict[str, Any]) -> None:
        tracer = _tracer()
        if tracer is None:
            return
        try:  # pragma: no cover - depends on live OTel pipeline
            span = tracer.start_span("mm.turn")
            attributes = {
                **self._span_base(),
                "mm.total_ms": record["total_ms"],
                "mm.stage_count": len(self.stages),
            }
            if "first_token_ms" in record:
                attributes["mm.first_token_ms"] = record["first_token_ms"]
            attributes.update({f"mm.{k}": v for k, v in self.attributes.items()})
            span.set_attributes(attributes)
            span.end()
        except Exception:
            logger.debug("Failed to emit turn span", exc_info=True)


def extract_request_id(headers: Any) -> str | None:
    """Pull the correlation id out of a header mapping, case-insensitively.

    Accepts anything mapping-like (``dict``, Starlette ``Headers``, ASGI scope
    header lists) and returns ``None`` when the id isn't present, which the
    caller should treat as "generate a fresh one".
    """
    if not headers:
        return None

    getter = getattr(headers, "get", None)
    if callable(getter):
        for key in (REQUEST_ID_HEADER, REQUEST_ID_HEADER.upper(), "X-Client-MM-Request-Id"):
            try:
                value = getter(key)
            except Exception:
                value = None
            if value:
                return str(value).strip() or None

    try:
        items = headers.items() if hasattr(headers, "items") else headers
        for key, value in items:
            name = key.decode() if isinstance(key, bytes) else str(key)
            if name.lower() == REQUEST_ID_HEADER:
                text = value.decode() if isinstance(value, bytes) else str(value)
                return text.strip() or None
    except Exception:
        return None
    return None


def env_flag(name: str, default: bool) -> bool:
    """Read a boolean environment variable with a sane default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
