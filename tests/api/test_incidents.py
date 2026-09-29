"""Incident route tests: alerts, reads, chat, and the memory ON/OFF comparison."""

from __future__ import annotations

from src.agent.groq_client import AllModelsFailedError
from src.agent.prompts import render_memory_off_notice
from src.contracts import IncidentState
from src.memory import EventType, InMemoryMemoryStore
from tests.agent.fake_llm import FakeLLM, call, text_response, tool_response
from tests.api.helpers import (
    RB_LEDGER_CONSUMER_GROUP,
    alert_body,
    investigate_then_propose,
    make_client,
    memory_store,
    seed_confirmed_resolution,
)


# --------------------------------------------------------------------------
# Opening an incident
# --------------------------------------------------------------------------
def test_open_alert_creates_an_incident_and_runs_the_agent() -> None:
    store = memory_store()
    llm = FakeLLM(investigate_then_propose())
    with make_client(store=store, llm=llm) as client:
        response = client.post("/alerts", json=alert_body())

    assert response.status_code == 201
    incident = response.json()
    assert incident["incident_id"].startswith("INC-9")
    assert incident["state"] == "WAITING_FOR_OPERATOR"
    assert incident["alert"]["service"] == "checkout-api"
    assert incident["proposed_diagnosis"]["status"] == "proposed"
    assert incident["proposed_resolution"]["status"] == "pending_confirmation"
    assert incident["agent_status"] == "completed"
    assert incident["model_used"] == "openai/gpt-oss-120b"


def test_open_alert_retains_the_normalized_alert_automatically() -> None:
    store = memory_store()
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        client.post("/alerts", json=alert_body())

    incident_opens = [e for e in store.events if e.event_type is EventType.INCIDENT_OPEN]
    assert len(incident_opens) == 1
    assert "service:checkout-api" in incident_opens[0].tags()
    assert "checkout-api p99 latency above 2s" in incident_opens[0].content


def test_open_alert_records_the_timeline_of_what_happened() -> None:
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        incident = client.post("/alerts", json=alert_body()).json()

    events = [entry["event"] for entry in incident["timeline"]]
    assert events[0] == "alert_fired"
    assert "incident_opened" in events
    assert "memory_retained" in events
    assert "recall_similar_incidents" in events
    assert "propose_diagnosis" in events
    assert "agent_run_finished" in events


def test_open_alert_is_persisted_and_readable_afterwards() -> None:
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        created = client.post("/alerts", json=alert_body()).json()
        fetched = client.get(f"/incidents/{created['incident_id']}")
        listed = client.get("/incidents").json()

    assert fetched.status_code == 200
    assert fetched.json() == created
    assert listed["count"] == 1
    assert listed["incidents"][0]["incident_id"] == created["incident_id"]


def test_an_incident_survives_a_restart(tmp_path) -> None:
    """A new app over the same database still sees the incident."""
    db = str(tmp_path / "dejaops.db")
    with make_client(llm=FakeLLM(investigate_then_propose()), sqlite_path=db) as client:
        created = client.post("/alerts", json=alert_body()).json()

    with make_client(llm=FakeLLM([]), sqlite_path=db) as client:
        fetched = client.get(f"/incidents/{created['incident_id']}")

    assert fetched.status_code == 200
    fetched_diagnosis = fetched.json()["proposed_diagnosis"]["content"]
    assert fetched_diagnosis == created["proposed_diagnosis"]["content"]


def test_a_failed_agent_run_still_creates_the_incident_and_says_so() -> None:
    llm = FakeLLM([AllModelsFailedError("primary and fallback both failed")])
    with make_client(llm=llm) as client:
        response = client.post("/alerts", json=alert_body())

    # The alert is real, so the incident exists; the failure is reported.
    assert response.status_code == 201
    incident = response.json()
    assert incident["agent_status"] == "model_failed"
    assert incident["proposed_diagnosis"] is None
    assert incident["state"] == "DIAGNOSING"
    assert any(entry["event"] == "agent_run_failed" for entry in incident["timeline"])


