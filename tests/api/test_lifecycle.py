"""Lifecycle tests: state transitions and what becomes durable memory.

These are pure-function tests. No SQLite, no Hindsight, no LLM: the rule that
only an operator outcome creates authoritative memory is enforced here, so it
can be checked without any of the moving parts.
"""

from __future__ import annotations

import pytest

from src.agent.trace import AgentStatus
from src.api import lifecycle
from src.catalog import get_catalog
from src.contracts import (
    FeedbackRequest,
    FeedbackType,
    IncidentState,
    MemoryModeName,
    Proposal,
)
from src.memory import EventType, Outcome
from tests.api.helpers import (
    RB_LEDGER_CONSUMER_GROUP,
    RC_KAFKA_LAG,
    make_incident,
    make_run,
)

CATALOG = get_catalog()


#: Fields the contract requires for each feedback type. Filled in so tests can
#: focus on the behaviour under test rather than repeating valid payloads.
_REQUIRED_FIELDS: dict[FeedbackType, dict[str, object]] = {
    FeedbackType.DIAGNOSIS_CONFIRMED: {"root_cause_id": RC_KAFKA_LAG},
    FeedbackType.OPERATOR_CORRECTION: {"corrected_root_cause": "the actual cause"},
    FeedbackType.RESOLUTION_CONFIRMED: {"validated_fix": "scale it"},
}


def feedback(feedback_type: FeedbackType, **overrides: object) -> FeedbackRequest:
    payload: dict[str, object] = {
        "feedback_type": feedback_type,
        "operator": "m.iyer",
        **_REQUIRED_FIELDS.get(feedback_type, {}),
    }
    payload.update(overrides)
    return FeedbackRequest(**payload)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Opening
# --------------------------------------------------------------------------
def test_new_incident_starts_open_with_no_proposal_and_no_outcome() -> None:
    incident = lifecycle.new_incident("INC-9001", make_incident().alert, MemoryModeName.ON)

    assert incident.state is IncidentState.OPEN
    assert incident.proposed_diagnosis is None
    assert incident.proposed_resolution is None
    assert incident.operator_outcome is None
    assert incident.memory_trace_ids == []


def test_incident_open_event_is_tagged_for_recall_and_carries_provenance() -> None:
    incident = make_incident()
    event = lifecycle.incident_open_event(incident)

    assert event.event_type is EventType.INCIDENT_OPEN
    assert "service:checkout-api" in event.tags()
    assert "event_type:incident_open" in event.tags()
    assert event.metadata()["incident_id"] == "INC-9001"
    # Metadata is context, never a filter: the two key sets stay disjoint.
    assert not set(event.metadata()) & set(event.tags())


# --------------------------------------------------------------------------
# Applying an agent run
# --------------------------------------------------------------------------
def test_apply_agent_run_records_the_proposal_and_waits_for_the_operator() -> None:
    incident = make_incident(proposed_diagnosis=None, state=IncidentState.DIAGNOSING)
    updated = lifecycle.apply_agent_run(incident, make_run())

    assert updated.state is IncidentState.WAITING_FOR_OPERATOR
    assert updated.proposed_diagnosis is not None
    assert updated.proposed_diagnosis.status == "proposed"
    assert updated.model_used == "openai/gpt-oss-120b"
    assert updated.agent_status == "completed"
    assert updated.memory_trace_ids == ["trace-1", "trace-2"]
    assert updated.tool_trace_ids == ["call_1", "call_2"]


def test_a_run_that_proposes_nothing_does_not_erase_an_existing_proposal() -> None:
    incident = make_incident()
    updated = lifecycle.apply_agent_run(
        incident, make_run(diagnosis=None, tool_calls=[], memory_trace_ids=[])
    )

    assert updated.proposed_diagnosis == incident.proposed_diagnosis
    assert updated.state is IncidentState.WAITING_FOR_OPERATOR


def test_a_failed_run_does_not_advance_the_incident() -> None:
    incident = make_incident(state=IncidentState.DIAGNOSING, proposed_diagnosis=None)
    updated = lifecycle.apply_agent_run(
        incident,
        make_run(
            status=AgentStatus.MODEL_FAILED,
            diagnosis=None,
            error="all models failed",
            tool_calls=[],
        ),
    )

    assert updated.state is IncidentState.DIAGNOSING
    assert updated.agent_status == "model_failed"
    assert any(entry.event == "agent_run_failed" for entry in updated.timeline)


