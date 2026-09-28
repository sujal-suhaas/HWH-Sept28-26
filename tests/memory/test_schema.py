"""Schema tests: tags scope recall, metadata carries context. They are different."""

from __future__ import annotations

import pytest

from src.memory.schema import EventType, MemoryEvent, Outcome, runbook_tags
from tests.conftest import make_event


def test_tags_use_scoping_prefixes() -> None:
    tags = make_event().tags()
    assert "service:checkout-api" in tags
    assert "severity:p1" in tags
    assert "incident_type:latency" in tags
    assert "environment:prod" in tags
    assert "event_type:postmortem" in tags


def test_metadata_carries_context_and_is_not_a_tag() -> None:
    event = make_event()
    meta = event.metadata()
    assert meta["incident_id"] == "INC-1001"
    assert meta["event_type"] == "POSTMORTEM"
    assert meta["source"] == "dejaops-backend"
    assert meta["runbook_id"] == "RB-014"
    assert meta["outcome"] == "confirmed"
    assert "timestamp" in meta
    # Context fields must never leak into the recall filter.
    assert not set(meta) & set(event.tags())


def test_tags_are_lowercased_for_scoping() -> None:
    tags = make_event(severity="P1", incident_type="Latency").tags()
    assert "severity:p1" in tags
    assert "incident_type:latency" in tags


def test_optional_dimensions_are_omitted() -> None:
    tags = make_event(service=None, severity=None, incident_type=None).tags()
    assert tags == ["environment:prod", "event_type:postmortem"]


def test_blank_content_rejected() -> None:
    with pytest.raises(ValueError):
        MemoryEvent(event_type=EventType.INCIDENT_OPEN, incident_id="INC-1", content="   ")


def test_document_id_defaults_to_incident_and_event() -> None:
    assert make_event().document_id_or_default() == "INC-1001:postmortem"
    assert make_event(document_id="custom").document_id_or_default() == "custom"


def test_runbook_tags_scope_to_runbook_entries() -> None:
    assert runbook_tags("checkout-api") == [
        "event_type:runbook_entry",
        "service:checkout-api",
    ]


def test_runbook_tags_do_not_scope_by_incident_type() -> None:
    """Runbook events never carry an incident_type tag, so scoping by one would match nothing."""
    assert runbook_tags() == ["event_type:runbook_entry"]
    assert all("incident_type" not in tag for tag in runbook_tags("checkout-api"))


def test_rejected_diagnosis_is_representable_without_becoming_confirmed() -> None:
    event = make_event(
        event_type=EventType.DIAGNOSIS,
        outcome=Outcome.REJECTED,
        content="Agent proposed Redis failover; operator rejected it. Actual cause: Kafka lag.",
    )
    assert event.metadata()["outcome"] == "rejected"