# --------------------------------------------------------------------------
# Bad input
# --------------------------------------------------------------------------
def test_a_blank_alert_title_is_rejected_with_422() -> None:
    body = alert_body()
    body["alert"]["title"] = "   "  # type: ignore[index]
    with make_client(llm=FakeLLM([])) as client:
        response = client.post("/alerts", json=body)

    assert response.status_code == 422
    assert "title" in str(response.json())


def test_an_unknown_severity_is_rejected_with_422() -> None:
    body = alert_body(severity="p9")
    with make_client(llm=FakeLLM([])) as client:
        response = client.post("/alerts", json=body)

    assert response.status_code == 422
    assert "severity" in str(response.json())


def test_a_missing_alert_object_is_rejected_with_422() -> None:
    with make_client(llm=FakeLLM([])) as client:
        assert client.post("/alerts", json={}).status_code == 422


def test_unknown_incident_is_404() -> None:
    with make_client(llm=FakeLLM([])) as client:
        assert client.get("/incidents/INC-9999").status_code == 404
        assert client.get("/incidents/INC-9999/memory-trace").status_code == 404
        assert (
            client.post(
                "/incidents/INC-9999/feedback",
                json={"feedback_type": "INCONCLUSIVE", "operator": "m.iyer"},
            ).status_code
            == 404
        )


def test_the_agent_is_unavailable_without_an_api_key() -> None:
    store = memory_store()
    with make_client(store=store) as client:
        # make_client builds a real Groq client from a placeholder key; drop it to
        # model a deployment with no key configured.
        client.app.state.llm = None  # type: ignore[attr-defined]
        response = client.post("/alerts", json=alert_body())

    assert response.status_code == 503
    assert "GROQ_API_KEY" in response.json()["detail"]
    # No incident was half-created.
    assert store.retain_calls == 0


# --------------------------------------------------------------------------
# Grounding
# --------------------------------------------------------------------------
def test_a_cited_memory_id_must_have_been_recalled_in_this_run() -> None:
    store = memory_store()
    seed_confirmed_resolution(store)
    llm = FakeLLM(investigate_then_propose(cited="INC-2201:RESOLUTION"))
    with make_client(store=store, llm=llm) as client:
        incident = client.post("/alerts", json=alert_body()).json()

    assert incident["proposed_diagnosis"]["cited_memory_ids"] == ["INC-2201:RESOLUTION"]
    assert incident["agent_status"] == "completed"


def test_an_invented_citation_is_rejected_and_the_run_grounds_nothing() -> None:
    llm = FakeLLM(investigate_then_propose(cited="INC-9999:RESOLUTION"))
    with make_client(llm=llm) as client:
        incident = client.post("/alerts", json=alert_body()).json()

    # The model finished its turn, but the fabricated citation was refused, so
    # nothing was proposed. The refusal is visible rather than silent.
    assert incident["agent_status"] == "completed"
    assert incident["proposed_diagnosis"] is None
    assert incident["proposed_resolution"] is None
    # The run ended, so the incident waits on a human - not on a process that has
    # already stopped. See test_a_completed_run_that_proposed_nothing_does_not_wait_forever.
    assert incident["state"] == IncidentState.WAITING_FOR_OPERATOR.value
    assert any(
        "cited_memory_ids contains ids that were not returned" in entry["detail"]
        for entry in incident["timeline"]
    )
    assert any(
        "rejected" in entry["detail"]
        for entry in incident["timeline"]
        if entry["event"] == "agent_run_finished"
    )


def test_a_runbook_id_must_exist_in_the_catalog() -> None:
    store = memory_store()
    seed_confirmed_resolution(store)
    llm = FakeLLM(
        investigate_then_propose(cited="INC-2201:RESOLUTION", runbook_id="RB-999")
    )
    with make_client(store=store, llm=llm) as client:
        incident = client.post("/alerts", json=alert_body()).json()

    assert incident["proposed_resolution"] is None
    assert any(
        "does not exist in the catalog" in entry["detail"] for entry in incident["timeline"]
    )


