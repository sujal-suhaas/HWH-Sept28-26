"""Teach then replay, end to end, without a model.

The demo's claim is that an operator's confirmed outcome becomes memory the next
incident can recall. The model's wording is not testable; the *mechanism* is, and
this file tests it against the real feedback lifecycle, the real catalog, and the
real tool handlers.

What this covers: the operator teach writes the right memories, they are scoped so
recall finds them, and a runbook hit is annotated with the cause it treats, which is
how a named cause id becomes reachable at all.

What this does not cover: whether a live model then says the right thing. That is a
measurement, not a test - see `scripts/evaluate_learning.py --mode teach`.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.agent.tools import (
    LookupRunbookArgs,
    ToolContext,
    execute_tool_call,
)
from src.catalog import load_catalog
from src.contracts import FeedbackType
from tests.api.helpers import make_client, memory_store

DEMO = json.loads(Path("data/demo/novel_incident.json").read_text(encoding="utf-8"))
CAUSE = DEMO["hidden_ground_truth"]["root_cause_id"]
RUNBOOK = DEMO["hidden_ground_truth"]["validated_runbook_id"]


def _open_demo_incident(client) -> str:
    """Open the demo alert through the real route, memory on."""
    alert = {
        "service": DEMO["service"],
        "severity": DEMO["severity"],
        "incident_type": DEMO["incident_type"],
        "title": DEMO["alert"]["title"],
        "summary": DEMO["alert"]["summary"],
        "source": DEMO["alert"]["source"],
        "fired_at": DEMO["alert"]["fired_at"],
        "error_samples": DEMO["alert"]["signals"]["error_samples"],
    }
    response = client.post("/alerts", json={"alert": alert, "memory_mode": "on"})
    assert response.status_code == 201, response.text
    return response.json()["incident_id"]


def _lookup(store, symptom: str, service: str) -> str:
    """What `lookup_runbook` would hand the model. This is the model's view."""
    ctx = ToolContext(
        incident_id="INC-TEST", memory=store, catalog=load_catalog(), memory_mode="on"
    )
    outcome = execute_tool_call(
        "lookup_runbook", LookupRunbookArgs(suspected_cause=symptom, service=service), ctx
    )
    return outcome.summary


# --------------------------------------------------------------------------
# Before the teach
# --------------------------------------------------------------------------
def test_before_the_teach_there_is_nothing_to_recall() -> None:
    store = memory_store()
    summary = _lookup(
        store, "signature verification failing for in-flight deliveries", DEMO["service"]
    )
    assert "No validated runbook" in summary
    assert RUNBOOK not in summary
    assert CAUSE not in summary


def test_opening_the_demo_incident_writes_no_authoritative_memory() -> None:
    """An alert creates an INCIDENT_OPEN memory and nothing else."""
    store = memory_store()
    with make_client(store=store) as client:
        _open_demo_incident(client)
    types = [event.event_type.value for event in store.events]
    assert types == ["INCIDENT_OPEN"]
    assert RUNBOOK not in " ".join(event.content for event in store.events)


# --------------------------------------------------------------------------
# The teach
# --------------------------------------------------------------------------
def _teach(client, incident_id: str) -> None:
    """Exactly what the operator does in the UI: correct the cause, confirm the fix."""
    correction = client.post(
        f"/incidents/{incident_id}/feedback",
        json={
            "feedback_type": FeedbackType.OPERATOR_CORRECTION.value,
            "operator": DEMO["operator"],
            "corrected_root_cause": DEMO["hidden_ground_truth"]["root_cause"],
            "validated_fix": DEMO["hidden_ground_truth"]["validated_fix"],
            "validated_runbook_id": RUNBOOK,
        },
    )
    assert correction.status_code == 200, correction.text

    resolved = client.post(
        f"/incidents/{incident_id}/feedback",
        json={
            "feedback_type": FeedbackType.RESOLUTION_CONFIRMED.value,
            "operator": DEMO["operator"],
            "validated_fix": DEMO["hidden_ground_truth"]["validated_fix"],
            "validated_runbook_id": RUNBOOK,
        },
    )
    assert resolved.status_code == 200, resolved.text


def test_the_operator_can_teach_the_demo_cause() -> None:
    """The catalog accepts RC-009 and RB-051, or the teach 422s and the demo is dead."""
    store = memory_store()
    with make_client(store=store) as client:
        incident_id = _open_demo_incident(client)
        _teach(client, incident_id)
    assert client.get(f"/incidents/{incident_id}").json()["state"] == "RESOLVED"


def test_the_teach_writes_authoritative_memory() -> None:
    store = memory_store()
    with make_client(store=store) as client:
        incident_id = _open_demo_incident(client)
        _teach(client, incident_id)

    types = [event.event_type.value for event in store.events]
    assert "OPERATOR_CORRECTION" in types
    assert "RESOLUTION" in types
    # A confirmed resolution with a runbook promotes it.
    assert "RUNBOOK_ENTRY" in types


