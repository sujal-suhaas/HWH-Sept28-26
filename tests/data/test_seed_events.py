"""Seed-event construction tests: only confirmed outcomes become durable memory."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.seed_memory import (
    build_seed_events,
    completed_document_ids,
    pending_events,
)
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


def test_a_runbook_is_retained_once_per_service_it_validated() -> None:
    """A runbook validated for two services must be findable from either one."""
    events = build_seed_events()
    runbooks = [e for e in events if e.event_type is EventType.RUNBOOK_ENTRY]

    # Exactly one event per (runbook, service), with a distinct document id.
    pairs = [(e.runbook_id, e.service) for e in runbooks]
    assert len(pairs) == len(set(pairs)), "duplicate runbook events for the same service"
    documents = [e.document_id_or_default() for e in runbooks]
    assert len(documents) == len(set(documents))
    assert all(service for _, service in pairs)

    services_per_runbook: dict[str, set[str]] = {}
    for runbook_id, service in pairs:
        services_per_runbook.setdefault(runbook_id or "?", set()).add(service or "?")

    # The defect this guards: a runbook used by several services was tagged with
    # only the first one, so a service-scoped lookup could never find it.
    multi_service = {rb: svc for rb, svc in services_per_runbook.items() if len(svc) > 1}
    assert multi_service, "expected at least one runbook validated for multiple services"


def test_every_resolution_runbook_has_a_matching_service_scoped_runbook_event() -> None:
    """Scoping a runbook lookup by service must actually find the runbook."""
    events = build_seed_events()
    tagged = {
        (e.runbook_id, e.service) for e in events if e.event_type is EventType.RUNBOOK_ENTRY
    }
    for event in events:
        if event.event_type is not EventType.RESOLUTION or not event.runbook_id:
            continue
        assert (event.runbook_id, event.service) in tagged


# --- §13: duplicate seeding is idempotent, and never silently a no-op ----------


def test_a_completed_seed_leaves_nothing_pending() -> None:
    """Re-running the seeder against its own state must retain nothing."""
    events = build_seed_events()
    done = {event.document_id_or_default() for event in events}

    assert pending_events(events, done) == []


def test_state_from_another_bank_does_not_mark_documents_done() -> None:
    """A bank switch must reseed, not skip everything and report success.

    This is the silent failure: the old state file names every document, so
    trusting it would leave the new bank empty while the run looked clean.
    """
    events = build_seed_events()
    foreign = {
        "bank_id": "some-other-bank",
        "document_ids": [event.document_id_or_default() for event in events],
    }

    done = completed_document_ids(foreign, "dejaops-prod")

    assert done == set()
    assert len(pending_events(events, done)) == len(events)


def test_state_from_this_bank_does_mark_documents_done() -> None:
    events = build_seed_events()
    mine = {
        "bank_id": "dejaops-prod",
        "document_ids": [event.document_id_or_default() for event in events],
    }

    assert len(completed_document_ids(mine, "dejaops-prod")) == len(events)


def test_a_partial_seed_resumes_at_the_unretained_events() -> None:
    """A run that died halfway must continue, not start over."""
    events = build_seed_events()
    first_five = {event.document_id_or_default() for event in events[:5]}

    pending = pending_events(events, first_five)

    assert len(pending) == len(events) - 5
    assert pending[0] == events[5]


def test_state_with_no_document_ids_is_treated_as_empty() -> None:
    """An older or truncated state file must not crash the seeder."""
    assert completed_document_ids({"bank_id": "dejaops-prod"}, "dejaops-prod") == set()
    assert completed_document_ids({}, "dejaops-prod") == set()