def test_the_demo_flow_grounds_a_resolution_in_a_real_runbook() -> None:
    """Recall, look up the runbook, propose a diagnosis and a fix that cites both."""
    store = memory_store()
    memory_id = seed_confirmed_resolution(store)
    llm = FakeLLM(
        [
            tool_response(
                call(
                    "recall_similar_incidents",
                    {"service": "checkout-api", "symptom": "p99 latency after ledger deploy"},
                )
            ),
            tool_response(
                call(
                    "lookup_runbook",
                    {"suspected_cause": "kafka consumer lag", "service": "checkout-api"},
                    call_id="call_2",
                )
            ),
            tool_response(
                call(
                    "propose_diagnosis",
                    {
                        "hypothesis": "Kafka consumer lag on payments-ledger after the deploy.",
                        "confidence": "high",
                        "evidence_summary": "Matches a prior incident on this service.",
                        "cited_memory_ids": [memory_id],
                    },
                    call_id="call_3",
                )
            ),
            tool_response(
                call(
                    "propose_resolution",
                    {
                        "fix": "Scale the consumer group and replay the affected partition.",
                        "evidence_summary": "Validated runbook for this signature.",
                        "cited_memory_ids": [memory_id],
                        "runbook_id": RB_LEDGER_CONSUMER_GROUP,
                    },
                    call_id="call_4",
                )
            ),
            text_response("Diagnosis and fix proposed; awaiting operator confirmation."),
        ]
    )
    with make_client(store=store, llm=llm) as client:
        incident = client.post("/alerts", json=alert_body()).json()

    assert incident["proposed_resolution"]["runbook_id"] == RB_LEDGER_CONSUMER_GROUP
    assert incident["proposed_resolution"]["cited_memory_ids"] == [memory_id]
    assert incident["proposed_diagnosis"]["confidence"] == "high"


# --------------------------------------------------------------------------
# Memory ON / OFF
# --------------------------------------------------------------------------
def test_memory_off_makes_no_memory_calls_at_all() -> None:
    store = memory_store()
    llm = FakeLLM(investigate_then_propose())
    with make_client(store=store, llm=llm) as client:
        incident = client.post("/alerts", json=alert_body(memory_mode="off")).json()

    assert store.retain_calls == 0
    assert store.recall_calls == 0
    assert incident["memory_mode"] == "off"
    assert incident["memory_trace_ids"] == []
    assert any(entry["event"] == "memory_off" for entry in incident["timeline"])


def test_memory_off_rejects_a_citation_because_nothing_was_recalled() -> None:
    llm = FakeLLM(investigate_then_propose(cited="INC-2201:RESOLUTION"))
    with make_client(llm=llm) as client:
        incident = client.post("/alerts", json=alert_body(memory_mode="off")).json()

    assert incident["proposed_diagnosis"] is None
    assert any(
        "must be empty when memory_mode is off" in entry["detail"]
        for entry in incident["timeline"]
    )


def test_on_and_off_use_the_same_alert_and_differ_only_by_memory_context() -> None:
    """The §4 requirement: the comparison must be causally meaningful."""
    on_llm = FakeLLM(investigate_then_propose())
    off_llm = FakeLLM(investigate_then_propose())
    body = alert_body()

    with make_client(store=memory_store(), llm=on_llm) as client:
        client.post("/alerts", json={**body, "memory_mode": "on"})
    with make_client(store=memory_store(), llm=off_llm) as client:
        client.post("/alerts", json={**body, "memory_mode": "off"})

    on_messages, off_messages = on_llm.messages_of(0), off_llm.messages_of(0)
    on_system, off_system = on_messages[0]["content"], off_messages[0]["content"]

    # Same normalized alert, byte for byte.
    assert on_messages[1] == off_messages[1]
    # Same system prompt, plus the memory-off notice and nothing else.
    assert off_system.startswith(on_system)
    assert off_system[len(on_system) :].strip() == render_memory_off_notice().strip()
    # Same tools offered to the model.
    assert on_llm.seen_tools[0] == off_llm.seen_tools[0]


