"""Seed-event construction tests: only confirmed outcomes become durable memory."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.seed_memory import build_seed_events
from src.memory.schema import EventType, Outcome

SEED_DIR = Path("data/seed")


def _seed_incidents() -> list[dict]:
    return json.loads((SEED_DIR / "incidents.json").read_text(encoding="utf-8"))["incidents"]


def test_seed_events_are_deterministic() -> None:
    assert build_seed_events() == build_seed_events()


def test_every_incident_produces_an_incident_open_event() -> None:
    events = build_seed_events()
    opens = {e.incident_id for e in events if e.event_type is EventType.INCIDENT_OPEN}
    assert opens == {i["incident_id"] for i in _seed_incidents()}


def test_resolution_events_exist_only_for_verified_confirmations() -> None:
    events = build_seed_events()
    resolutions = {e.incident_id for e in events if e.event_type is EventType.RESOLUTION}
    expected = {
        i["incident_id"]
        for i in _seed_incidents()
        if i["diagnosis"]["outcome"] == "confirmed" and i["resolution"]["verified"]
    }
    assert resolutions == expected
    assert resolutions  # the dataset must contain confirmations


def test_no_resolution_memory_for_inconclusive_or_rejected_incidents() -> None:
    events = build_seed_events()
    resolutions = {e.incident_id for e in events if e.event_type is EventType.RESOLUTION}
    for incident in _seed_incidents():
        if incident["diagnosis"]["outcome"] in {"inconclusive", "rejected", "pending"}:
            assert incident["incident_id"] not in resolutions


def test_runbook_memory_is_limited_to_runbooks_that_resolved_an_incident() -> None:
    events = build_seed_events()
    promoted = {e.runbook_id for e in events if e.event_type is EventType.RUNBOOK_ENTRY}
    confirmed = {
        i["resolution"]["validated_runbook_id"]
        for i in _seed_incidents()
        if i["resolution"].get("validated_runbook_id")
    }
    assert promoted == confirmed
    assert promoted


def test_every_event_has_scoping_tags_and_context_metadata() -> None:
    for event in build_seed_events():
        tags = event.tags()
        metadata = event.metadata()
        assert any(tag.startswith("environment:") for tag in tags)
        assert any(tag.startswith("event_type:") for tag in tags)
        assert metadata["incident_id"]
        assert metadata["source"] == "dejaops-backend"
        # tags filter recall; metadata never does
        assert not set(metadata) & set(tags)


def test_diagnosis_events_carry_their_outcome() -> None:
    diagnoses = [e for e in build_seed_events() if e.event_type is EventType.DIAGNOSIS]
    assert diagnoses
    assert all(e.outcome is not None for e in diagnoses)
    outcomes = {e.outcome for e in diagnoses}
    assert Outcome.CONFIRMED in outcomes
    assert Outcome.REJECTED in outcomes
    assert Outcome.INCONCLUSIVE in outcomes


def test_postmortem_events_exist_for_the_seeded_postmortems() -> None:
    postmortems = json.loads(
        (SEED_DIR / "postmortems.json").read_text(encoding="utf-8")
    )["postmortems"]
    events = [e for e in build_seed_events() if e.event_type is EventType.POSTMORTEM]
    assert {e.incident_id for e in events} == {p["incident_id"] for p in postmortems}


def test_no_demo_incident_is_seeded_into_history() -> None:
    """The demo must start untaught or the learning demonstration is meaningless."""
    events = build_seed_events()
    assert all(e.incident_id != "DEMO-001" for e in events)
    assert not any("signature verification" in e.content.lower() for e in events)


def test_runbook_events_are_scoped_to_runbook_entries() -> None:
    for event in build_seed_events():
        if event.event_type is EventType.RUNBOOK_ENTRY:
            assert "event_type:runbook_entry" in event.tags()
            assert event.runbook_id
            assert event.outcome is Outcome.CONFIRMED
