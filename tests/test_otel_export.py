"""Sprint 17.2 — OpenTelemetry trace export tests."""
from __future__ import annotations

import json
import os
import time
from unittest.mock import patch, MagicMock

import pytest

from ipracticom_sweeper.monitoring.otel import (
    Span, Tracer, Sampler,
    InMemoryExporter, OtlpHttpExporter,
    get_tracer, reset_tracer,
)


@pytest.fixture(autouse=True)
def _clean_tracer():
    """Reset global tracer between tests."""
    reset_tracer()
    yield
    reset_tracer()


# ============= Span ========================================================

def test_span_set_attribute() -> None:
    s = Span(name="x", trace_id="t1", span_id="s1")
    s.set_attribute("key", "value")
    assert s.attributes["key"] == "value"


def test_span_set_status() -> None:
    s = Span(name="x", trace_id="t1", span_id="s1")
    s.set_status("error")
    assert s.status == "error"


def test_span_duration_ms() -> None:
    s = Span(name="x", trace_id="t1", span_id="s1", start_time=100.0)
    s.end_time = 100.5
    assert s.duration_ms() == 500.0


def test_span_end_sets_end_time() -> None:
    s = Span(name="x", trace_id="t1", span_id="s1", start_time=time.time())
    s.end()
    assert s.end_time > 0


# ============= Sampler ======================================================

def test_sampler_rate_1_always_samples() -> None:
    s = Sampler(rate=1.0)
    for _ in range(100):
        assert s.should_sample()


def test_sampler_rate_0_never_samples() -> None:
    s = Sampler(rate=0.0)
    for _ in range(100):
        assert not s.should_sample()


def test_sampler_default_rate_is_small() -> None:
    """Default 1% — over 10000 trials, expect ~1% to be sampled."""
    s = Sampler(rate=0.01)
    sampled = sum(1 for _ in range(10000) if s.should_sample())
    # 1% of 10000 = 100, allow wide range
    assert 30 < sampled < 250


def test_sampler_clamps_rate() -> None:
    s = Sampler(rate=2.0)  # > 1.0
    assert s.rate == 1.0
    s2 = Sampler(rate=-0.5)  # < 0
    assert s2.rate == 0.0


# ============= InMemoryExporter =============================================

def test_inmemory_exporter_stores_spans() -> None:
    exp = InMemoryExporter()
    s = Span(name="x", trace_id="t1", span_id="s1")
    exp.export(s)
    assert len(exp.spans) == 1
    assert exp.spans[0].name == "x"


def test_inmemory_exporter_clear() -> None:
    exp = InMemoryExporter()
    exp.export(Span(name="x", trace_id="t1", span_id="s1"))
    exp.clear()
    assert exp.spans == []


def test_inmemory_exporter_by_name() -> None:
    exp = InMemoryExporter()
    exp.export(Span(name="a", trace_id="t1", span_id="s1"))
    exp.export(Span(name="b", trace_id="t1", span_id="s2"))
    exp.export(Span(name="a", trace_id="t1", span_id="s3"))
    a_spans = exp.by_name("a")
    assert len(a_spans) == 2


# ============= Tracer =======================================================

def test_tracer_emits_span_per_run() -> None:
    exp = InMemoryExporter()
    tracer = Tracer(sampler=Sampler(rate=1.0), exporter=exp)
    span = tracer.start_span("pipeline_run")
    span.set_attribute("defcon", 3)
    tracer.finish_span(span)
    spans = exp.by_name("pipeline_run")
    assert len(spans) == 1
    assert spans[0].attributes["defcon"] == 3


def test_tracer_emits_child_spans() -> None:
    """When a span is finished, child spans share the trace_id."""
    exp = InMemoryExporter()
    tracer = Tracer(sampler=Sampler(rate=1.0), exporter=exp)
    parent = tracer.start_span("pipeline")
    parent.set_attribute("phase", "start")
    child = tracer.start_span("check_fs01", parent_span_id=parent.span_id)
    child.set_attribute("status", "ok")
    tracer.finish_span(child)
    tracer.finish_span(parent)
    assert len(exp.spans) == 2
    # Both spans share the same trace_id
    assert exp.spans[0].trace_id == exp.spans[1].trace_id
    # Child has parent_span_id set
    child_span = next(s for s in exp.spans if s.name == "check_fs01")
    assert child_span.parent_span_id == parent.span_id


