"""Sprint 17.2 — OpenTelemetry-style trace export.

Lightweight: a NoOp tracer + an OTLP HTTP exporter shim. We don't pull
in the full `opentelemetry-*` stack to keep the sweeper's footprint
small. The exporter is pluggable so a real OTel collector can replace
the local buffer.

Provides:
  - Tracer(): get_tracer()
  - start_span(name) / span.end() / span.set_attribute()
  - InMemoryExporter: stores spans in a list (for tests)
  - OtlpHttpExporter: POSTs to configured URL (no-op on import error)
  - 1-in-100 sampler (configurable rate)
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import structlog

logger = structlog.get_logger("ipracticom.monitoring.otel")

from .._log import log_suppressed



@dataclass
class Span:
    """A single trace span."""
    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    start_time: float = 0.0
    end_time: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # ok | error

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, status: str) -> None:
        self.status = status

    def end(self) -> None:
        if self.end_time == 0.0:
            self.end_time = time.time()

    def duration_ms(self) -> float:
        if self.end_time == 0.0:
            return 0.0
        return (self.end_time - self.start_time) * 1000.0


class InMemoryExporter:
    """Stores spans in a list — useful for tests and local debugging."""

    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def clear(self) -> None:
        self.spans.clear()

    def by_name(self, name: str) -> list[Span]:
        return [s for s in self.spans if s.name == name]


class OtlpHttpExporter:
    """POSTs spans to an OTLP HTTP endpoint as JSON.

    Endpoint: env SWEEPER_OTLP_ENDPOINT (e.g. http://otel-collector:4318/v1/traces)
    No-op if endpoint not configured.
    """

    def __init__(self, endpoint: Optional[str] = None, timeout: float = 5.0) -> None:
        self.endpoint = endpoint or os.environ.get("SWEEPER_OTLP_ENDPOINT", "").strip()
        self.timeout = timeout

    def export(self, span: Span) -> None:
        if not self.endpoint:
            return
        try:
            payload = json.dumps({
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "parent_span_id": span.parent_span_id,
                "name": span.name,
                "start_time": span.start_time,
                "end_time": span.end_time,
                "duration_ms": span.duration_ms(),
                "attributes": span.attributes,
                "status": span.status,
            }).encode("utf-8")
            req = urllib.request.Request(
                self.endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout):
                pass
        except Exception as e:
            logger.debug("otlp_export_failed", error=str(e))


class Sampler:
    """Random sampler. Default rate = 1/100 (1%)."""

    def __init__(self, rate: float = 0.01) -> None:
        self.rate = max(0.0, min(1.0, rate))

    def should_sample(self) -> bool:
        if self.rate >= 1.0:
            return True
        if self.rate <= 0.0:
            return False
        return random.random() < self.rate


class Tracer:
    """A minimal tracer. Use as a context manager."""

    def __init__(
        self,
        sampler: Optional[Sampler] = None,
        exporter: Optional[Any] = None,
    ) -> None:
        self.sampler = sampler or Sampler()
        self.exporter = exporter or InMemoryExporter()
        self._trace_counter = 0
        self._span_counter = 0
        self._current_trace_id: Optional[str] = None
        self._current_span_id: Optional[str] = None

    def _next_id(self, prefix: str) -> str:
        if prefix == "trace":
            self._trace_counter += 1
            return f"trace-{self._trace_counter:08x}"
        self._span_counter += 1
        return f"span-{self._span_counter:08x}"

    def start_span(self, name: str, parent_span_id: Optional[str] = None) -> Span:
        """Start a new span. Respects the sampler at the trace root."""
        if not self.sampler.should_sample():
            # Return a NoOp span
            return Span(
                name=name,
                trace_id="noop",
                span_id="noop",
                parent_span_id=parent_span_id,
            )
        if self._current_trace_id is None or parent_span_id is None:
            # New root span
            self._current_trace_id = self._next_id("trace")
            parent = None
        else:
            parent = self._current_span_id
        self._current_span_id = self._next_id("span")
        span = Span(
            name=name,
            trace_id=self._current_trace_id,
            span_id=self._current_span_id,
            parent_span_id=parent,
            start_time=time.time(),
        )
        return span

    def finish_span(self, span: Span) -> None:
        """End a span and export it."""
        span.end()
        if span.trace_id != "noop" and self.exporter is not None:
            try:
                self.exporter.export(span)
            except Exception as e:
                log_suppressed("otel_exporter_export", e)
        # Pop the span stack: if this was a child, restore parent as current
        if span.parent_span_id:
            self._current_span_id = span.parent_span_id
        else:
            self._current_span_id = None
            self._current_trace_id = None


# Module-level singleton
_TRACER: Optional[Tracer] = None


def get_tracer() -> Tracer:
    """Return the global tracer instance (created lazily)."""
    global _TRACER
    if _TRACER is None:
        rate_env = os.environ.get("SWEEPER_TRACE_SAMPLE_RATE", "0.01")
        try:
            rate = float(rate_env)
        except (TypeError, ValueError):
            rate = 0.01
        endpoint = os.environ.get("SWEEPER_OTLP_ENDPOINT", "").strip()
        if endpoint:
            exporter = OtlpHttpExporter(endpoint)
        else:
            exporter = InMemoryExporter()
        _TRACER = Tracer(sampler=Sampler(rate=rate), exporter=exporter)
    return _TRACER


def reset_tracer() -> None:
    """Reset the global tracer (useful for tests)."""
    global _TRACER
    _TRACER = None