def test_a_memory_off_incident_keeps_its_mode_across_a_chat_turn() -> None:
    store = memory_store()
    llm = FakeLLM(
        investigate_then_propose() + [text_response("No precedent available for this run.")]
    )
    with make_client(store=store, llm=llm) as client:
        incident = client.post("/alerts", json=alert_body(memory_mode="off")).json()
        store.retain_calls = 0
        store.recall_calls = 0
        after = client.post(
            f"/chat/{incident['incident_id']}", json={"message": "any precedent?"}
        ).json()

    assert after["memory_mode"] == "off"
    assert store.recall_calls == 0
    assert store.retain_calls == 0


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------
def test_chat_appends_the_operator_message_and_an_agent_reply() -> None:
    llm = FakeLLM(investigate_then_propose() + [text_response("Yes: INC-2201, same signature.")])
    with make_client(llm=llm) as client:
        created = client.post("/alerts", json=alert_body()).json()
        after = client.post(
            f"/chat/{created['incident_id']}",
            json={"message": "Is this the ledger deploy again?", "operator": "m.iyer"},
        ).json()

    messages = [e for e in after["timeline"] if e["event"] == "chat_message"]
    replies = [e for e in after["timeline"] if e["event"] == "chat_reply"]
    assert messages[-1]["detail"] == "Is this the ledger deploy again?"
    assert messages[-1]["actor"] == "operator"
    assert replies[-1]["detail"] == "Yes: INC-2201, same signature."
    assert after["operator"] == "m.iyer"


def test_chat_passes_the_current_proposal_state_to_the_model() -> None:
    script = investigate_then_propose()
    llm = FakeLLM([*script, text_response("Still awaiting your call.")])
    with make_client(llm=llm) as client:
        created = client.post("/alerts", json=alert_body()).json()
        client.post(f"/chat/{created['incident_id']}", json={"message": "status?"})

    # The chat run's first model call comes after the alert run's scripted turns.
    follow_up = llm.messages_of(len(script))
    prompt = follow_up[-1]["content"]
    assert "Operator follow-up: status?" in prompt
    assert "unconfirmed" in prompt
    assert "Kafka consumer lag on payments-ledger" in prompt


def test_chat_reports_a_failed_run_instead_of_inventing_a_reply() -> None:
    llm = FakeLLM(
        investigate_then_propose() + [AllModelsFailedError("primary and fallback both failed")]
    )
    with make_client(llm=llm) as client:
        created = client.post("/alerts", json=alert_body()).json()
        after = client.post(f"/chat/{created['incident_id']}", json={"message": "hello?"}).json()

    reply = [e for e in after["timeline"] if e["event"] == "chat_reply"][-1]["detail"]
    assert "no reply" in reply
    assert "Nothing was concluded" in reply


def test_a_blank_chat_message_is_rejected_with_422() -> None:
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        created = client.post("/alerts", json=alert_body()).json()
        response = client.post(f"/chat/{created['incident_id']}", json={"message": "   "})

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Memory trace for one incident
# --------------------------------------------------------------------------
def test_memory_trace_returns_only_this_incidents_operations() -> None:
    store = memory_store()
    seed_confirmed_resolution(store)
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        created = client.post("/alerts", json=alert_body()).json()
        response = client.get(f"/incidents/{created['incident_id']}/memory-trace")

    assert response.status_code == 200
    body = response.json()
    assert body["incident_id"] == created["incident_id"]
    assert body["memory_mode"] == "on"
    assert body["count"] == len(body["traces"]) > 0
    trace_ids = {trace["trace_id"] for trace in body["traces"]}
    assert trace_ids == set(created["memory_trace_ids"])
    # Every trace belongs to this incident: the retain is here, the recall is here.
    assert {trace["operation"] for trace in body["traces"]} == {"retain", "recall"}