def test_the_taught_runbook_is_retained_under_its_own_id() -> None:
    store = memory_store()
    with make_client(store=store) as client:
        incident_id = _open_demo_incident(client)
        _teach(client, incident_id)

    promoted = [event for event in store.events if event.runbook_id == RUNBOOK]
    assert promoted, f"{RUNBOOK} was not promoted to memory"
    assert all(event.service == DEMO["service"] for event in promoted)


def test_a_runbook_with_no_service_is_not_scoped_away() -> None:
    """The promoted runbook must be recallable for the service it resolved on."""
    store = memory_store()
    with make_client(store=store) as client:
        incident_id = _open_demo_incident(client)
        _teach(client, incident_id)

    # By event type, not by runbook id: the correction and resolution memories also
    # carry the runbook id, and only the promotion is scoped as a runbook entry.
    promoted = next(
        event
        for event in store.events
        if event.runbook_id == RUNBOOK and event.event_type.value == "RUNBOOK_ENTRY"
    )
    assert f"service:{DEMO['service']}" in promoted.tags()
    assert "event_type:runbook_entry" in promoted.tags()


# --------------------------------------------------------------------------
# The replay
# --------------------------------------------------------------------------
def test_after_the_teach_recall_finds_the_taught_runbook() -> None:
    store = memory_store()
    with make_client(store=store) as client:
        incident_id = _open_demo_incident(client)
        _teach(client, incident_id)

    summary = _lookup(
        store, "signature verification failing for in-flight deliveries", DEMO["service"]
    )
    assert RUNBOOK in summary, "the taught runbook was not retrievable"


def test_the_recalled_runbook_tells_the_model_which_cause_it_treats() -> None:
    """Without this annotation the model can retrieve the fix but cannot name the cause.

    This is the link that makes Root Cause Hit@1 reachable after a teach: the cause
    id is not in the memory text, so it has to travel with the runbook hit.
    """
    store = memory_store()
    with make_client(store=store) as client:
        incident_id = _open_demo_incident(client)
        _teach(client, incident_id)

    summary = _lookup(
        store, "signature verification failing for in-flight deliveries", DEMO["service"]
    )
    assert f"root_cause_id={CAUSE}" in summary


def test_the_taught_correction_is_recallable_as_history() -> None:
    """The correction is the operator's own words, and recall can surface them."""
    store = memory_store()
    with make_client(store=store) as client:
        incident_id = _open_demo_incident(client)
        _teach(client, incident_id)

    hits = store.recall(
        "webhook signature verification key rotation", tags=[f"service:{DEMO['service']}"], limit=5
    )
    assert hits.hits, "the teach produced no recallable history for this service"
    assert any(
        DEMO["hidden_ground_truth"]["root_cause"] in hit.text for hit in hits.hits
    ), "the operator's corrected cause is not in any recallable memory"


def test_another_service_cannot_recall_the_teach() -> None:
    """The teach is scoped to its service, so it does not leak across the estate."""
    store = memory_store()
    with make_client(store=store) as client:
        incident_id = _open_demo_incident(client)
        _teach(client, incident_id)

    summary = _lookup(
        store, "signature verification failing for in-flight deliveries", "auth-service"
    )
    assert RUNBOOK not in summary


def test_the_demo_cause_was_not_reachable_before_the_teach_and_is_after() -> None:
    """The before/after the demo claims, at the level the mechanism can prove."""
    store = memory_store()
    before = _lookup(
        store, "signature verification failing for in-flight deliveries", DEMO["service"]
    )
    assert f"root_cause_id={CAUSE}" not in before

    with make_client(store=store) as client:
        incident_id = _open_demo_incident(client)
        _teach(client, incident_id)

    after = _lookup(
        store, "signature verification failing for in-flight deliveries", DEMO["service"]
    )
    assert f"root_cause_id={CAUSE}" in after


def test_memory_off_teaches_nothing() -> None:
    """The control: a memory-off incident writes no durable memory at all."""
    store = memory_store()
    with make_client(store=store) as client:
        alert = {
            "service": DEMO["service"],
            "severity": DEMO["severity"],
            "incident_type": DEMO["incident_type"],
            "title": DEMO["alert"]["title"],
            "summary": DEMO["alert"]["summary"],
        }
        incident_id = client.post(
            "/alerts", json={"alert": alert, "memory_mode": "off"}
        ).json()["incident_id"]
        response = client.post(
            f"/incidents/{incident_id}/feedback",
            json={
                "feedback_type": FeedbackType.RESOLUTION_CONFIRMED.value,
                "operator": DEMO["operator"],
                "validated_fix": DEMO["hidden_ground_truth"]["validated_fix"],
                "validated_runbook_id": RUNBOOK,
            },
        )
        assert response.status_code == 200, response.text

    assert store.events == [], "a memory-off incident must not write durable memory"
    assert RUNBOOK not in _lookup(
        store, "signature verification failing for in-flight deliveries", DEMO["service"]
    )