def test_tool_results_are_recorded_in_the_timeline_not_model_reasoning() -> None:
    updated = lifecycle.apply_agent_run(
        make_incident(state=IncidentState.DIAGNOSING, proposed_diagnosis=None), make_run()
    )
    events = [entry.event for entry in updated.timeline]

    assert "recall_similar_incidents" in events
    assert "propose_diagnosis" in events
    assert "agent_run_finished" in events
    # No raw model text is stored on the incident by the alert flow.
    assert "final_text" not in events


def test_tool_detail_is_bounded() -> None:
    from src.agent.trace import ToolCall

    long_summary = "x" * 5000
    updated = lifecycle.apply_agent_run(
        make_incident(state=IncidentState.DIAGNOSING, proposed_diagnosis=None),
        make_run(
            diagnosis=None,
            tool_calls=[
                ToolCall(
                    call_id="c1",
                    name="recall_similar_incidents",
                    raw_arguments="{}",
                    valid=True,
                    executed=True,
                    result_summary=long_summary,
                )
            ],
        ),
    )
    detail = next(e.detail for e in updated.timeline if e.event == "recall_similar_incidents")
    assert len(detail) <= lifecycle.MAX_TOOL_DETAIL_CHARS + 1


# --------------------------------------------------------------------------
# Validating feedback
# --------------------------------------------------------------------------
def test_unknown_catalog_ids_are_rejected() -> None:
    errors = lifecycle.validate_feedback(
        make_incident(),
        feedback(FeedbackType.DIAGNOSIS_CONFIRMED, root_cause_id="RC-999"),
        CATALOG,
    )
    assert any("RC-999" in error for error in errors)


def test_unknown_runbook_id_is_rejected() -> None:
    errors = lifecycle.validate_feedback(
        make_incident(),
        feedback(
            FeedbackType.RESOLUTION_CONFIRMED,
            validated_fix="scale it",
            validated_runbook_id="RB-999",
        ),
        CATALOG,
    )
    assert any("RB-999" in error for error in errors)


def test_a_resolved_incident_accepts_no_further_outcome() -> None:
    errors = lifecycle.validate_feedback(
        make_incident(state=IncidentState.RESOLVED),
        feedback(FeedbackType.INCONCLUSIVE),
        CATALOG,
    )
    assert any("already RESOLVED" in error for error in errors)


def test_valid_feedback_produces_no_errors() -> None:
    assert (
        lifecycle.validate_feedback(
            make_incident(),
            feedback(FeedbackType.DIAGNOSIS_CONFIRMED, root_cause_id=RC_KAFKA_LAG),
            CATALOG,
        )
        == []
    )


# --------------------------------------------------------------------------
# What becomes durable memory
# --------------------------------------------------------------------------
def test_confirmed_diagnosis_becomes_a_confirmed_diagnosis_memory() -> None:
    events = lifecycle.feedback_events(
        make_incident(),
        feedback(FeedbackType.DIAGNOSIS_CONFIRMED, root_cause_id=RC_KAFKA_LAG),
        CATALOG,
    )

    assert [event.event_type for event in events] == [EventType.DIAGNOSIS]
    assert events[0].outcome is Outcome.CONFIRMED
    assert RC_KAFKA_LAG in events[0].content
    assert "service:checkout-api" in events[0].tags()


def test_rejected_diagnosis_is_retained_as_rejected_and_never_as_confirmed() -> None:
    events = lifecycle.feedback_events(
        make_incident(), feedback(FeedbackType.DIAGNOSIS_REJECTED), CATALOG
    )

    assert len(events) == 1
    assert events[0].outcome is Outcome.REJECTED
    assert "rejected by the operator" in events[0].content
    # A rejected hypothesis must not become a confirmed root cause anywhere.
    assert not any(event.outcome is Outcome.CONFIRMED for event in events)


def test_inconclusive_is_recorded_as_inconclusive() -> None:
    events = lifecycle.feedback_events(
        make_incident(), feedback(FeedbackType.INCONCLUSIVE), CATALOG
    )
    assert [event.outcome for event in events] == [Outcome.INCONCLUSIVE]


def test_operator_correction_overrides_the_agent_hypothesis() -> None:
    events = lifecycle.feedback_events(
        make_incident(),
        feedback(
            FeedbackType.OPERATOR_CORRECTION,
            corrected_root_cause="The real cause was a Redis failover flap, not Kafka lag.",
            root_cause_id="RC-002",
        ),
        CATALOG,
    )

    assert [event.event_type for event in events] == [EventType.OPERATOR_CORRECTION]
    assert "the agent was wrong" in events[0].content
    assert "Redis failover flap" in events[0].content
    # The agent's own hypothesis is kept for contrast, not as the cause.
    assert "Kafka consumer lag" in events[0].content