def test_memory_trace_is_empty_for_a_memory_off_incident() -> None:
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        created = client.post("/alerts", json=alert_body(memory_mode="off")).json()
        body = client.get(f"/incidents/{created['incident_id']}/memory-trace").json()

    assert body["memory_mode"] == "off"
    assert body["count"] == 0
    assert body["traces"] == []


def test_a_degraded_recall_is_visible_in_the_trace_not_hidden() -> None:
    store = InMemoryMemoryStore(bank_id="dejaops-test", fail_recall=True)
    with make_client(store=store, llm=FakeLLM(investigate_then_propose())) as client:
        created = client.post("/alerts", json=alert_body()).json()
        body = client.get(f"/incidents/{created['incident_id']}/memory-trace").json()

    recalls = [t for t in body["traces"] if t["operation"] == "recall"]
    assert recalls and all(not trace["success"] for trace in recalls)
    assert all(trace["degraded"] for trace in recalls)
    # And the incident says nothing was found, without claiming no precedent exists.
    assert created["state"] == IncidentState.WAITING_FOR_OPERATOR.value


# --- §13: a bad alert payload is a 4xx with a usable message ------------------


def test_a_wrong_typed_field_is_rejected_with_422() -> None:
    """`service` as a list is a client bug; say which field, don't 500."""
    body = alert_body()
    body["alert"]["service"] = ["checkout-api"]  # type: ignore[index]

    with make_client(llm=FakeLLM([])) as client:
        response = client.post("/alerts", json=body)

    assert response.status_code == 422
    assert "service" in str(response.json())


def test_a_wrong_typed_list_field_is_rejected_with_422() -> None:
    body = alert_body()
    body["alert"]["error_samples"] = "not-a-list"  # type: ignore[index]

    with make_client(llm=FakeLLM([])) as client:
        response = client.post("/alerts", json=body)

    assert response.status_code == 422
    assert "error_samples" in str(response.json())


def test_an_overlong_title_is_rejected_with_422() -> None:
    body = alert_body(title="x" * 301)

    with make_client(llm=FakeLLM([])) as client:
        response = client.post("/alerts", json=body)

    assert response.status_code == 422
    assert "title" in str(response.json())


def test_an_overlong_summary_is_rejected_with_422() -> None:
    body = alert_body(summary="x" * 2001)

    with make_client(llm=FakeLLM([])) as client:
        response = client.post("/alerts", json=body)

    assert response.status_code == 422
    assert "summary" in str(response.json())


def test_a_non_json_body_is_rejected_with_422() -> None:
    with make_client(llm=FakeLLM([])) as client:
        response = client.post(
            "/alerts", content=b"this is not json", headers={"content-type": "application/json"}
        )

    assert response.status_code == 422


def test_a_json_array_body_is_rejected_with_422() -> None:
    """A list is valid JSON but not a request body."""
    with make_client(llm=FakeLLM([])) as client:
        assert client.post("/alerts", json=[1, 2, 3]).status_code == 422


def test_a_bad_payload_creates_no_incident() -> None:
    """Validation must run before anything is persisted."""
    with make_client(llm=FakeLLM([])) as client:
        before = client.get("/incidents").json()
        client.post("/alerts", json={"alert": {"service": ""}})
        after = client.get("/incidents").json()

    assert before == after


def test_an_unparseable_timestamp_is_rejected_with_422() -> None:
    body = alert_body(fired_at="last tuesday")

    with make_client(llm=FakeLLM([])) as client:
        response = client.post("/alerts", json=body)

    assert response.status_code == 422
    assert "fired_at" in str(response.json())


def test_an_unknown_memory_mode_is_rejected_with_422() -> None:
    body = alert_body(memory_mode="maybe")

    with make_client(llm=FakeLLM([])) as client:
        response = client.post("/alerts", json=body)

    assert response.status_code == 422
    assert "memory_mode" in str(response.json())
