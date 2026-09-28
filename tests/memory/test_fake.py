"""Fake store tests - it is a test double, so its filtering must be trustworthy."""

from __future__ import annotations

from src.memory.fake import InMemoryMemoryStore
from src.memory.schema import EventType
from tests.conftest import make_event


def test_tag_filter_excludes_other_services() -> None:
    store = InMemoryMemoryStore()
    store.retain(make_event(service="checkout-api", content="checkout latency kafka consumer lag"))
    store.retain(
        make_event(
            incident_id="INC-2",
            service="auth-service",
            content="checkout latency kafka consumer lag",
        )
    )

    outcome = store.recall("checkout latency kafka consumer lag", tags=["service:checkout-api"])

    assert len(outcome.hits) == 1
    assert outcome.hits[0].incident_id == "INC-1001"


def test_all_match_requires_every_tag() -> None:
    store = InMemoryMemoryStore()
    store.retain(
        make_event(service="checkout-api", severity="p1", content="checkout latency kafka lag")
    )

    hit = store.recall(
        "checkout latency kafka lag",
        tags=["service:checkout-api", "severity:p1"],
        tags_match="all",
    )
    assert hit.hits

    # "all" is the default, so a non-matching tag must exclude the memory.
    assert not store.recall(
        "checkout latency kafka lag", tags=["service:checkout-api", "severity:p3"]
    ).hits


def test_default_scope_is_all_not_any() -> None:
    store = InMemoryMemoryStore()
    store.retain(make_event(service="checkout-api", content="checkout latency kafka lag"))

    # An extra, unmatched tag must narrow the scope rather than widen it.
    assert not store.recall(
        "checkout latency kafka lag", tags=["service:checkout-api", "event_type:resolution"]
    ).hits
    assert store.recall(
        "checkout latency kafka lag", tags=["service:checkout-api", "event_type:postmortem"]
    ).hits


def test_no_shared_tags_yields_no_match() -> None:
    store = InMemoryMemoryStore()
    store.retain(make_event())

    outcome = store.recall("checkout latency", tags=["service:auth-service"])

    assert outcome.no_match is True
    assert outcome.trace.success is True


def test_low_overlap_is_filtered_by_min_score() -> None:
    store = InMemoryMemoryStore(min_score=0.9)
    store.retain(make_event(content="checkout latency kafka consumer lag"))

    outcome = store.recall(
        "checkout latency unrelated words entirely", tags=["service:checkout-api"]
    )

    assert outcome.no_match is True


def test_recall_failure_is_degraded_not_empty_success() -> None:
    store = InMemoryMemoryStore(fail_recall=True)
    store.retain(make_event())

    outcome = store.recall("checkout latency", tags=["service:checkout-api"])

    assert outcome.hits == []
    assert outcome.trace.success is False
    assert outcome.trace.degraded is True


def test_retain_failure_is_not_recorded_as_stored() -> None:
    store = InMemoryMemoryStore(fail_retain=True)

    trace = store.retain(make_event())

    assert trace.success is False
    assert store.events == []


def test_runbook_events_are_scopable() -> None:
    store = InMemoryMemoryStore()
    store.retain(
        make_event(
            event_type=EventType.RUNBOOK_ENTRY,
            runbook_id="RB-014",
            content="Validated runbook: scale payments-ledger consumer group then replay partition",
        )
    )
    store.retain(make_event(content="Validated runbook scale consumer group replay partition"))

    outcome = store.recall(
        "validated runbook scale consumer group replay partition",
        tags=["event_type:runbook_entry"],
    )

    assert len(outcome.hits) == 1
    assert outcome.hits[0].runbook_id == "RB-014"