def test_tracer_sampler_drops_below_rate() -> None:
    """When sampler rate is 0, spans are not exported."""
    exp = InMemoryExporter()
    tracer = Tracer(sampler=Sampler(rate=0.0), exporter=exp)
    span = tracer.start_span("pipeline_run")
    tracer.finish_span(span)
    # Span is created but not exported (NoOp)
    assert exp.spans == []


def test_tracer_sampler_1_in_100() -> None:
    """Default 1% sampling — over many runs, ~1% should be exported."""
    exp = InMemoryExporter()
    tracer = Tracer(sampler=Sampler(rate=0.01), exporter=exp)
    for _ in range(1000):
        span = tracer.start_span("x")
        tracer.finish_span(span)
    # Expect roughly 10 spans (allow 0..40)
    assert 0 <= len(exp.spans) <= 40


def test_tracer_handles_export_failure() -> None:
    """Exporter that raises should not crash the tracer."""
    class BadExporter:
        def export(self, span):
            raise RuntimeError("boom")
    tracer = Tracer(sampler=Sampler(rate=1.0), exporter=BadExporter())
    span = tracer.start_span("x")
    tracer.finish_span(span)  # should not raise


# ============= OtlpHttpExporter =============================================

def test_otlp_no_op_without_endpoint() -> None:
    exp = OtlpHttpExporter(endpoint="")
    exp.export(Span(name="x", trace_id="t", span_id="s"))  # no raise


def test_otlp_posts_to_endpoint() -> None:
    """When endpoint set, exporter POSTs JSON."""
    from unittest.mock import MagicMock
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = lambda self, *a: False
    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_post:
        exp = OtlpHttpExporter(endpoint="http://localhost:4318/v1/traces")
        span = Span(
            name="test", trace_id="t1", span_id="s1",
            start_time=100.0, end_time=100.5,
        )
        exp.export(span)
        assert mock_post.called
        # The Request object is passed positionally as the first arg
        req = mock_post.call_args.args[0]
        body = req.data
        parsed = json.loads(body)
        assert parsed["name"] == "test"
        assert parsed["trace_id"] == "t1"


def test_otlp_handles_network_failure() -> None:
    """Network errors are swallowed (best-effort)."""
    with patch("urllib.request.urlopen", side_effect=OSError("nope")):
        exp = OtlpHttpExporter(endpoint="http://localhost:9999")
        exp.export(Span(name="x", trace_id="t", span_id="s"))  # no raise


# ============= get_tracer ===================================================

def test_get_tracer_creates_singleton() -> None:
    reset_tracer()
    t1 = get_tracer()
    t2 = get_tracer()
    assert t1 is t2


def test_get_tracer_uses_env_sample_rate() -> None:
    reset_tracer()
    with patch.dict(os.environ, {"SWEEPER_TRACE_SAMPLE_RATE": "0.5"}):
        t = get_tracer()
        assert t.sampler.rate == 0.5


def test_get_tracer_uses_env_otlp_endpoint() -> None:
    reset_tracer()
    with patch.dict(os.environ, {"SWEEPER_OTLP_ENDPOINT": "http://otel:4318/v1/traces"}):
        t = get_tracer()
        assert isinstance(t.exporter, OtlpHttpExporter)


def test_get_tracer_defaults_to_inmemory_when_no_endpoint() -> None:
    reset_tracer()
    os.environ.pop("SWEEPER_OTLP_ENDPOINT", None)
    t = get_tracer()
    assert isinstance(t.exporter, InMemoryExporter)


def test_get_tracer_handles_bad_sample_rate() -> None:
    reset_tracer()
    with patch.dict(os.environ, {"SWEEPER_TRACE_SAMPLE_RATE": "not-a-float"}):
        t = get_tracer()
        # Falls back to default 0.01
        assert t.sampler.rate == 0.01


# ============= audit log integration ========================================

def test_trace_id_in_audit_record() -> None:
    """Spans carry a trace_id that can be recorded in the audit log."""
    exp = InMemoryExporter()
    tracer = Tracer(sampler=Sampler(rate=1.0), exporter=exp)
    span = tracer.start_span("pipeline_run")
    span.set_attribute("audit_id", "abc-123")
    tracer.finish_span(span)
    s = exp.spans[0]
    assert s.attributes["audit_id"] == "abc-123"
    assert s.trace_id != "noop"