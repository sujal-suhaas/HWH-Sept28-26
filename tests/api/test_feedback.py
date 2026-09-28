"""Operator feedback route tests.

The rule under test throughout: a model proposal is never authoritative memory,
and only an explicit operator outcome creates one.
"""

from __future__ import annotations

from src.memory import EventType, Outcome, runbook_tags
from tests.agent.fake_llm import FakeLLM
from tests.api.helpers import (
    CHECKOUT,
    RB_LEDGER_CONSUMER_GROUP,
    RC_KAFKA_LAG,
    alert_body,
    investigate_then_propose,
    make_client,
    memory_store,
)


def open_incident(client, *, memory_mode: str | None = None) -> dict:
    response = client.post("/alerts", json=alert_body(memory_mode=memory_mode))
    assert response.status_code == 201, response.text
    return response.json()


def submit(client, incident_id: str, **body: object):
    payload = {"operator": "m.iyer", **body}
    return client.post(f"/incidents/{incident_id}/feedback", json=payload)


def events_of_type(store, event_type: EventType) -> list:
    return [event for event in store.events if event.event_type is event_type]


# --------------------------------------------------------------------------
# The confirmation boundary
# --------------------------------------------------------------------------
def test_running_the_agent_writes_no_authoritative_memory() -> None:
    """A proposal is not an outcome, however confident the model is."""
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)

    assert incident["proposed_diagnosis"] is not None
    assert incident["proposed_resolution"] is not None
    authoritative = {EventType.DIAGNOSIS, EventType.RESOLUTION, EventType.RUNBOOK_ENTRY}
    assert not authoritative & {event.event_type for event in store.events}
    # Only the alert itself was retained.
    assert [event.event_type for event in store.events] == [EventType.INCIDENT_OPEN]


