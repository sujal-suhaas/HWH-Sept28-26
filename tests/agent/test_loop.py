"""Agent loop tests: happy path, repair, fallback, and every honest failure.

The LLM is scripted and the memory store is in-memory, so these run offline.
"""

from __future__ import annotations

import json

from src.agent.groq_client import AllModelsFailedError
from src.agent.loop import AgentLoop
from src.agent.trace import AgentStatus, ModelRole
from src.contracts import AlertPayload, IncidentType, Severity
from src.memory.disabled import DisabledMemoryStore
from tests.agent.conftest import seed_lag_incident
from tests.agent.fake_llm import (
    FALLBACK,
    PRIMARY,
    FakeLLM,
    call,
    tc,
    text_response,
    tool_response,
)


def alert() -> AlertPayload:
    return AlertPayload(
        service="checkout-api",
        severity=Severity.P1,
        incident_type=IncidentType.LATENCY,
        title="checkout-api p99 latency above 2s",
        summary="Checkout latency spiked after the payments-ledger deploy.",
        error_samples=["upstream timeout waiting for ledger confirmation"],
    )


def build_loop(memory, catalog, llm, max_steps: int = 6) -> AgentLoop:
    return AgentLoop(memory=memory, llm=llm, catalog=catalog, max_steps=max_steps)


SYMPTOM = "p99 latency spike after payments-ledger deploy"
SUSPECTED_CAUSE = "Kafka consumer lag on the payments topic"
CITED = ["INC-2201:RESOLUTION"]

HAPPY_SCRIPT = [
    tc("recall_similar_incidents", {"service": "checkout-api", "symptom": SYMPTOM}, "c1"),
    tc("lookup_runbook", {"suspected_cause": SUSPECTED_CAUSE, "service": "checkout-api"}, "c2"),
    tc(
        "propose_diagnosis",
        {
            "hypothesis": "Kafka consumer lag on payments-ledger after the deploy",
            "confidence": "high",
            "evidence_summary": "Matches a prior confirmed incident on the same service.",
            "cited_memory_ids": CITED,
        },
        "c3",
    ),
    tc(
        "propose_resolution",
        {
            "fix": "Scale the ledger consumer group and replay the affected partition",
            "evidence_summary": "Matches runbook RB-014.",
            "cited_memory_ids": CITED,
            "runbook_id": "RB-014",
        },
        "c4",
    ),
    text_response("Proposed Kafka consumer lag, fix RB-014. Confirm to resolve."),
]


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------
def test_full_run_produces_a_grounded_diagnosis_and_resolution(
    memory_with_history, catalog
) -> None:
    llm = FakeLLM(list(HAPPY_SCRIPT))
    run = build_loop(memory_with_history, catalog, llm).run(alert(), "INC-3001")

    assert run.status is AgentStatus.COMPLETED
    assert run.error is None
    assert run.model_used == PRIMARY
    assert run.used_fallback is False
    assert run.steps == 5

    assert run.diagnosis is not None
    assert run.diagnosis["status"] == "proposed"
    assert "consumer lag" in run.diagnosis["content"]

    assert run.resolution is not None
    assert run.resolution["status"] == "pending_confirmation"
    assert run.resolution["runbook_id"] == "RB-014"

    assert run.recalled_memory_ids == ["INC-2201:RESOLUTION"]
    assert run.memory_trace_ids
    assert run.final_text

    assert run.tool_names == [
        "recall_similar_incidents",
        "lookup_runbook",
        "propose_diagnosis",
        "propose_resolution",
    ]
    assert run.invalid_tool_calls == []
    assert all(record.executed for record in run.tool_calls)
    assert run.model_failures == []


def test_the_run_never_retains_to_memory(memory_with_history, catalog) -> None:
    """Running the agent must not create authoritative memory. Only an operator does."""
    llm = FakeLLM(list(HAPPY_SCRIPT))
    before = memory_with_history.retain_calls
    build_loop(memory_with_history, catalog, llm).run(alert(), "INC-3001")
    assert memory_with_history.retain_calls == before