def test_confirmed_resolution_promotes_the_runbook_for_this_service() -> None:
    events = lifecycle.feedback_events(
        make_incident(),
        feedback(
            FeedbackType.RESOLUTION_CONFIRMED,
            validated_fix="Scale the consumer group and replay the partition.",
            validated_runbook_id=RB_LEDGER_CONSUMER_GROUP,
        ),
        CATALOG,
    )

    assert [event.event_type for event in events] == [
        EventType.RESOLUTION,
        EventType.RUNBOOK_ENTRY,
    ]
    resolution, runbook = events
    assert resolution.outcome is Outcome.CONFIRMED
    assert resolution.runbook_id == RB_LEDGER_CONSUMER_GROUP
    assert "Time to resolve:" in resolution.content

    # Scoped per (runbook, service), so a lookup for checkout-api finds it and
    # re-promoting an already-seeded pair rewrites the same document.
    assert "service:checkout-api" in runbook.tags()
    assert "event_type:runbook_entry" in runbook.tags()
    assert runbook.document_id == f"RUNBOOK-{RB_LEDGER_CONSUMER_GROUP}:checkout-api:runbook_entry"
    # Never tagged by incident type: runbook lookup would then match nothing.
    assert not any(tag.startswith("incident_type:") for tag in runbook.tags())


def test_confirmed_resolution_without_a_runbook_promotes_nothing() -> None:
    events = lifecycle.feedback_events(
        make_incident(),
        feedback(FeedbackType.RESOLUTION_CONFIRMED, validated_fix="Restarted the consumer."),
        CATALOG,
    )
    assert [event.event_type for event in events] == [EventType.RESOLUTION]


def test_failed_resolution_is_recorded_as_failed_and_never_as_a_runbook() -> None:
    proposed = Proposal(
        kind="resolution",
        status="pending_confirmation",
        content="Restarted the ledger consumer group.",
        evidence_summary="Matches a validated fix.",
        runbook_id=RB_LEDGER_CONSUMER_GROUP,
    )
    events = lifecycle.feedback_events(
        make_incident(proposed_resolution=proposed),
        feedback(FeedbackType.RESOLUTION_FAILED),
        CATALOG,
    )

    assert [event.event_type for event in events] == [EventType.RESOLUTION]
    assert events[0].outcome is Outcome.FAILED
    # Even though the proposal named a runbook, a failed fix is not a runbook.
    assert events[0].runbook_id is None
    assert "NOT verified by the operator" in events[0].content
    assert "Restarted the ledger consumer group." in events[0].content
    assert EventType.RUNBOOK_ENTRY not in {event.event_type for event in events}


def test_failed_resolution_falls_back_to_the_operator_note() -> None:
    events = lifecycle.feedback_events(
        make_incident(proposed_resolution=None),
        feedback(FeedbackType.RESOLUTION_FAILED, note="restarting the consumer did not help"),
        CATALOG,
    )
    assert [event.outcome for event in events] == [Outcome.FAILED]
    assert "restarting the consumer did not help" in events[0].content


def test_failed_resolution_with_nothing_to_describe_retains_nothing() -> None:
    events = lifecycle.feedback_events(
        make_incident(proposed_resolution=None),
        feedback(FeedbackType.RESOLUTION_FAILED),
        CATALOG,
    )
    assert events == []


def test_a_memory_off_incident_writes_no_memory_at_all() -> None:
    incident = make_incident(memory_mode=MemoryModeName.OFF)
    for feedback_type in FeedbackType:
        assert lifecycle.feedback_events(incident, feedback(feedback_type), CATALOG) == []


# --------------------------------------------------------------------------
# Applying feedback
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("feedback_type", "expected"),
    [
        (FeedbackType.DIAGNOSIS_CONFIRMED, IncidentState.RESOLVING),
        (FeedbackType.DIAGNOSIS_REJECTED, IncidentState.DIAGNOSING),
        (FeedbackType.OPERATOR_CORRECTION, IncidentState.RESOLVING),
        (FeedbackType.RESOLUTION_CONFIRMED, IncidentState.RESOLVED),
        (FeedbackType.RESOLUTION_FAILED, IncidentState.RESOLVING),
        (FeedbackType.INCONCLUSIVE, IncidentState.INCONCLUSIVE),
    ],
)
def test_each_outcome_lands_in_the_expected_state(
    feedback_type: FeedbackType, expected: IncidentState
) -> None:
    updated = lifecycle.apply_feedback(make_incident(), feedback(feedback_type))
    assert updated.state is expected
    assert updated.operator_outcome is feedback_type
    assert updated.operator == "m.iyer"