def test_confirming_a_diagnosis_writes_exactly_one_diagnosis_memory() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        response = submit(
            client,
            incident["incident_id"],
            feedback_type="DIAGNOSIS_CONFIRMED",
            root_cause_id=RC_KAFKA_LAG,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "RESOLVING"
    assert body["operator_outcome"] == "DIAGNOSIS_CONFIRMED"
    assert body["root_cause_id"] == RC_KAFKA_LAG

    diagnoses = events_of_type(store, EventType.DIAGNOSIS)
    assert len(diagnoses) == 1
    assert diagnoses[0].outcome is Outcome.CONFIRMED
    assert RC_KAFKA_LAG in diagnoses[0].content
    # The agent's own proposal is preserved as the hypothesis, not as the cause.
    assert "Agent hypothesis" in diagnoses[0].content


def test_a_rejected_diagnosis_never_becomes_a_confirmed_root_cause() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        body = submit(
            client, incident["incident_id"], feedback_type="DIAGNOSIS_REJECTED"
        ).json()

    assert body["state"] == "DIAGNOSING"
    assert body["root_cause_id"] is None
    diagnoses = events_of_type(store, EventType.DIAGNOSIS)
    assert [event.outcome for event in diagnoses] == [Outcome.REJECTED]
    assert not any(event.outcome is Outcome.CONFIRMED for event in store.events)


def test_an_operator_correction_overrides_the_model_proposal() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        body = submit(
            client,
            incident["incident_id"],
            feedback_type="OPERATOR_CORRECTION",
            corrected_root_cause="It was a Redis failover flap, not Kafka lag.",
            root_cause_id="RC-002",
        ).json()

    assert body["state"] == "RESOLVING"
    corrections = events_of_type(store, EventType.OPERATOR_CORRECTION)
    assert len(corrections) == 1
    assert "the agent was wrong" in corrections[0].content
    assert "Redis failover flap" in corrections[0].content
    assert body["root_cause_id"] == "RC-002"


def test_confirming_a_resolution_promotes_the_runbook() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        body = submit(
            client,
            incident["incident_id"],
            feedback_type="RESOLUTION_CONFIRMED",
            validated_fix="Scaled the ledger consumer group and replayed the partition.",
            validated_runbook_id=RB_LEDGER_CONSUMER_GROUP,
        ).json()

    assert body["state"] == "RESOLVED"
    assert body["validated_runbook_id"] == RB_LEDGER_CONSUMER_GROUP

    resolutions = events_of_type(store, EventType.RESOLUTION)
    runbooks = events_of_type(store, EventType.RUNBOOK_ENTRY)
    assert len(resolutions) == 1 and resolutions[0].outcome is Outcome.CONFIRMED
    assert len(runbooks) == 1
    assert runbooks[0].runbook_id == RB_LEDGER_CONSUMER_GROUP


def test_a_promoted_runbook_is_actually_findable_by_service_scope() -> None:
    """The promotion is only useful if runbook recall can see it afterwards."""
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        submit(
            client,
            incident["incident_id"],
            feedback_type="RESOLUTION_CONFIRMED",
            validated_fix="Scaled the ledger consumer group.",
            validated_runbook_id=RB_LEDGER_CONSUMER_GROUP,
        )

    found = store.recall(
        "checkout-api kafka consumer lag",
        tags=runbook_tags(CHECKOUT),
        min_score=0.0,
    )
    assert found.hits, "the promoted runbook must be recallable for this service"
    assert any(hit.runbook_id == RB_LEDGER_CONSUMER_GROUP for hit in found.hits)


def test_a_failed_resolution_is_never_a_validated_runbook() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        body = submit(
            client,
            incident["incident_id"],
            feedback_type="RESOLUTION_FAILED",
            note="scaling the consumer group did not help",
        ).json()

    assert body["state"] == "RESOLVING"
    assert body["validated_runbook_id"] is None
    assert body["root_cause_id"] is None

    resolutions = events_of_type(store, EventType.RESOLUTION)
    assert [event.outcome for event in resolutions] == [Outcome.FAILED]
    assert events_of_type(store, EventType.RUNBOOK_ENTRY) == []


def test_inconclusive_lands_inconclusive_and_records_why() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        body = submit(
            client,
            incident["incident_id"],
            feedback_type="INCONCLUSIVE",
            note="mitigated manually, cause unknown",
        ).json()

    assert body["state"] == "INCONCLUSIVE"
    diagnoses = events_of_type(store, EventType.DIAGNOSIS)
    assert [event.outcome for event in diagnoses] == [Outcome.INCONCLUSIVE]


# --------------------------------------------------------------------------
# Rejected feedback
# --------------------------------------------------------------------------
def test_an_unknown_root_cause_id_is_422() -> None:
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        response = submit(
            client,
            incident["incident_id"],
            feedback_type="DIAGNOSIS_CONFIRMED",
            root_cause_id="RC-999",
        )

    assert response.status_code == 422
    assert "RC-999" in response.json()["detail"]


def test_an_unknown_runbook_id_is_422() -> None:
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        response = submit(
            client,
            incident["incident_id"],
            feedback_type="RESOLUTION_CONFIRMED",
            validated_fix="something",
            validated_runbook_id="RB-999",
        )

    assert response.status_code == 422
    assert "RB-999" in response.json()["detail"]


def test_feedback_that_contradicts_a_resolved_incident_is_422() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        submit(
            client,
            incident["incident_id"],
            feedback_type="RESOLUTION_CONFIRMED",
            validated_fix="scaled it",
        )
        response = submit(
            client, incident["incident_id"], feedback_type="RESOLUTION_FAILED", note="nope"
        )

    assert response.status_code == 422
    assert "already RESOLVED" in response.json()["detail"]


def test_a_rejected_feedback_writes_no_memory() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        before = list(store.events)
        submit(
            client,
            incident["incident_id"],
            feedback_type="DIAGNOSIS_CONFIRMED",
            root_cause_id="RC-999",
        )

    assert store.events == before


def test_unknown_feedback_type_is_422() -> None:
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        response = submit(client, incident["incident_id"], feedback_type="LOOKS_GOOD")

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Contract requirements per feedback type
# --------------------------------------------------------------------------
def test_a_confirmed_diagnosis_requires_a_root_cause() -> None:
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        response = submit(client, incident["incident_id"], feedback_type="DIAGNOSIS_CONFIRMED")

    assert response.status_code == 422
    assert "root_cause_id" in str(response.json())


def test_a_confirmed_resolution_requires_a_validated_fix() -> None:
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        response = submit(client, incident["incident_id"], feedback_type="RESOLUTION_CONFIRMED")

    assert response.status_code == 422
    assert "validated_fix" in str(response.json())


def test_an_operator_correction_requires_the_corrected_cause() -> None:
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        response = submit(
            client,
            incident["incident_id"],
            feedback_type="OPERATOR_CORRECTION",
            root_cause_id=RC_KAFKA_LAG,
        )

    assert response.status_code == 422
    assert "corrected_root_cause" in str(response.json())


def test_a_blank_operator_is_422() -> None:
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        response = client.post(
            f"/incidents/{incident['incident_id']}/feedback",
            json={"feedback_type": "INCONCLUSIVE", "operator": "   "},
        )

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Memory OFF
# --------------------------------------------------------------------------
def test_a_memory_off_incident_records_the_outcome_without_writing_memory() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client, memory_mode="off")
        response = submit(
            client,
            incident["incident_id"],
            feedback_type="RESOLUTION_CONFIRMED",
            validated_fix="scaled the consumer group",
            validated_runbook_id=RB_LEDGER_CONSUMER_GROUP,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "RESOLVED"
    assert body["validated_runbook_id"] == RB_LEDGER_CONSUMER_GROUP

    assert store.retain_calls == 0
    assert store.events == []
    assert any(entry["event"] == "memory_off" for entry in body["timeline"])
    # And the incident is honest about the outcome not being remembered.
    assert any(
        "written to no memory" in entry["detail"]
        for entry in body["timeline"]
        if entry["event"] == "memory_off"
    )


# --------------------------------------------------------------------------
# Degraded retain
# --------------------------------------------------------------------------
def test_a_failed_retain_is_reported_as_failed_not_successful() -> None:
    store = memory_store(fail_retain=True)
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        body = submit(
            client,
            incident["incident_id"],
            feedback_type="DIAGNOSIS_CONFIRMED",
            root_cause_id=RC_KAFKA_LAG,
        ).json()

    # The operator outcome is still recorded on the incident.
    assert body["state"] == "RESOLVING"
    failed = [entry for entry in body["timeline"] if entry["event"] == "memory_retain_failed"]
    assert failed, "a failed retain must not be silent"
    assert "hindsight_unavailable" in failed[-1]["detail"]
    assert not any(entry["event"] == "memory_retained" for entry in body["timeline"])


# --------------------------------------------------------------------------
# Traces
# --------------------------------------------------------------------------
def test_feedback_traces_are_visible_on_the_incident() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        body = submit(
            client,
            incident["incident_id"],
            feedback_type="RESOLUTION_CONFIRMED",
            validated_fix="scaled the consumer group",
            validated_runbook_id=RB_LEDGER_CONSUMER_GROUP,
        ).json()
        traces = client.get(f"/incidents/{incident['incident_id']}/memory-trace").json()

    # The two feedback retains add two more traces to the incident.
    assert len(body["memory_trace_ids"]) == len(incident["memory_trace_ids"]) + 2
    assert {trace["trace_id"] for trace in traces["traces"]} == set(body["memory_trace_ids"])
    assert [trace["operation"] for trace in traces["traces"]].count("retain") == 3


def test_retain_traces_name_the_memory_they_wrote() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        body = submit(
            client,
            incident["incident_id"],
            feedback_type="DIAGNOSIS_CONFIRMED",
            root_cause_id=RC_KAFKA_LAG,
        ).json()

    retained = [entry for entry in body["timeline"] if entry["event"] == "memory_retained"]
    assert any("DIAGNOSIS (confirmed)" in entry["detail"] for entry in retained)


# --------------------------------------------------------------------------
# The full lifecycle
# --------------------------------------------------------------------------
def test_alert_to_resolution_is_a_complete_auditable_chain() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        incident = open_incident(client)
        assert incident["state"] == "WAITING_FOR_OPERATOR"

        diagnosed = submit(
            client,
            incident["incident_id"],
            feedback_type="DIAGNOSIS_CONFIRMED",
            root_cause_id=RC_KAFKA_LAG,
        ).json()
        assert diagnosed["state"] == "RESOLVING"

        resolved = submit(
            client,
            incident["incident_id"],
            feedback_type="RESOLUTION_CONFIRMED",
            validated_fix="Scaled the ledger consumer group and replayed the partition.",
            validated_runbook_id=RB_LEDGER_CONSUMER_GROUP,
        ).json()

    assert resolved["state"] == "RESOLVED"
    assert resolved["operator_outcome"] == "RESOLUTION_CONFIRMED"
    assert resolved["root_cause_id"] == RC_KAFKA_LAG
    assert resolved["validated_runbook_id"] == RB_LEDGER_CONSUMER_GROUP

    # Every category the operator authorised, and nothing they did not.
    assert [event.event_type for event in store.events] == [
        EventType.INCIDENT_OPEN,
        EventType.DIAGNOSIS,
        EventType.RESOLUTION,
        EventType.RUNBOOK_ENTRY,
    ]
    assert all(event.incident_id == resolved["incident_id"] for event in store.events[:3])