def test_tool_results_are_fed_back_to_the_model(memory_with_history, catalog) -> None:
    llm = FakeLLM(list(HAPPY_SCRIPT))
    build_loop(memory_with_history, catalog, llm).run(alert(), "INC-3001")

    # Second model call must see the first tool's result.
    second_call = llm.messages_of(1)
    tool_messages = [message for message in second_call if message["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "c1"
    assert "INC-2201:RESOLUTION" in tool_messages[0]["content"]


def test_an_empty_recall_is_reported_to_the_model_honestly(memory, catalog) -> None:
    llm = FakeLLM(
        [
            tc(
                "recall_similar_incidents",
                {"service": "checkout-api", "symptom": "never seen before"},
                "c1",
            ),
            tool_response(
                call(
                    "propose_diagnosis",
                    {
                        "hypothesis": "unclear",
                        "confidence": "low",
                        "evidence_summary": "No matching history.",
                    },
                    "c2",
                )
            ),
            text_response("No precedent found."),
        ]
    )
    run = build_loop(memory, catalog, llm).run(alert(), "INC-3002")

    assert run.status is AgentStatus.COMPLETED
    assert run.recalled_memory_ids == []
    assert "No relevant historical incident found" in llm.messages_of(1)[-1]["content"]


def test_the_model_stopping_immediately_completes_the_run(memory, catalog) -> None:
    llm = FakeLLM([text_response("Nothing to investigate.")])
    run = build_loop(memory, catalog, llm).run(alert(), "INC-3003")

    assert run.status is AgentStatus.COMPLETED
    assert run.steps == 1
    assert run.tool_calls == []
    assert run.diagnosis is None
    assert run.final_text == "Nothing to investigate."


# --------------------------------------------------------------------------
# Memory OFF
# --------------------------------------------------------------------------
def test_memory_off_makes_no_memory_calls_and_forbids_citations(catalog) -> None:
    store = DisabledMemoryStore(bank_id="dejaops-test")
    llm = FakeLLM(
        [
            tc("recall_similar_incidents", {"service": "checkout-api", "symptom": "latency"}, "c1"),
            tool_response(
                call(
                    "propose_diagnosis",
                    {
                        "hypothesis": "possibly a deploy regression",
                        "confidence": "low",
                        "evidence_summary": "No historical context available.",
                    },
                    "c2",
                )
            ),
            text_response("Generic guidance only."),
        ]
    )
    run = build_loop(store, catalog, llm).run(alert(), "INC-3004")

    assert run.status is AgentStatus.COMPLETED
    assert run.memory_mode == "off"
    assert run.memory_trace_ids == []
    assert run.recalled_memory_ids == []
    assert "memory_mode=off" in llm.messages_of(1)[-1]["content"]
    # The system prompt tells the model memory is off.
    assert "MEMORY MODE: off" in llm.messages_of(0)[0]["content"]


def test_memory_off_rejects_a_citation_and_then_repairs_it(catalog) -> None:
    store = DisabledMemoryStore(bank_id="dejaops-test")
    llm = FakeLLM(
        [
            tool_response(
                call(
                    "propose_diagnosis",
                    {
                        "hypothesis": "made up",
                        "confidence": "high",
                        "evidence_summary": "cites nothing real",
                        "cited_memory_ids": ["INC-9999:RESOLUTION"],
                    },
                    "c1",
                )
            ),
            tool_response(
                call(
                    "propose_diagnosis",
                    {
                        "hypothesis": "unclear",
                        "confidence": "low",
                        "evidence_summary": "No memory consulted.",
                    },
                    "c2",
                )
            ),
            text_response("done"),
        ]
    )
    run = build_loop(store, catalog, llm).run(alert(), "INC-3005")

    assert run.status is AgentStatus.COMPLETED
    assert run.tool_calls[0].valid is False
    assert run.tool_calls[0].repair_attempted is True
    assert any("memory_mode is off" in error for error in run.tool_calls[0].validation_errors)
    assert run.tool_calls[1].valid is True
    assert run.diagnosis is not None
    assert run.diagnosis["cited_memory_ids"] == []


def test_memory_on_ignores_the_off_notice(memory, catalog) -> None:
    llm = FakeLLM([text_response()])
    build_loop(memory, catalog, llm).run(alert(), "INC-3006")
    assert "MEMORY MODE: off" not in llm.messages_of(0)[0]["content"]


# --------------------------------------------------------------------------
# Tool-call repair
# --------------------------------------------------------------------------
def test_a_malformed_call_is_repaired_once_then_executes(memory_with_history, catalog) -> None:
    llm = FakeLLM(
        [
            # Missing the required `symptom` field.
            tool_response(call("recall_similar_incidents", {"service": "checkout-api"}, "c1")),
            tool_response(
                call(
                    "recall_similar_incidents",
                    {"service": "checkout-api", "symptom": "latency after payments-ledger deploy"},
                    "c2",
                )
            ),
            text_response("Investigated."),
        ]
    )
    run = build_loop(memory_with_history, catalog, llm).run(alert(), "INC-3007")

    assert run.status is AgentStatus.COMPLETED
    assert len(run.tool_calls) == 2

    first, second = run.tool_calls
    assert first.valid is False
    assert first.executed is False
    assert first.repair_attempted is True
    assert any("symptom" in error for error in first.validation_errors)

    assert second.valid is True
    assert second.executed is True

    # The repair prompt must name the tool and the field.
    repair_message = llm.messages_of(1)[-1]["content"]
    assert "recall_similar_incidents" in repair_message
    assert "symptom" in repair_message


def test_a_call_that_is_still_malformed_after_repair_ends_the_run(memory, catalog) -> None:
    llm = FakeLLM(
        [
            tool_response(call("recall_similar_incidents", {"service": "checkout-api"}, "c1")),
            tool_response(call("recall_similar_incidents", {"symptom": "still no service"}, "c2")),
            # No third response: the loop must not ask again.
            text_response("should never be reached"),
        ]
    )
    run = build_loop(memory, catalog, llm).run(alert(), "INC-3008")

    assert run.status is AgentStatus.TOOL_CALL_INVALID
    assert run.error is not None
    assert "invalid after repair" in run.error
    assert llm.calls_made == 2
    assert all(not record.executed for record in run.tool_calls)
    assert run.diagnosis is None


def test_an_unknown_tool_name_is_rejected_then_repaired(memory, catalog) -> None:
    llm = FakeLLM(
        [
            tool_response(call("restart_everything", {"service": "checkout-api"}, "c1")),
            tool_response(call("get_service_map", {"service": "checkout-api"}, "c2")),
            text_response("done"),
        ]
    )
    run = build_loop(memory, catalog, llm).run(alert(), "INC-3009")

    assert run.status is AgentStatus.COMPLETED
    assert run.tool_calls[0].valid is False
    assert any("unknown tool" in error for error in run.tool_calls[0].validation_errors)
    assert run.tool_calls[1].executed is True


def test_non_json_arguments_are_rejected_then_repaired(memory, catalog) -> None:
    llm = FakeLLM(
        [
            tool_response(call("get_service_map", "{service: checkout-api", "c1")),
            tool_response(call("get_service_map", {"service": "checkout-api"}, "c2")),
            text_response("done"),
        ]
    )
    run = build_loop(memory, catalog, llm).run(alert(), "INC-3010")

    assert run.status is AgentStatus.COMPLETED
    assert any("not valid JSON" in error for error in run.tool_calls[0].validation_errors)


def test_a_fabricated_citation_is_rejected_and_never_reaches_the_proposal(
    memory_with_history, catalog
) -> None:
    llm = FakeLLM(
        [
            tc("recall_similar_incidents", {"service": "checkout-api", "symptom": SYMPTOM}, "c1"),
            tool_response(
                call(
                    "propose_diagnosis",
                    {
                        "hypothesis": "hallucinated cause",
                        "confidence": "high",
                        "evidence_summary": "cites a memory that does not exist",
                        "cited_memory_ids": ["INC-0000:RESOLUTION"],
                    },
                    "c2",
                )
            ),
            tool_response(
                call(
                    "propose_diagnosis",
                    {
                        "hypothesis": "Kafka consumer lag",
                        "confidence": "high",
                        "evidence_summary": "Grounded in the recalled resolution.",
                        "cited_memory_ids": ["INC-2201:RESOLUTION"],
                    },
                    "c3",
                )
            ),
            text_response("done"),
        ]
    )
    run = build_loop(memory_with_history, catalog, llm).run(alert(), "INC-3011")

    assert run.status is AgentStatus.COMPLETED
    assert any("not returned by any tool" in error for error in run.tool_calls[1].validation_errors)
    assert run.diagnosis is not None
    assert run.diagnosis["cited_memory_ids"] == ["INC-2201:RESOLUTION"]


def test_resolution_before_diagnosis_is_rejected_then_repaired(memory, catalog) -> None:
    llm = FakeLLM(
        [
            tc("propose_resolution", {"fix": "restart it", "evidence_summary": "vibes"}, "c1"),
            tc(
                "propose_diagnosis",
                {
                    "hypothesis": "unknown",
                    "confidence": "low",
                    "evidence_summary": "no evidence",
                },
                "c2",
            ),
            tc("propose_resolution", {"fix": "restart it", "evidence_summary": "none"}, "c3"),
            text_response("done"),
        ]
    )
    run = build_loop(memory, catalog, llm).run(alert(), "INC-3012")

    assert run.status is AgentStatus.COMPLETED
    assert any(
        "requires a diagnosis first" in error for error in run.tool_calls[0].validation_errors
    )
    assert run.tool_calls[0].executed is False
    assert run.resolution is not None
    assert run.resolution["status"] == "pending_confirmation"


def test_an_invented_runbook_id_is_rejected(memory, catalog) -> None:
    llm = FakeLLM(
        [
            tc(
                "propose_diagnosis",
                {"hypothesis": "lag", "confidence": "low", "evidence_summary": "e"},
                "c1",
            ),
            tool_response(
                call(
                    "propose_resolution",
                    {"fix": "scale consumers", "evidence_summary": "e", "runbook_id": "RB-9999"},
                    "c2",
                )
            ),
            tool_response(
                call(
                    "propose_resolution",
                    {"fix": "scale consumers", "evidence_summary": "e"},
                    "c3",
                )
            ),
            text_response("done"),
        ]
    )
    run = build_loop(memory, catalog, llm).run(alert(), "INC-3013")

    assert run.status is AgentStatus.COMPLETED
    assert any("RB-9999" in error for error in run.tool_calls[1].validation_errors)
    assert run.resolution is not None
    assert run.resolution["runbook_id"] is None


# --------------------------------------------------------------------------
# Protocol correctness
# --------------------------------------------------------------------------
def test_every_tool_call_in_a_batch_gets_a_tool_message(memory, catalog) -> None:
    """The provider rejects a request with an unanswered tool_call id."""
    llm = FakeLLM(
        [
            tool_response(
                call("get_service_map", {"service": "checkout-api"}, "c1"),
                call(
                    "recall_similar_incidents",
                    {"service": "checkout-api", "symptom": "x"},
                    "c2",
                ),
            ),
            text_response("done"),
        ]
    )
    build_loop(memory, catalog, llm).run(alert(), "INC-3014")

    second_call = llm.messages_of(1)
    assistant = next(m for m in second_call if m["role"] == "assistant")
    tool_ids = {m["tool_call_id"] for m in second_call if m["role"] == "tool"}
    assert {item["id"] for item in assistant["tool_calls"]} == {"c1", "c2"}
    assert tool_ids == {"c1", "c2"}


def test_a_valid_and_an_invalid_call_in_the_same_batch_are_both_answered(
    memory, catalog
) -> None:
    llm = FakeLLM(
        [
            tool_response(
                call("get_service_map", {"service": "checkout-api"}, "c1"),
                call("get_service_map", {"service": ["not", "a", "string"]}, "c2"),
            ),
            text_response("done"),
        ]
    )
    run = build_loop(memory, catalog, llm).run(alert(), "INC-3015")

    second_call = llm.messages_of(1)
    tool_ids = {m["tool_call_id"] for m in second_call if m["role"] == "tool"}
    assert tool_ids == {"c1", "c2"}
    assert run.tool_calls[0].executed is True
    assert run.tool_calls[1].valid is False


# --------------------------------------------------------------------------
# Bounds and failures
# --------------------------------------------------------------------------
def test_a_model_that_never_stops_hits_the_step_bound(memory, catalog) -> None:
    llm = FakeLLM(
        [
            tool_response(call("get_service_map", {}, f"c{index}"))
            for index in range(4)
        ]
    )
    run = build_loop(memory, catalog, llm, max_steps=3).run(alert(), "INC-3016")

    assert run.status is AgentStatus.MAX_STEPS
    assert run.steps == 3
    assert llm.calls_made == 3
    assert "did not stop within 3 steps" in run.error


def test_total_model_failure_ends_the_run_honestly(memory, catalog) -> None:
    llm = FakeLLM([AllModelsFailedError("all configured models failed", [])])
    run = build_loop(memory, catalog, llm).run(alert(), "INC-3017")

    assert run.status is AgentStatus.MODEL_FAILED
    assert "all configured models failed" in run.error
    assert run.diagnosis is None
    assert run.resolution is None
    assert run.model_failures


def test_a_fallback_model_is_recorded_on_the_run(memory, catalog) -> None:
    llm = FakeLLM(
        [text_response("from the fallback", model=FALLBACK, role=ModelRole.FALLBACK)]
    )
    run = build_loop(memory, catalog, llm).run(alert(), "INC-3018")

    assert run.status is AgentStatus.COMPLETED
    assert run.used_fallback is True
    assert run.model_used == FALLBACK


def test_the_system_prompt_is_identical_in_both_memory_modes(memory, catalog) -> None:
    """Only the memory-off notice may differ, or the ON/OFF comparison is not causal."""
    shared = alert()

    on_llm = FakeLLM([text_response()])
    build_loop(memory, catalog, on_llm).run(shared, "INC-3019")
    on_system = on_llm.messages_of(0)[0]["content"]

    off_llm = FakeLLM([text_response()])
    build_loop(DisabledMemoryStore(bank_id="x"), catalog, off_llm).run(shared, "INC-3020")
    off_system = off_llm.messages_of(0)[0]["content"]

    assert on_system in off_system
    assert off_system.startswith(on_system)


def test_the_same_alert_rendering_is_used_in_both_memory_modes(memory, catalog) -> None:
    shared = alert()

    on_llm = FakeLLM([text_response()])
    build_loop(memory, catalog, on_llm).run(shared, "INC-3021")

    off_llm = FakeLLM([text_response()])
    build_loop(DisabledMemoryStore(bank_id="x"), catalog, off_llm).run(shared, "INC-3022")

    assert on_llm.messages_of(0)[1] == off_llm.messages_of(0)[1]
    assert "fired at" in on_llm.messages_of(0)[1]["content"]


# --------------------------------------------------------------------------
# Trace shape
# --------------------------------------------------------------------------
def test_the_run_trace_records_latency_and_arguments(memory_with_history, catalog) -> None:
    llm = FakeLLM(list(HAPPY_SCRIPT))
    run = build_loop(memory_with_history, catalog, llm).run(alert(), "INC-3023")

    assert run.finished_at is not None
    assert run.latency_ms >= 0
    assert run.steps == 5
    assert len(run.model_calls) == 5

    recall_record = run.tool_calls[0]
    assert recall_record.arguments is not None
    assert recall_record.arguments["service"] == "checkout-api"
    assert recall_record.raw_arguments == json.dumps(
        {"service": "checkout-api", "symptom": "p99 latency spike after payments-ledger deploy"}
    )
    assert recall_record.result_summary
    assert recall_record.latency_ms >= 0


def test_model_call_summary_counts_failures_and_fallbacks(memory, catalog) -> None:
    llm = FakeLLM([text_response()])
    run = build_loop(memory, catalog, llm, max_steps=1).run(alert(), "INC-3024")
    summary = AgentLoop.model_call_summary(run.model_calls)
    assert summary == {"total": 1, "failed": 0, "fallback": 0}


def test_seeded_history_keeps_the_retain_count_stable_across_runs(
    memory_with_history, catalog
) -> None:
    seed_lag_incident(memory_with_history)
    before = len(memory_with_history.events)
    for index in range(3):
        build_loop(memory_with_history, catalog, FakeLLM(list(HAPPY_SCRIPT))).run(
            alert(), f"INC-40{index}"
        )
    assert len(memory_with_history.events) == before
