"""Trace tests: no embeddings leak, traces are bounded and complete."""

from __future__ import annotations

from src.memory.trace import (
    ErrorCode,
    MemoryMode,
    MemoryOperation,
    MemoryTrace,
    TraceLog,
    sanitize_provider_trace,
    utcnow,
)


def test_provider_trace_strips_embeddings() -> None:
    raw = {
        "query": {"query_text": "checkout latency", "query_embedding": [0.1] * 384},
        "summary": {"results_returned": 3},
    }
    clean = sanitize_provider_trace(raw)
    assert "query_embedding" not in clean["query"]
    assert clean["query"]["query_text"] == "checkout latency"
    assert clean["summary"]["results_returned"] == 3


def test_provider_trace_bounds_large_lists() -> None:
    clean = sanitize_provider_trace({"items": list(range(100))})
    assert len(clean["items"]) == 26  # 25 kept + truncation marker
    assert clean["items"][-1] == "<+75 more>"


def test_provider_trace_truncates_long_strings() -> None:
    clean = sanitize_provider_trace({"note": "x" * 900})
    assert len(clean["note"]) == 503


def test_trace_records_latency_and_outcome() -> None:
    started = utcnow()
    trace = MemoryTrace.build(
        operation=MemoryOperation.RECALL,
        mode=MemoryMode.ON,
        started_at=started,
        success=True,
        bank_id="dejaops-prod",
        hit_count=2,
        query="checkout latency",
        tags=["service:checkout-api"],
    )
    assert trace.success is True
    assert trace.hit_count == 2
    assert trace.latency_ms >= 0
    assert trace.error_code is ErrorCode.NONE
    assert trace.trace_id


def test_failure_trace_is_degraded_and_explicit() -> None:
    trace = MemoryTrace.build(
        operation=MemoryOperation.RECALL,
        mode=MemoryMode.DEGRADED,
        started_at=utcnow(),
        success=False,
        error_code=ErrorCode.UNAVAILABLE,
        error_message="ApiException: 503",
        degraded=True,
        no_match=True,
    )
    assert trace.success is False
    assert trace.degraded is True
    assert trace.no_match is True
    assert trace.error_code is ErrorCode.UNAVAILABLE


def test_trace_log_is_bounded_and_newest_last() -> None:
    log = TraceLog(capacity=3)
    for i in range(5):
        log.add(
            MemoryTrace.build(
                operation=MemoryOperation.RECALL,
                mode=MemoryMode.ON,
                started_at=utcnow(),
                success=True,
                query=f"q{i}",
            )
        )
    queries = [t.query for t in log.all()]
    assert queries == ["q2", "q3", "q4"]


def test_trace_log_filters_by_bank() -> None:
    log = TraceLog()
    log.add(
        MemoryTrace.build(
            operation=MemoryOperation.HEALTH,
            mode=MemoryMode.ON,
            started_at=utcnow(),
            success=True,
            bank_id="bank-a",
        )
    )
    log.add(
        MemoryTrace.build(
            operation=MemoryOperation.HEALTH,
            mode=MemoryMode.ON,
            started_at=utcnow(),
            success=True,
            bank_id="bank-b",
        )
    )
    assert len(log.for_bank("bank-a")) == 1