def test_apply_feedback_records_the_outcome_in_the_timeline() -> None:
    updated = lifecycle.apply_feedback(
        make_incident(),
        feedback(FeedbackType.DIAGNOSIS_CONFIRMED, root_cause_id=RC_KAFKA_LAG, note="verified"),
    )
    entry = updated.timeline[-1]
    assert entry.actor == "operator"
    assert entry.event == FeedbackType.DIAGNOSIS_CONFIRMED.value
    assert "verified" in entry.detail


def test_apply_feedback_says_so_when_memory_is_off() -> None:
    updated = lifecycle.apply_feedback(
        make_incident(memory_mode=MemoryModeName.OFF),
        feedback(FeedbackType.DIAGNOSIS_CONFIRMED, root_cause_id=RC_KAFKA_LAG),
    )
    assert any(entry.event == "memory_off" for entry in updated.timeline)


def test_apply_feedback_does_not_mutate_the_input() -> None:
    incident = make_incident()
    lifecycle.apply_feedback(incident, feedback(FeedbackType.INCONCLUSIVE))
    assert incident.state is IncidentState.WAITING_FOR_OPERATOR


# --------------------------------------------------------------------------
# Chat prompt
# --------------------------------------------------------------------------
def test_chat_prompt_includes_the_unconfirmed_proposal_state() -> None:
    prompt = lifecycle.chat_prompt(make_incident(), "Is this the ledger deploy again?")

    assert "Is this the ledger deploy again?" in prompt
    assert "unconfirmed" in prompt
    assert "Kafka consumer lag on payments-ledger." in prompt
    assert "no resolution proposed yet" in prompt


def test_chat_prompt_reports_when_nothing_was_proposed() -> None:
    prompt = lifecycle.chat_prompt(
        make_incident(proposed_diagnosis=None), "what do you think?"
    )
    assert "no diagnosis proposed yet" in prompt


def test_apply_agent_run_accepts_a_proposal_from_a_later_chat_turn() -> None:
    incident = make_incident(proposed_resolution=None)
    resolution = Proposal(
        kind="resolution",
        status="pending_confirmation",
        content="Scale the consumer group.",
        evidence_summary="Matches a validated fix.",
        runbook_id=RB_LEDGER_CONSUMER_GROUP,
    )
    updated = lifecycle.apply_agent_run(
        incident, make_run(resolution=resolution.model_dump(mode="json"))
    )

    assert updated.proposed_resolution is not None
    assert updated.proposed_resolution.status == "pending_confirmation"
    assert updated.proposed_resolution.runbook_id == RB_LEDGER_CONSUMER_GROUP


def test_a_completed_run_that_proposed_nothing_does_not_wait_forever() -> None:
    """A finished run must not leave the incident looking busy.

    Found on a live run: the agent recalled the right runbook and then stopped
    without proposing. `agent_status` read `completed` while the state read
    `DIAGNOSING`, so the incident sat there waiting on a process that had already
    stopped - and nothing ever moved it. The agent's turn is over either way, so
    the next move is the operator's.
    """
    incident = make_incident(state=IncidentState.DIAGNOSING, proposed_diagnosis=None)

    updated = lifecycle.apply_agent_run(
        incident,
        make_run(diagnosis=None, resolution=None, tool_calls=[], memory_trace_ids=[]),
    )

    assert updated.agent_status == "completed"
    assert updated.proposed_diagnosis is None
    assert updated.state is IncidentState.WAITING_FOR_OPERATOR


def test_a_completed_run_with_nothing_to_propose_still_says_so() -> None:
    """The state moves, but the timeline must not imply the agent concluded."""
    incident = make_incident(state=IncidentState.OPEN, proposed_diagnosis=None)

    updated = lifecycle.apply_agent_run(
        incident,
        make_run(diagnosis=None, resolution=None, tool_calls=[], memory_trace_ids=[]),
    )

    finished = [e for e in updated.timeline if e.event == "agent_run_finished"]
    assert finished and "0 cited" in finished[0].detail


def test_a_completed_run_does_not_move_an_incident_that_is_already_resolving() -> None:
    """Only the OPEN/DIAGNOSING states are the agent's to end."""
    incident = make_incident(state=IncidentState.RESOLVING)

    updated = lifecycle.apply_agent_run(incident, make_run())

    assert updated.state is IncidentState.RESOLVING


def test_a_failed_run_leaves_the_state_alone() -> None:
    """A failed run is not a finished turn, so it must not look like one."""
    incident = make_incident(state=IncidentState.DIAGNOSING, proposed_diagnosis=None)

    updated = lifecycle.apply_agent_run(
        incident,
        make_run(status=AgentStatus.MODEL_FAILED, diagnosis=None, error="boom"),
    )

    assert updated.state is IncidentState.DIAGNOSING
